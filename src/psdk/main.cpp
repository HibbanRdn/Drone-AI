#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <ctime>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <utility>
#include <vector>

#include <dji_aircraft_info.h>
#include <dji_core.h>
#include <dji_fc_subscription.h>
#include <dji_high_speed_data_channel.h>
#include <dji_liveview.h>
#include <dji_logger.h>
#include <dji_payload_camera.h>
#include <dji_platform.h>
#include <dji_widget.h>
#include <dji_version.h>

#include "dji_sdk_config.h"
#include "osal/osal.h"
#include "osal/osal_fs.h"
#include "osal/osal_socket.h"
#include "hal_usb_bulk.h"

#ifdef GAP_PLOT_AI_COMPILED_APP_INFO
#include "dji_sdk_app_info.generated.h"
#endif

namespace {

constexpr char kFrameMagic[8] = {'G', 'P', 'A', 'I', 'F', 'R', 'M', '2'};
constexpr uint16_t kFrameHeaderVersion = 2;
constexpr uint32_t kTelemetryLatitude = 1U << 0;
constexpr uint32_t kTelemetryLongitude = 1U << 1;
constexpr uint32_t kTelemetryRelativeAltitude = 1U << 2;
constexpr uint32_t kTelemetryAbsoluteAltitude = 1U << 3;
constexpr uint32_t kTelemetryGimbalPitch = 1U << 4;
constexpr uint32_t kTelemetryHeading = 1U << 5;
constexpr uint32_t kTelemetryRtk = 1U << 6;
constexpr uint32_t kTelemetryVelocity = 1U << 7;
constexpr uint32_t kTelemetryGpsQuality = 1U << 8;
constexpr std::size_t kMaxPilotBoxes = std::numeric_limits<uint8_t>::max();
constexpr std::size_t kMaxContours = 64;
constexpr std::size_t kMaxContourPoints = 128;
constexpr uint64_t kDefaultStaleResultTimeoutNs = 1'500'000'000ULL;

static_assert(DJI_VERSION_MAJOR == 3 && DJI_VERSION_MINOR == 16 &&
                  DJI_VERSION_MODIFY == 0,
              "gap_plot_ai requires DJI Payload SDK 3.16.0");
static_assert(CONFIG_HARDWARE_CONNECTION == DJI_USE_ONLY_USB_BULK_DEVICE,
              "Manifold 3 must use the official USB bulk connection mode");

#pragma pack(push, 1)
struct FrameHeader {
    char magic[8];
    uint16_t headerVersion;
    uint16_t pixelFormat;
    uint64_t frameSequence;
    uint32_t sourceFrameId;
    uint64_t captureMonotonicNs;
    uint64_t captureWallClockNs;
    uint64_t telemetryMonotonicNs;
    uint32_t width;
    uint32_t height;
    uint32_t rowStride;
    uint32_t channels;
    uint32_t dataLength;
    double latitude;
    double longitude;
    double relativeAltitude;
    double absoluteAltitude;
    double aircraftRoll;
    double aircraftPitch;
    double aircraftYaw;
    double gimbalRoll;
    double gimbalPitch;
    double gimbalYaw;
    double velocityX;
    double velocityY;
    double velocityZ;
    double speedMps;
    int32_t rtkStatus;
    int32_t gpsSignalLevel;
    uint32_t visibleSatellites;
    uint32_t telemetryValidMask;
};
#pragma pack(pop)

static_assert(sizeof(FrameHeader) == 196, "FrameHeader v2 harus sama dengan worker Python");

struct FramePacket {
    uint64_t frameSequence = 0;
    uint32_t sourceFrameId = 0;
    uint64_t captureMonotonicNs = 0;
    uint64_t captureWallClockNs = 0;
    uint16_t pixelFormat = 0;
    uint16_t width = 0;
    uint16_t height = 0;
    uint32_t rowStride = 0;
    T_DjiLiveviewImageInfo imageInfo = {};
    std::vector<uint8_t> rgb;
};

struct NormalizedBox {
    uint16_t id = 0;
    uint8_t classId = 0;
    float confidence = 0;
    float x1 = 0;
    float y1 = 0;
    float x2 = 0;
    float y2 = 0;
};

struct NormalizedPoint {
    float x = 0;
    float y = 0;
};

struct NormalizedContour {
    std::vector<NormalizedPoint> points;
};

struct RenderResult {
    uint64_t frameIndex = 0;
    uint64_t captureMonotonicNs = 0;
    uint64_t generatedMonotonicNs = 0;
    std::string status = "IDLE";
    double latencyMs = 0;
    double fps = 0;
    double p50LatencyMs = 0;
    int32_t gapCount = -1;
    uint32_t totalDetections = 0;
    uint32_t overlayDetections = 0;
    uint32_t sourceWidth = 0;
    uint32_t sourceHeight = 0;
    uint32_t rowStride = 0;
    int32_t rtkStatus = -1;
    int32_t gpsSignalLevel = -1;
    int32_t modelLoadCount = 0;
    int32_t warmupCount = 0;
    int32_t backendInitializationCount = 0;
    std::string sessionId;
    std::string lastError;
    std::vector<NormalizedBox> boxes;
    std::vector<NormalizedContour> contours;
};

struct Controls {
    bool running = false;
    bool plant = true;
    bool segmenter = false;
    uint32_t snapshotSequence = 0;
};

std::atomic<bool> g_stop{false};
std::atomic<uint64_t> g_lastFrameArrivalNs{0};
std::atomic<uint64_t> g_frameSequence{0};
std::atomic<uint64_t> g_sourceFrameCount{0};
std::mutex g_frameMutex;
std::condition_variable g_frameCondition;
FramePacket g_latestFrame;
bool g_framePending = false;
std::atomic<uint64_t> g_droppedFrames{0};
std::atomic<bool> g_overlayMetadataAvailable{true};
std::atomic<uint64_t> g_overlayMetadataErrorCode{0};
std::mutex g_resultMutex;
RenderResult g_renderResult;
std::mutex g_controlMutex;
Controls g_controls;
std::array<int32_t, 6> g_widgetValues{{0, 0, 1, 0, 0, 0}};
std::atomic<int32_t> g_statusValue{0};
std::string g_ipcDirectory;
bool g_fcSubscriptionInitialized = false;
bool g_liveviewInitialized = false;
bool g_imageStreamStarted = false;
bool g_encoderRegistered = false;
bool g_renderedStreamEnabled = false;
bool g_staticOverlayDebug = false;
uint64_t g_staleResultTimeoutNs = kDefaultStaleResultTimeoutNs;

uint64_t RealtimeNs()
{
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count());
}

uint64_t MonotonicNs()
{
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch())
            .count());
}

float Clamp01(float value)
{
    if (!std::isfinite(value)) {
        return 0;
    }
    return std::max(0.0F, std::min(1.0F, value));
}

