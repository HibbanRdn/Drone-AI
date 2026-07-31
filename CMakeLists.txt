cmake_minimum_required(VERSION 3.16)
project(gap_plot_ai LANGUAGES C CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR "PSDK frontend hanya boleh dibangun pada Linux/Manifold 3.")
endif()
if(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
  message(FATAL_ERROR "PSDK frontend membutuhkan target aarch64 Manifold 3.")
endif()
if(NOT DEFINED PSDK_ROOT)
  message(FATAL_ERROR "Set -DPSDK_ROOT=/path/to/official/Payload-SDK-3.16.0")
endif()
if(NOT DEFINED GAP_PLOT_AI_APP_INFO_HEADER)
  message(FATAL_ERROR
    "Set -DGAP_PLOT_AI_APP_INFO_HEADER to the generated private header")
endif()

get_filename_component(PSDK_ROOT "${PSDK_ROOT}" REALPATH)
get_filename_component(GAP_PLOT_AI_APP_INFO_HEADER
  "${GAP_PLOT_AI_APP_INFO_HEADER}" REALPATH)
get_filename_component(GAP_PLOT_AI_APP_INFO_DIR
  "${GAP_PLOT_AI_APP_INFO_HEADER}" DIRECTORY)
set(PSDK_INCLUDE "${PSDK_ROOT}/psdk_lib/include")
set(PSDK_LIB "${PSDK_ROOT}/psdk_lib/lib/aarch64-linux-gnu-gcc/libpayloadsdk.a")
set(PSDK_COMMON "${PSDK_ROOT}/samples/sample_c++/platform/linux/common")
set(PSDK_HAL "${PSDK_ROOT}/samples/sample_c++/platform/linux/manifold3/hal")
set(PSDK_316_AARCH64_LIBRARY_SHA256
    "c940d6d88f449ef6f48e535e0b765f4bf8486db44702d4d64e37da156d204426")

foreach(required_path "${PSDK_INCLUDE}/dji_liveview.h" "${PSDK_LIB}"
                      "${PSDK_COMMON}/osal/osal.c" "${PSDK_HAL}/hal_usb_bulk.c"
                      "${GAP_PLOT_AI_APP_INFO_HEADER}")
  if(NOT EXISTS "${required_path}")
    message(FATAL_ERROR "PSDK dependency tidak ditemukan: ${required_path}")
  endif()
endforeach()

file(SHA256 "${PSDK_LIB}" PSDK_LIBRARY_SHA256)
if(NOT PSDK_LIBRARY_SHA256 STREQUAL PSDK_316_AARCH64_LIBRARY_SHA256)
  message(FATAL_ERROR
    "libpayloadsdk.a bukan artefak aarch64 resmi PSDK 3.16.0")
endif()

add_executable(gap_plot_ai
  src/psdk/main.cpp
  "${PSDK_COMMON}/osal/osal.c"
  "${PSDK_COMMON}/osal/osal_fs.c"
  "${PSDK_COMMON}/osal/osal_socket.c"
  "${PSDK_HAL}/hal_usb_bulk.c"
)
target_compile_features(gap_plot_ai PRIVATE cxx_std_17)
target_compile_definitions(gap_plot_ai PRIVATE
  SYSTEM_ARCH_LINUX=1
  PLATFORM_ARCH_AARCH64=1
  _GNU_SOURCE
  GAP_PLOT_AI_COMPILED_APP_INFO=1
)
target_include_directories(gap_plot_ai PRIVATE
  include
  "${GAP_PLOT_AI_APP_INFO_DIR}"
  "${PSDK_INCLUDE}"
  "${PSDK_COMMON}"
  "${PSDK_HAL}"
)
target_link_libraries(gap_plot_ai PRIVATE "${PSDK_LIB}" pthread dl m stdc++)
set_target_properties(gap_plot_ai PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

message(STATUS "Gap Plot AI target: Linux ${CMAKE_SYSTEM_PROCESSOR}, C++17")
message(STATUS "PSDK library: ${PSDK_LIB}")
message(STATUS
  "OpenCV: not linked by native frontend; Python cv2 must select one target installation")

install(TARGETS gap_plot_ai RUNTIME DESTINATION bin)
install(DIRECTORY config/widget DESTINATION share/gap_plot_ai)