bool EnvironmentFlag(const char *name, bool defaultValue = false)
{
    const char *value = std::getenv(name);
    if (value == nullptr || value[0] == '\0') {
        return defaultValue;
    }
    return std::strcmp(value, "1") == 0 || std::strcmp(value, "true") == 0 ||
           std::strcmp(value, "TRUE") == 0;
}

uint64_t EnvironmentMilliseconds(const char *name, uint64_t defaultValue)
{
    const char *value = std::getenv(name);
    if (value == nullptr || value[0] == '\0') {
        return defaultValue;
    }
    char *end = nullptr;
    const unsigned long parsed = std::strtoul(value, &end, 10);
    if (end == value || *end != '\0' || parsed < 100) {
        return defaultValue;
    }
    return static_cast<uint64_t>(parsed);
}

void SignalHandler(int)
{
    g_stop.store(true);
    g_frameCondition.notify_all();
}

void EnsureRuntimeDirectories()
{
    const char *appRootValue = std::getenv("GAP_PLOT_AI_APP_ROOT");
    const std::filesystem::path appRoot =
        appRootValue != nullptr && appRootValue[0] != '\0'
            ? std::filesystem::path(appRootValue)
            : std::filesystem::current_path();

    std::error_code error;
    std::filesystem::create_directories(appRoot / "data/logs", error);
    if (error) {
        throw std::runtime_error("Tidak dapat membuat direktori data/logs: " +
                                 error.message());
    }
    error.clear();
    std::filesystem::create_directories(g_ipcDirectory, error);
    if (error) {
        throw std::runtime_error("Tidak dapat membuat direktori IPC: " +
                                 error.message());
    }
}

T_DjiReturnCode ConsoleOutput(const uint8_t *data, uint16_t dataLength)
{
    if (data != nullptr && dataLength > 0) {
        std::fwrite(data, 1, dataLength, stdout);
        std::fflush(stdout);
    }
    return DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS;
}

void SetupPlatform()
{
    T_DjiOsalHandler osal = {};
    osal.TaskCreate = Osal_TaskCreate;
    osal.TaskDestroy = Osal_TaskDestroy;
    osal.TaskSleepMs = Osal_TaskSleepMs;
    osal.MutexCreate = Osal_MutexCreate;
    osal.MutexDestroy = Osal_MutexDestroy;
    osal.MutexLock = Osal_MutexLock;
    osal.MutexUnlock = Osal_MutexUnlock;
    osal.SemaphoreCreate = Osal_SemaphoreCreate;
    osal.SemaphoreDestroy = Osal_SemaphoreDestroy;
    osal.SemaphoreWait = Osal_SemaphoreWait;
    osal.SemaphoreTimedWait = Osal_SemaphoreTimedWait;
    osal.SemaphorePost = Osal_SemaphorePost;
    osal.Malloc = Osal_Malloc;
    osal.Free = Osal_Free;
    osal.GetTimeMs = Osal_GetTimeMs;
    osal.GetTimeUs = Osal_GetTimeUs;
    osal.GetRandomNum = Osal_GetRandomNum;

    T_DjiHalUsbBulkHandler usb = {};
    usb.UsbBulkInit = HalUsbBulk_Init;
    usb.UsbBulkDeInit = HalUsbBulk_DeInit;
    usb.UsbBulkWriteData = HalUsbBulk_WriteData;
    usb.UsbBulkReadData = HalUsbBulk_ReadData;
    usb.UsbBulkGetDeviceInfo = HalUsbBulk_GetDeviceInfo;

    T_DjiSocketHandler socket = {};
    socket.Socket = Osal_Socket;
    socket.Bind = Osal_Bind;
    socket.Close = Osal_Close;
    socket.UdpSendData = Osal_UdpSendData;
    socket.UdpRecvData = Osal_UdpRecvData;
    socket.TcpListen = Osal_TcpListen;
    socket.TcpAccept = Osal_TcpAccept;
    socket.TcpConnect = Osal_TcpConnect;
    socket.TcpSendData = Osal_TcpSendData;
    socket.TcpRecvData = Osal_TcpRecvData;

    T_DjiFileSystemHandler fileSystem = {};
    fileSystem.FileOpen = Osal_FileOpen;
    fileSystem.FileClose = Osal_FileClose;
    fileSystem.FileWrite = Osal_FileWrite;
    fileSystem.FileRead = Osal_FileRead;
    fileSystem.FileSync = Osal_FileSync;
    fileSystem.FileSeek = Osal_FileSeek;
    fileSystem.DirOpen = Osal_DirOpen;
    fileSystem.DirClose = Osal_DirClose;
    fileSystem.DirRead = Osal_DirRead;
    fileSystem.Mkdir = Osal_Mkdir;
    fileSystem.Unlink = Osal_Unlink;
    fileSystem.Rename = Osal_Rename;
    fileSystem.Stat = Osal_Stat;

    auto RequireSuccess = [](T_DjiReturnCode code, const char *operation) {
        if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
            std::ostringstream message;
            message << operation << " gagal, code=0x" << std::hex << code;
            throw std::runtime_error(message.str());
        }
    };
    RequireSuccess(DjiPlatform_RegOsalHandler(&osal), "register OSAL");
    RequireSuccess(DjiPlatform_RegHalUsbBulkHandler(&usb), "register USB bulk");
    RequireSuccess(DjiPlatform_RegSocketHandler(&socket), "register socket");
    RequireSuccess(DjiPlatform_RegFileSystemHandler(&fileSystem), "register filesystem");

    T_DjiLoggerConsole console = {};
    console.func = ConsoleOutput;
    console.consoleLevel = DJI_LOGGER_CONSOLE_LOG_LEVEL_INFO;
    console.isSupportColor = true;
    RequireSuccess(DjiLogger_AddConsole(&console), "register logger");
}

template <std::size_t N>
void CopyRequiredValue(char (&destination)[N], const char *value, const char *name,
                       bool nulTerminated)
{
    if (value == nullptr || value[0] == '\0') {
        throw std::runtime_error(std::string("PSDK identity field belum terisi: ") + name);
    }
    const std::size_t length = std::strlen(value);
    const std::size_t limit = nulTerminated ? N - 1 : N;
    if (length > limit) {
        throw std::runtime_error(std::string("Environment secret terlalu panjang: ") + name);
    }
    std::memset(destination, 0, N);
    std::memcpy(destination, value, length);
}

T_DjiUserInfo LoadUserInfo()
{
    T_DjiUserInfo info = {};
#ifdef GAP_PLOT_AI_COMPILED_APP_INFO
    CopyRequiredValue(info.appName, USER_APP_NAME, "appName", true);
    CopyRequiredValue(info.appId, USER_APP_ID, "appId", false);
    CopyRequiredValue(info.appKey, USER_APP_KEY, "appKey", false);
    CopyRequiredValue(info.appLicense, USER_APP_LICENSE, "appLicense", false);
    CopyRequiredValue(info.developerAccount, USER_DEVELOPER_ACCOUNT,
                      "developerAccount", true);
    CopyRequiredValue(info.baudRate, USER_BAUD_RATE, "baudRate", false);
#else
    CopyRequiredValue(info.appName, std::getenv("DJI_APP_NAME"), "appName", true);
    CopyRequiredValue(info.appId, std::getenv("DJI_APP_ID"), "appId", false);
    CopyRequiredValue(info.appKey, std::getenv("DJI_APP_KEY"), "appKey", false);
    CopyRequiredValue(info.appLicense, std::getenv("DJI_APP_LICENSE"),
                      "appLicense", false);
    CopyRequiredValue(info.developerAccount, std::getenv("DJI_DEVELOPER_ACCOUNT"),
                      "developerAccount", true);
    CopyRequiredValue(info.baudRate, std::getenv("DJI_BAUD_RATE"), "baudRate",
                      false);
#endif
    return info;
}

std::string IpcPath(const std::string &name)
{
    return g_ipcDirectory + "/" + name;
}

bool AtomicWrite(const std::string &path, const uint8_t *data, std::size_t size)
{
    const std::string temporary = path + ".tmp";
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    if (!output) {
        return false;
    }
    output.write(reinterpret_cast<const char *>(data), static_cast<std::streamsize>(size));
    output.flush();
    if (!output) {
        output.close();
        std::remove(temporary.c_str());
        return false;
    }
    output.close();
    if (std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(temporary.c_str());
        return false;
    }
    return true;
}

bool WriteControls()
{
    Controls controls;
    {
        std::lock_guard<std::mutex> lock(g_controlMutex);
        controls = g_controls;
    }
    std::ostringstream value;
    value << "running=" << (controls.running ? 1 : 0) << '\n'
          << "plant=" << (controls.plant ? 1 : 0) << '\n'
          << "segmenter=" << (controls.segmenter ? 1 : 0) << '\n'
          << "snapshot_seq=" << controls.snapshotSequence << '\n';
    const std::string text = value.str();
    return AtomicWrite(IpcPath("control.txt"),
                       reinterpret_cast<const uint8_t *>(text.data()), text.size());
}

T_DjiReturnCode SetWidgetValue(E_DjiWidgetType, uint32_t index, int32_t value, void *)
{
    if (index >= g_widgetValues.size()) {
        return DJI_ERROR_SYSTEM_MODULE_CODE_INVALID_PARAMETER;
    }
    {
        std::lock_guard<std::mutex> lock(g_controlMutex);
        switch (index) {
            case 0:
                if (!g_controls.running) {
                    g_controls.running = true;
                    g_statusValue.store(1);
                    g_overlayMetadataAvailable.store(true);
                    g_overlayMetadataErrorCode.store(0);
                }
                break;
            case 1:
                if (g_controls.running) {
                    g_controls.running = false;
                    g_statusValue.store(4);
                }
                {
                    std::lock_guard<std::mutex> resultLock(g_resultMutex);
                    g_renderResult.boxes.clear();
                    g_renderResult.contours.clear();
                    g_renderResult.status = "STOPPING";
                }
                break;
            case 2:
                g_controls.plant = value != 0;
                g_widgetValues[2] = value != 0;
                break;
            case 3:
                g_controls.segmenter = value != 0;
                g_widgetValues[3] = value != 0;
                break;
            case 4:
                ++g_controls.snapshotSequence;
                break;
            case 5:
                break;
            default:
                return DJI_ERROR_SYSTEM_MODULE_CODE_INVALID_PARAMETER;
        }
    }
    if (!WriteControls()) {
        USER_LOG_ERROR("Gagal menulis control worker.");
        return DJI_ERROR_SYSTEM_MODULE_CODE_SYSTEM_ERROR;
    }
    return DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS;
}

T_DjiReturnCode GetWidgetValue(E_DjiWidgetType, uint32_t index, int32_t *value, void *)
{
    if (value == nullptr || index >= g_widgetValues.size()) {
        return DJI_ERROR_SYSTEM_MODULE_CODE_INVALID_PARAMETER;
    }
    if (index == 5) {
        *value = g_statusValue.load();
    } else {
        std::lock_guard<std::mutex> lock(g_controlMutex);
        *value = g_widgetValues[index];
    }
    return DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS;
}

void InitWidget()
{
    const char *widgetDirectory = std::getenv("GAP_PLOT_AI_WIDGET_DIR");
    if (widgetDirectory == nullptr || widgetDirectory[0] == '\0') {
        throw std::runtime_error("GAP_PLOT_AI_WIDGET_DIR belum terisi");
    }
    static const T_DjiWidgetHandlerListItem handlers[] = {
        {0, DJI_WIDGET_TYPE_BUTTON, SetWidgetValue, GetWidgetValue, nullptr},
        {1, DJI_WIDGET_TYPE_BUTTON, SetWidgetValue, GetWidgetValue, nullptr},
        {2, DJI_WIDGET_TYPE_SWITCH, SetWidgetValue, GetWidgetValue, nullptr},
        {3, DJI_WIDGET_TYPE_SWITCH, SetWidgetValue, GetWidgetValue, nullptr},
        {4, DJI_WIDGET_TYPE_BUTTON, SetWidgetValue, GetWidgetValue, nullptr},
        {5, DJI_WIDGET_TYPE_LIST, SetWidgetValue, GetWidgetValue, nullptr},
    };
    T_DjiReturnCode code = DjiWidget_Init();
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        throw std::runtime_error("DjiWidget_Init gagal");
    }
    code = DjiWidget_RegDefaultUiConfigByDirPath(widgetDirectory);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        throw std::runtime_error("Registrasi widget_config.json gagal");
    }
    code = DjiWidget_RegHandlerList(handlers, sizeof(handlers) / sizeof(handlers[0]));
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        throw std::runtime_error("Registrasi widget handlers gagal");
    }
}

template <typename T>
bool ReadTopic(E_DjiFcSubscriptionTopic topic, T *value)
{
    T_DjiDataTimestamp timestamp = {};
    return DjiFcSubscription_GetLatestValueOfTopic(
               topic, reinterpret_cast<uint8_t *>(value), sizeof(T), &timestamp) ==
           DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS;
}

void SubscribeTopic(E_DjiFcSubscriptionTopic topic, E_DjiDataSubscriptionTopicFreq frequency)
{
    const T_DjiReturnCode code = DjiFcSubscription_SubscribeTopic(topic, frequency, nullptr);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS &&
        code != DJI_ERROR_SUBSCRIPTION_MODULE_CODE_TOPIC_DUPLICATE &&
        code != DJI_ERROR_SYSTEM_MODULE_CODE_NONSUPPORT) {
        USER_LOG_WARN("Subscribe topic %d gagal, code=0x%08llX", static_cast<int>(topic),
                      static_cast<unsigned long long>(code));
    }
}

void InitTelemetry()
{
    const T_DjiReturnCode code = DjiFcSubscription_Init();
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        USER_LOG_WARN("Telemetry init gagal, semua telemetry akan null. code=0x%08llX",
                      static_cast<unsigned long long>(code));
        return;
    }
    g_fcSubscriptionInitialized = true;
    SubscribeTopic(DJI_FC_SUBSCRIPTION_TOPIC_POSITION_FUSED,
                   DJI_DATA_SUBSCRIPTION_TOPIC_5_HZ);
    SubscribeTopic(DJI_FC_SUBSCRIPTION_TOPIC_HEIGHT_RELATIVE,
                   DJI_DATA_SUBSCRIPTION_TOPIC_5_HZ);
    SubscribeTopic(DJI_FC_SUBSCRIPTION_TOPIC_GIMBAL_ANGLES,
                   DJI_DATA_SUBSCRIPTION_TOPIC_5_HZ);
    SubscribeTopic(DJI_FC_SUBSCRIPTION_TOPIC_QUATERNION,
                   DJI_DATA_SUBSCRIPTION_TOPIC_5_HZ);
    SubscribeTopic(DJI_FC_SUBSCRIPTION_TOPIC_VELOCITY,
                   DJI_DATA_SUBSCRIPTION_TOPIC_5_HZ);
    SubscribeTopic(DJI_FC_SUBSCRIPTION_TOPIC_GPS_SIGNAL_LEVEL,
                   DJI_DATA_SUBSCRIPTION_TOPIC_5_HZ);
    SubscribeTopic(DJI_FC_SUBSCRIPTION_TOPIC_RTK_POSITION_INFO,
                   DJI_DATA_SUBSCRIPTION_TOPIC_1_HZ);
}

FrameHeader BuildFrameHeader(const FramePacket &frame)
{
    FrameHeader header = {};
    header.gpsSignalLevel = -1;
    std::memcpy(header.magic, kFrameMagic, sizeof(kFrameMagic));
    header.headerVersion = kFrameHeaderVersion;
    header.pixelFormat = frame.pixelFormat;
    header.frameSequence = frame.frameSequence;
    header.sourceFrameId = frame.sourceFrameId;
    header.captureMonotonicNs = frame.captureMonotonicNs;
    header.captureWallClockNs = frame.captureWallClockNs;
    header.telemetryMonotonicNs = MonotonicNs();
    header.width = frame.width;
    header.height = frame.height;
    header.rowStride = frame.rowStride;
    header.channels = 3;
    header.dataLength = static_cast<uint32_t>(frame.rgb.size());
    if (!g_fcSubscriptionInitialized) {
        return header;
    }
    T_DjiFcSubscriptionPositionFused position = {};
    if (ReadTopic(DJI_FC_SUBSCRIPTION_TOPIC_POSITION_FUSED, &position)) {
        constexpr double radiansToDegrees = 180.0 / 3.14159265358979323846;
        header.latitude = position.latitude * radiansToDegrees;
        header.longitude = position.longitude * radiansToDegrees;
        header.absoluteAltitude = position.altitude;
        header.visibleSatellites = position.visibleSatelliteNumber;
        header.telemetryValidMask |=
            kTelemetryLatitude | kTelemetryLongitude | kTelemetryAbsoluteAltitude |
            kTelemetryGpsQuality;
    }
    T_DjiFcSubscriptionHeightRelative relativeAltitude = 0;
    if (ReadTopic(DJI_FC_SUBSCRIPTION_TOPIC_HEIGHT_RELATIVE, &relativeAltitude)) {
        header.relativeAltitude = relativeAltitude;
        header.telemetryValidMask |= kTelemetryRelativeAltitude;
    }
    T_DjiFcSubscriptionGimbalAngles gimbal = {};
    if (ReadTopic(DJI_FC_SUBSCRIPTION_TOPIC_GIMBAL_ANGLES, &gimbal)) {
        header.gimbalRoll = gimbal.y;
        header.gimbalPitch = gimbal.x;
        header.gimbalYaw = gimbal.z;
        header.telemetryValidMask |= kTelemetryGimbalPitch;
    }
    T_DjiFcSubscriptionQuaternion quaternion = {};
    if (ReadTopic(DJI_FC_SUBSCRIPTION_TOPIC_QUATERNION, &quaternion)) {
        const double rollNumerator =
            2.0 * (quaternion.q0 * quaternion.q1 + quaternion.q2 * quaternion.q3);
        const double rollDenominator =
            1.0 - 2.0 * (quaternion.q1 * quaternion.q1 + quaternion.q2 * quaternion.q2);
        const double pitchValue = std::max(
            -1.0, std::min(1.0, 2.0 *
                (quaternion.q0 * quaternion.q2 - quaternion.q3 * quaternion.q1)));
        const double yawNumerator =
            2.0 * (quaternion.q0 * quaternion.q3 + quaternion.q1 * quaternion.q2);
        const double yawDenominator =
            1.0 - 2.0 * (quaternion.q2 * quaternion.q2 + quaternion.q3 * quaternion.q3);
        constexpr double radiansToDegrees = 180.0 / 3.14159265358979323846;
        header.aircraftRoll = std::atan2(rollNumerator, rollDenominator) * radiansToDegrees;
        header.aircraftPitch = std::asin(pitchValue) * radiansToDegrees;
        header.aircraftYaw = std::atan2(yawNumerator, yawDenominator) * radiansToDegrees;
        header.telemetryValidMask |= kTelemetryHeading;
    }
    T_DjiFcSubscriptionVelocity velocity = {};
    if (ReadTopic(DJI_FC_SUBSCRIPTION_TOPIC_VELOCITY, &velocity) && velocity.health) {
        header.velocityX = velocity.data.x;
        header.velocityY = velocity.data.y;
        header.velocityZ = velocity.data.z;
        header.speedMps = std::sqrt(
            header.velocityX * header.velocityX +
            header.velocityY * header.velocityY +
            header.velocityZ * header.velocityZ);
        header.telemetryValidMask |= kTelemetryVelocity;
    }
    T_DjiFcSubscriptionGpsSignalLevel gpsSignal = 0;
    if (ReadTopic(DJI_FC_SUBSCRIPTION_TOPIC_GPS_SIGNAL_LEVEL, &gpsSignal)) {
        header.gpsSignalLevel = gpsSignal;
        header.telemetryValidMask |= kTelemetryGpsQuality;
    }
    T_DjiFcSubscriptionRtkPositionInfo rtk = 0;
    if (ReadTopic(DJI_FC_SUBSCRIPTION_TOPIC_RTK_POSITION_INFO, &rtk)) {
        header.rtkStatus = rtk;
        header.telemetryValidMask |= kTelemetryRtk;
    }
    return header;
}

void DrawLine(std::vector<uint8_t> *rgb, int width, int height,
              NormalizedPoint first, NormalizedPoint second);
T_DjiLiveViewStandardMetaData *BuildPilotMetadata(const RenderResult &result);

void FrameSpoolThread()
{
    while (!g_stop.load()) {
        FramePacket frame;
        {
            std::unique_lock<std::mutex> lock(g_frameMutex);
            g_frameCondition.wait_for(lock, std::chrono::milliseconds(200), [] {
                return g_stop.load() || g_framePending;
            });
            if (g_stop.load()) {
                break;
            }
            if (!g_framePending) {
                continue;
            }
            frame = std::move(g_latestFrame);
            g_framePending = false;
        }
        FrameHeader header = BuildFrameHeader(frame);
        std::vector<uint8_t> payload(sizeof(header) + frame.rgb.size());
        std::memcpy(payload.data(), &header, sizeof(header));
        std::memcpy(payload.data() + sizeof(header), frame.rgb.data(), frame.rgb.size());
        if (!AtomicWrite(IpcPath("latest_frame.rgb"), payload.data(), payload.size())) {
            USER_LOG_WARN("Gagal menulis latest-frame spool.");
        }
        if (g_renderedStreamEnabled) {
            RenderResult render;
            {
                std::lock_guard<std::mutex> lock(g_resultMutex);
                render = g_renderResult;
            }
            const uint64_t now = MonotonicNs();
            if (render.generatedMonotonicNs == 0 ||
                now - render.generatedMonotonicNs > g_staleResultTimeoutNs) {
                render.boxes.clear();
                render.contours.clear();
            }
            std::vector<uint8_t> rendered = frame.rgb;
            for (const NormalizedContour &contour : render.contours) {
                for (std::size_t index = 0; index < contour.points.size(); ++index) {
                    DrawLine(&rendered, frame.width, frame.height,
                             contour.points[index],
                             contour.points[(index + 1) % contour.points.size()]);
                }
            }
            T_DjiLiveViewStandardMetaData *metadata = BuildPilotMetadata(render);
            const T_DjiReturnCode encodeCode = DjiLiveview_EncodeAFrameToH264(
                rendered.data(), static_cast<uint32_t>(rendered.size()),
                frame.imageInfo, metadata);
            if (encodeCode != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
                USER_LOG_WARN("Encode AI rendered stream gagal, code=0x%08llX",
                              static_cast<unsigned long long>(encodeCode));
            }
            std::free(metadata);
        }
    }
}

bool ParseResultFile(RenderResult *parsed)
{
    std::ifstream input(IpcPath("latest_result.txt"));
    if (!input) {
        return false;
    }
    std::string line;
    RenderResult next;
    bool sawHeader = false;
    while (std::getline(input, line)) {
        std::istringstream stream(line);
        std::string type;
        stream >> type;
        if (type == "RESULT") {
            int warningCount = 0;
            stream >> next.frameIndex >> next.captureMonotonicNs >>
                next.generatedMonotonicNs >> next.latencyMs >> next.fps >>
                next.p50LatencyMs >> next.status >> warningCount >>
                next.totalDetections >> next.overlayDetections >> next.gapCount >>
                next.sourceWidth >> next.sourceHeight >> next.rowStride;
            sawHeader = !stream.fail();
        } else if (type == "BOX" && next.boxes.size() < kMaxPilotBoxes) {
            unsigned int classId = 0;
            NormalizedBox box;
            stream >> box.id >> classId >> box.confidence >> box.x1 >> box.y1 >> box.x2 >>
                box.y2;
            if (!stream.fail()) {
                box.classId = static_cast<uint8_t>(std::min(classId, 255U));
                box.x1 = Clamp01(box.x1);
                box.y1 = Clamp01(box.y1);
                box.x2 = Clamp01(box.x2);
                box.y2 = Clamp01(box.y2);
                if (box.x2 > box.x1 && box.y2 > box.y1) {
                    next.boxes.push_back(box);
                }
            }
        } else if (type == "CONTOUR" && next.contours.size() < kMaxContours) {
            uint32_t contourId = 0;
            std::size_t pointCount = 0;
            stream >> contourId >> pointCount;
            pointCount = std::min(pointCount, kMaxContourPoints);
            NormalizedContour contour;
            for (std::size_t i = 0; i < pointCount; ++i) {
                NormalizedPoint point;
                stream >> point.x >> point.y;
                if (stream.fail()) {
                    contour.points.clear();
                    break;
                }
                point.x = Clamp01(point.x);
                point.y = Clamp01(point.y);
                contour.points.push_back(point);
            }
            if (contour.points.size() >= 3) {
                next.contours.push_back(std::move(contour));
            }
        } else if (type == "END") {
            break;
        }
    }
    if (!sawHeader) {
        return false;
    }
    *parsed = std::move(next);
    return true;
}

void DrawPixel(std::vector<uint8_t> *rgb, int width, int height, int x, int y)
{
    if (x < 0 || y < 0 || x >= width || y >= height) {
        return;
    }
    const std::size_t offset = (static_cast<std::size_t>(y) * width + x) * 3;
    (*rgb)[offset] = 255;
    (*rgb)[offset + 1] = 215;
    (*rgb)[offset + 2] = 0;
}

void DrawLine(std::vector<uint8_t> *rgb, int width, int height,
              NormalizedPoint first, NormalizedPoint second)
{
    int x0 = static_cast<int>(std::lround(first.x * std::max(0, width - 1)));
    int y0 = static_cast<int>(std::lround(first.y * std::max(0, height - 1)));
    const int x1 = static_cast<int>(std::lround(second.x * std::max(0, width - 1)));
    const int y1 = static_cast<int>(std::lround(second.y * std::max(0, height - 1)));
    const int dx = std::abs(x1 - x0);
    const int sx = x0 < x1 ? 1 : -1;
    const int dy = -std::abs(y1 - y0);
    const int sy = y0 < y1 ? 1 : -1;
    int error = dx + dy;
    while (true) {
        for (int oy = -1; oy <= 1; ++oy) {
            for (int ox = -1; ox <= 1; ++ox) {
                DrawPixel(rgb, width, height, x0 + ox, y0 + oy);
            }
        }
        if (x0 == x1 && y0 == y1) {
            break;
        }
        const int twice = 2 * error;
        if (twice >= dy) {
            error += dy;
            x0 += sx;
        }
        if (twice <= dx) {
            error += dx;
            y0 += sy;
        }
    }
}

T_DjiLiveViewStandardMetaData *BuildPilotMetadata(const RenderResult &result)
{
    const std::size_t boxCount = std::min(result.boxes.size(), kMaxPilotBoxes);
    const std::size_t allocation =
        sizeof(T_DjiLiveViewStandardMetaData) +
        (boxCount > 0 ? boxCount - 1 : 0) * sizeof(T_DjiLiveViewBoundingBox);
    auto *metadata = static_cast<T_DjiLiveViewStandardMetaData *>(std::calloc(1, allocation));
    if (metadata == nullptr) {
        return nullptr;
    }
    metadata->boxCount = static_cast<uint8_t>(boxCount);
    for (std::size_t index = 0; index < boxCount; ++index) {
        const NormalizedBox &source = result.boxes[index];
        T_DjiLiveViewBoundingBox &target = metadata->boxData[index];
        target.id = source.id;
        target.type = source.classId;
        target.state = DJI_LIVEVIEW_OBJ_STATE_TRACKED;
        const float width = source.x2 - source.x1;
        const float height = source.y2 - source.y1;
        target.box.cx = static_cast<uint16_t>(
            std::lround(Clamp01(source.x1 + width / 2) * 10000));
        target.box.cy = static_cast<uint16_t>(
            std::lround(Clamp01(source.y1 + height / 2) * 10000));
        target.box.w =
            static_cast<uint16_t>(std::lround(Clamp01(width) * 10000));
        target.box.h =
            static_cast<uint16_t>(std::lround(Clamp01(height) * 10000));
        target.box.distance = 0;
    }
    return metadata;
}

bool SendPilotMetadata(RenderResult result)
{
    const uint64_t now = MonotonicNs();
    if (!g_staticOverlayDebug &&
        (result.generatedMonotonicNs == 0 ||
         now - result.generatedMonotonicNs > g_staleResultTimeoutNs)) {
        result.boxes.clear();
        result.contours.clear();
    }
    T_DjiLiveViewStandardMetaData *metadata = BuildPilotMetadata(result);
    if (metadata == nullptr) {
        USER_LOG_ERROR("Alokasi AI metadata gagal.");
        return false;
    }
    const T_DjiReturnCode code = DjiLiveview_SendAiMetaToPilot(metadata);
    std::free(metadata);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        g_overlayMetadataAvailable.store(false);
        g_overlayMetadataErrorCode.store(static_cast<uint64_t>(code));
        USER_LOG_ERROR("PSDK AI metadata overlay unavailable, code=0x%08llX",
                       static_cast<unsigned long long>(code));
        return false;
    }
    g_overlayMetadataAvailable.store(true);
    g_overlayMetadataErrorCode.store(0);
    return true;
}

RenderResult StaticOverlayResult()
{
    RenderResult result;
    result.status = "RUNNING";
    result.generatedMonotonicNs = MonotonicNs();
    const std::array<std::pair<float, float>, 5> centers{{
        {0.50F, 0.50F}, {0.06F, 0.06F}, {0.94F, 0.06F},
        {0.06F, 0.94F}, {0.94F, 0.94F},
    }};
    for (std::size_t index = 0; index < centers.size(); ++index) {
        NormalizedBox box;
        box.id = static_cast<uint16_t>(index);
        box.classId = 0;
        box.confidence = 1.0F;
        box.x1 = Clamp01(centers[index].first - 0.04F);
        box.y1 = Clamp01(centers[index].second - 0.04F);
        box.x2 = Clamp01(centers[index].first + 0.04F);
        box.y2 = Clamp01(centers[index].second + 0.04F);
        result.boxes.push_back(box);
    }
    result.totalDetections = static_cast<uint32_t>(result.boxes.size());
    result.overlayDetections = result.totalDetections;
    return result;
}

void EncoderCallback(const uint8_t *buffer, uint32_t length)
{
    const T_DjiReturnCode code = DjiPayloadCamera_SendVideoStream(buffer, length);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        static std::atomic<uint32_t> failures{0};
        if ((++failures % 100) == 1) {
            USER_LOG_WARN("Send AI video stream gagal, code=0x%08llX",
                          static_cast<unsigned long long>(code));
        }
    }
}

void ImageCallback(E_DjiLiveViewCameraPosition, const uint8_t *buffer, uint32_t length,
                   T_DjiLiveviewImageInfo imageInfo)
{
    if (g_stop.load()) {
        return;
    }
    const uint32_t rowStride = static_cast<uint32_t>(imageInfo.width) * 3U;
    const uint64_t expected = static_cast<uint64_t>(rowStride) * imageInfo.height;
    if (buffer == nullptr || imageInfo.pixFmt != PIXFMT_RGB_PACKED ||
        length != expected || expected == 0) {
        return;
    }
    const uint64_t arrivalMonotonicNs = MonotonicNs();
    g_lastFrameArrivalNs.store(arrivalMonotonicNs);
    g_sourceFrameCount.fetch_add(1);
    Controls controls;
    {
        std::lock_guard<std::mutex> lock(g_controlMutex);
        controls = g_controls;
    }
    if (!controls.running && !g_renderedStreamEnabled) {
        return;
    }
    FramePacket packet;
    packet.frameSequence = g_frameSequence.fetch_add(1) + 1;
    packet.sourceFrameId = imageInfo.frameId;
    packet.captureMonotonicNs = arrivalMonotonicNs;
    packet.captureWallClockNs = RealtimeNs();
    packet.pixelFormat = static_cast<uint16_t>(imageInfo.pixFmt);
    packet.width = imageInfo.width;
    packet.height = imageInfo.height;
    packet.rowStride = rowStride;
    packet.imageInfo = imageInfo;
    packet.rgb.assign(buffer, buffer + length);
    {
        std::lock_guard<std::mutex> lock(g_frameMutex);
        if (g_framePending) {
            g_droppedFrames.fetch_add(1);
        }
        g_latestFrame = std::move(packet);
        g_framePending = true;
    }
    g_frameCondition.notify_one();
}

void StartLiveview()
{
    const T_DjiDataChannelBandwidthProportionOfHighspeedChannel bandwidth = {10, 60, 30};
    T_DjiReturnCode code = DjiHighSpeedDataChannel_SetBandwidthProportion(bandwidth);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        throw std::runtime_error("Set bandwidth liveview gagal");
    }
    code = DjiLiveview_Init();
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        throw std::runtime_error("DjiLiveview_Init gagal");
    }
    g_liveviewInitialized = true;
    static const char *labels[] = {"plant"};
    code = DjiLiveview_RegUserAiTargetLableList(1, labels);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        throw std::runtime_error("Register AI label gagal");
    }
    if (g_renderedStreamEnabled) {
        code = DjiLiveview_RegEncoderCallback(EncoderCallback);
        if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
            throw std::runtime_error("Rendered stream diminta tetapi encoder callback tidak tersedia");
        }
        g_encoderRegistered = true;
    }
    code = DjiLiveview_StartImageStream(
        DJI_LIVEVIEW_CAMERA_POSITION_NO_1, DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS,
        PIXFMT_RGB_PACKED, ImageCallback);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        std::ostringstream message;
        message << "Decoded M4E RGB stream unavailable, code=0x" << std::hex << code
                << "; H.264 decoder fallback tidak dibangun pada target ini";
        throw std::runtime_error(message.str());
    }
    g_imageStreamStarted = true;
    g_lastFrameArrivalNs.store(MonotonicNs());
}

void StopLiveview()
{
    if (g_imageStreamStarted) {
        DjiLiveview_StopImageStream(DJI_LIVEVIEW_CAMERA_POSITION_NO_1,
                                    DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS);
        g_imageStreamStarted = false;
    }
    if (g_liveviewInitialized) {
        DjiLiveview_UnregUserAiTargetLableList();
        if (g_encoderRegistered) {
            DjiLiveview_UnregEncoderCallback();
            g_encoderRegistered = false;
        }
        DjiLiveview_Deinit();
        g_liveviewInitialized = false;
    }
}

void RefreshLiveviewIfStalled()
{
    if (!g_imageStreamStarted) {
        return;
    }
    const uint64_t now = MonotonicNs();
    const uint64_t last = g_lastFrameArrivalNs.load();
    if (now - last < 5'000'000'000ULL) {
        return;
    }
    USER_LOG_WARN("Liveview tidak menerima frame 5 detik; mencoba resubscribe aman.");
    DjiLiveview_StopImageStream(DJI_LIVEVIEW_CAMERA_POSITION_NO_1,
                                DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS);
    const T_DjiReturnCode code = DjiLiveview_StartImageStream(
        DJI_LIVEVIEW_CAMERA_POSITION_NO_1, DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS,
        PIXFMT_RGB_PACKED, ImageCallback);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        USER_LOG_ERROR("Resubscribe liveview gagal, code=0x%08llX",
                       static_cast<unsigned long long>(code));
        g_statusValue.store(5);
    }
    g_lastFrameArrivalNs.store(now);
}

void UpdatePilotStatus(const RenderResult &result)
{
    if (result.status == "STARTING") g_statusValue.store(1);
    else if (result.status == "WARMING_UP") g_statusValue.store(2);
    else if (result.status == "RUNNING") g_statusValue.store(3);
    else if (result.status == "STOPPING") g_statusValue.store(4);
    else if (result.status == "ERROR") g_statusValue.store(5);
    else g_statusValue.store(0);
    static uint64_t previousFrames = 0;
    static uint64_t previousTime = MonotonicNs();
    const uint64_t now = MonotonicNs();
    const uint64_t currentFrames = g_sourceFrameCount.load();
    const double elapsedSeconds = static_cast<double>(now - previousTime) / 1e9;
    const double sourceFps = elapsedSeconds > 0
        ? static_cast<double>(currentFrames - previousFrames) / elapsedSeconds
        : 0.0;
    previousFrames = currentFrames;
    previousTime = now;
    char gapText[16] = {};
    if (result.gapCount < 0) {
        std::snprintf(gapText, sizeof(gapText), "N/A");
    } else {
        std::snprintf(gapText, sizeof(gapText), "%d", result.gapCount);
    }
    char message[DJI_WIDGET_FLOATING_WINDOW_MSG_MAX_LEN] = {};
    if (result.status == "ERROR") {
        std::snprintf(message, sizeof(message),
                      "Gap Plot AI DEV: ERROR\r\n%.190s",
                      result.lastError.empty() ? "worker/stream error; check errors.log"
                                               : result.lastError.c_str());
    } else {
        std::snprintf(message, sizeof(message),
                      "Gap Plot AI DEV: %s\r\nAI %.1f FPS | %.0f ms | p50 %.0f\r\n"
                      "plants %u | Pilot %u | gap %s\r\n"
                      "RTK %d GPS %d | model %d ctx %d warm %d\r\n"
                      "%ux%u %.1f FPS | drop %llu | log %s",
                      result.status.c_str(), result.fps, result.latencyMs,
                      result.p50LatencyMs, result.totalDetections,
                      result.overlayDetections, gapText,
                      result.rtkStatus, result.gpsSignalLevel,
                      result.modelLoadCount, result.backendInitializationCount,
                      result.warmupCount,
                      result.sourceWidth, result.sourceHeight, sourceFps,
                      static_cast<unsigned long long>(g_droppedFrames.load()),
                      result.sessionId.empty() ? "off" : "active");
    }
    DjiWidgetFloatingWindow_ShowMessage(message);
}

bool WorkerHeartbeatHealthy(uint64_t timeoutMilliseconds)
{
    struct stat info = {};
    if (stat(IpcPath("worker_status.json").c_str(), &info) != 0) {
        return false;
    }
    const std::time_t now = std::time(nullptr);
    if (now < info.st_mtime) {
        return true;
    }
    const uint64_t ageMilliseconds =
        static_cast<uint64_t>(now - info.st_mtime) * 1000ULL;
    return ageMilliseconds <= timeoutMilliseconds;
}

std::string ExtractJsonString(const std::string &payload, const std::string &key)
{
    const std::string marker = "\"" + key + "\":\"";
    const std::size_t start = payload.find(marker);
    if (start == std::string::npos) {
        return {};
    }
    const std::size_t valueStart = start + marker.size();
    const std::size_t end = payload.find('"', valueStart);
    return end == std::string::npos ? std::string{} : payload.substr(valueStart, end - valueStart);
}

int32_t ExtractJsonInteger(const std::string &payload, const std::string &key,
                           int32_t fallback)
{
    const std::string marker = "\"" + key + "\":";
    const std::size_t start = payload.find(marker);
    if (start == std::string::npos) {
        return fallback;
    }
    const char *value = payload.c_str() + start + marker.size();
    char *end = nullptr;
    const long parsed = std::strtol(value, &end, 10);
    if (end == value) {
        return fallback;
    }
    return static_cast<int32_t>(parsed);
}

void ReadWorkerStatus(RenderResult *result)
{
    std::ifstream input(IpcPath("worker_status.json"));
    if (!input) {
        return;
    }
    const std::string payload((std::istreambuf_iterator<char>(input)),
                              std::istreambuf_iterator<char>());
    const std::string status = ExtractJsonString(payload, "status");
    if (status == "IDLE" || status == "STARTING" || status == "WARMING_UP" ||
        status == "RUNNING" || status == "STOPPING" || status == "ERROR") {
        result->status = status;
    }
    const std::string code = ExtractJsonString(payload, "code");
    const std::string message = ExtractJsonString(payload, "message");
    if (!code.empty() || !message.empty()) {
        result->lastError = code + (code.empty() || message.empty() ? "" : ": ") + message;
    }
    result->rtkStatus = ExtractJsonInteger(payload, "rtk_status", -1);
    result->gpsSignalLevel = ExtractJsonInteger(payload, "gps_signal_level", -1);
    result->modelLoadCount = ExtractJsonInteger(payload, "model_load_count", 0);
    result->warmupCount = ExtractJsonInteger(payload, "warmup_count", 0);
    result->backendInitializationCount =
        ExtractJsonInteger(payload, "backend_initialization_count", 0);
    result->sessionId = ExtractJsonString(payload, "session_id");
}

void ValidateAircraft()
{
    T_DjiAircraftInfoBaseInfo info = {};
    const T_DjiReturnCode code = DjiAircraftInfo_GetBaseInfo(&info);
    if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
        throw std::runtime_error("Tidak dapat membaca aircraft product type");
    }
    USER_LOG_INFO("Aircraft type=%d mountPositionType=%d adapter=%d",
                  static_cast<int>(info.aircraftType),
                  static_cast<int>(info.mountPositionType),
                  static_cast<int>(info.djiAdapterType));
    if (info.aircraftType != DJI_AIRCRAFT_TYPE_M4E) {
        throw std::runtime_error("Aplikasi dikunci untuk DJI Matrice 4E");
    }
    if (info.mountPositionType != DJI_MOUNT_POSITION_TYPE_MANIFOLD3_ONBOARD) {
        throw std::runtime_error("Aplikasi harus berjalan sebagai Manifold 3 onboard");
    }
}

}  // namespace

int main()
{
    std::signal(SIGINT, SignalHandler);
    std::signal(SIGTERM, SignalHandler);
    const char *ipc = std::getenv("GAP_PLOT_AI_IPC_DIR");
    if (ipc == nullptr || ipc[0] == '\0') {
        std::cerr << "GAP_PLOT_AI_IPC_DIR belum terisi\n";
        return 2;
    }
    g_ipcDirectory = ipc;
    g_renderedStreamEnabled = EnvironmentFlag("GAP_PLOT_AI_ENABLE_RENDERED_STREAM");
    g_staticOverlayDebug = EnvironmentFlag("GAP_PLOT_AI_STATIC_OVERLAY_DEBUG");
    g_staleResultTimeoutNs =
        EnvironmentMilliseconds("GAP_PLOT_AI_STALE_RESULT_TIMEOUT_MS", 1500) *
        1'000'000ULL;
    const uint64_t workerHeartbeatTimeoutMs =
        EnvironmentMilliseconds("GAP_PLOT_AI_WORKER_HEARTBEAT_TIMEOUT_MS", 3000);
    bool coreInitialized = false;
    std::thread spoolThread;
    try {
        EnsureRuntimeDirectories();
        SetupPlatform();
        const T_DjiUserInfo userInfo = LoadUserInfo();
        T_DjiReturnCode code = DjiCore_Init(&userInfo);
        if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
            throw std::runtime_error("DjiCore_Init gagal; cek aktivasi/credential secara lokal");
        }
        coreInitialized = true;
        ValidateAircraft();
        code = DjiCore_SetAlias("Gap Plot AI DEV");
        if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
            throw std::runtime_error("DjiCore_SetAlias gagal");
        }
        T_DjiFirmwareVersion version = {};
        version.majorVersion = 0;
        version.minorVersion = 1;
        version.modifyVersion = 0;
        version.debugVersion = 0;
        code = DjiCore_SetFirmwareVersion(version);
        if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
            throw std::runtime_error("DjiCore_SetFirmwareVersion gagal");
        }
        InitWidget();
        code = DjiCore_ApplicationStart();
        if (code != DJI_ERROR_SYSTEM_MODULE_CODE_SUCCESS) {
            throw std::runtime_error("DjiCore_ApplicationStart gagal");
        }
        InitTelemetry();
        if (!WriteControls()) {
            throw std::runtime_error("Tidak dapat membuat control.txt");
        }
        StartLiveview();
        spoolThread = std::thread(FrameSpoolThread);
        if (g_staticOverlayDebug) {
            std::lock_guard<std::mutex> lock(g_resultMutex);
            g_renderResult = StaticOverlayResult();
        }
        USER_LOG_INFO("gap_plot_ai DEV ready, M4E visual stream requested; "
                      "rendered_stream=%d static_overlay=%d.",
                      g_renderedStreamEnabled, g_staticOverlayDebug);

        uint64_t lastParsedFrame = std::numeric_limits<uint64_t>::max();
        uint64_t lastStatusUpdate = 0;
        bool pilotOverlayCleared = true;
        while (!g_stop.load()) {
            RenderResult parsed;
            if (ParseResultFile(&parsed) && parsed.frameIndex != lastParsedFrame) {
                lastParsedFrame = parsed.frameIndex;
                {
                    std::lock_guard<std::mutex> lock(g_resultMutex);
                    g_renderResult = parsed;
                }
                Controls controls;
                {
                    std::lock_guard<std::mutex> lock(g_controlMutex);
                    controls = g_controls;
                }
                if (controls.running && controls.plant) {
                    if (!SendPilotMetadata(parsed)) {
                        g_statusValue.store(5);
                    } else {
                        pilotOverlayCleared = parsed.boxes.empty();
                    }
                }
            }
            const uint64_t now = MonotonicNs();
            if (now - lastStatusUpdate > 500'000'000ULL) {
                RenderResult current;
                {
                    std::lock_guard<std::mutex> lock(g_resultMutex);
                    current = g_renderResult;
                }
                ReadWorkerStatus(&current);
                Controls controls;
                {
                    std::lock_guard<std::mutex> lock(g_controlMutex);
                    controls = g_controls;
                }
                if (g_staticOverlayDebug) {
                    current = StaticOverlayResult();
                    if (!SendPilotMetadata(current)) {
                        current.status = "ERROR";
                        current.lastError = "STATIC_OVERLAY_UNAVAILABLE: cek PSDK/firmware";
                    }
                } else if (controls.running &&
                           !WorkerHeartbeatHealthy(workerHeartbeatTimeoutMs)) {
                    current.status = "ERROR";
                    current.lastError = "WORKER_HEARTBEAT_TIMEOUT: worker tidak merespons";
                    current.boxes.clear();
                    current.contours.clear();
                    g_statusValue.store(5);
                } else if (controls.running &&
                           !g_overlayMetadataAvailable.load()) {
                    current.status = "ERROR";
                    std::ostringstream error;
                    error << "OVERLAY_UNAVAILABLE: DjiLiveview_SendAiMetaToPilot code=0x"
                          << std::hex << g_overlayMetadataErrorCode.load();
                    current.lastError = error.str();
                    current.boxes.clear();
                    current.contours.clear();
                    g_statusValue.store(5);
                } else if (!controls.running && current.status != "ERROR") {
                    current.status = "IDLE";
                    current.totalDetections = 0;
                    current.overlayDetections = 0;
                }
                const bool resultIsStale =
                    current.generatedMonotonicNs != 0 &&
                    now - current.generatedMonotonicNs > g_staleResultTimeoutNs;
                if (!g_staticOverlayDebug && !pilotOverlayCleared &&
                    (!controls.running || resultIsStale)) {
                    RenderResult empty;
                    empty.generatedMonotonicNs = now;
                    if (SendPilotMetadata(empty)) {
                        pilotOverlayCleared = true;
                    }
                    current.boxes.clear();
                    current.contours.clear();
                    current.overlayDetections = 0;
                }
                UpdatePilotStatus(current);
                RefreshLiveviewIfStalled();
                lastStatusUpdate = now;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    } catch (const std::exception &error) {
        std::cerr << "gap_plot_ai error: " << error.what() << '\n';
        g_statusValue.store(5);
        g_stop.store(true);
    }

    g_stop.store(true);
    g_frameCondition.notify_all();
    if (spoolThread.joinable()) {
        spoolThread.join();
    }
    StopLiveview();
    if (g_fcSubscriptionInitialized) {
        DjiFcSubscription_DeInit();
        g_fcSubscriptionInitialized = false;
    }
    if (coreInitialized) {
        DjiCore_DeInit();
    }
    return g_statusValue.load() == 5 ? 1 : 0;
}
