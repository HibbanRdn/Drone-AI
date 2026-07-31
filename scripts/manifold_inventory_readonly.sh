#!/usr/bin/env bash
set -u

echo "# identity"
hostname
uname -a
uname -m
sed -n '1,20p' /etc/os-release 2>/dev/null
date -Is
if [[ -r /proc/device-tree/model ]]; then
  printf 'device_tree_model='
  tr -d '\0' < /proc/device-tree/model
  printf '\n'
else
  echo "device_tree_model=unavailable"
fi

echo "# resources"
df -h / 2>&1 || true
df -Pk / 2>&1 || true
free -h 2>&1 || true

echo "# toolchain"
command -v python3 2>/dev/null || true
python3 --version 2>&1 || true
command -v cc 2>/dev/null || true
cc --version 2>&1 | head -n 1 || true
command -v c++ 2>/dev/null || true
c++ --version 2>&1 | head -n 1 || true
command -v cmake 2>/dev/null || true
cmake --version 2>&1 | head -n 1 || true

echo "# cuda"
nvcc_on_path="$(command -v nvcc 2>/dev/null || true)"
echo "nvcc_on_path=${nvcc_on_path:-unavailable}"
cuda_default="$(readlink -f /usr/local/cuda 2>/dev/null || true)"
echo "usr_local_cuda=${cuda_default:-unavailable}"
for nvcc_candidate in /usr/local/cuda/bin/nvcc /usr/local/cuda-11.4/bin/nvcc; do
  if [[ -x "${nvcc_candidate}" ]]; then
    echo "nvcc_candidate=${nvcc_candidate}"
    "${nvcc_candidate}" --version 2>&1 | tail -n 5 || true
  else
    echo "nvcc_candidate_missing=${nvcc_candidate}"
  fi
done

echo "# tensorrt"
command -v trtexec 2>/dev/null || true
dpkg-query -W -f='${Package}\t${Version}\n' \
  libnvinfer8 libnvinfer-plugin8 libnvonnxparsers8 2>/dev/null || true
for tensorrt_header in \
  /usr/include/aarch64-linux-gnu/NvInferVersion.h \
  /usr/include/NvInferVersion.h; do
  if [[ -r "${tensorrt_header}" ]]; then
    echo "tensorrt_version_header=${tensorrt_header}"
    grep -E '^#define NV_TENSORRT_(MAJOR|MINOR|PATCH|BUILD)' \
      "${tensorrt_header}" 2>/dev/null || true
    break
  fi
done

echo "# libraries"
ldconfig -p 2>/dev/null | grep -E 'libcudnn|libnvinfer' | head -n 30 || true
ldconfig -p 2>/dev/null | grep -E 'libopencv_core' | head -n 20 || true
if command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -version 2>&1 | head -n 3 || true
else
  echo "ffmpeg=unavailable"
fi
if command -v gst-launch-1.0 >/dev/null 2>&1; then
  gst-launch-1.0 --version 2>&1 | head -n 3 || true
else
  echo "gstreamer=unavailable"
fi
if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query -W 2>/dev/null \
    | grep -E 'cuda|cudnn|tensorrt|nvinfer|opencv|gstreamer|ffmpeg|payload|dji' \
    | head -n 80 || true
else
  echo "dpkg_query=unavailable"
fi

echo "# opencv pkg-config"
command -v pkg-config 2>/dev/null || true
for opencv_pc in opencv4 opencv; do
  if pkg-config --exists "${opencv_pc}" 2>/dev/null; then
    echo "opencv_pkg_config=${opencv_pc}"
    echo "opencv_version=$(pkg-config --modversion "${opencv_pc}" 2>/dev/null)"
    echo "opencv_libdir=$(pkg-config --variable=libdir "${opencv_pc}" 2>/dev/null)"
    echo "opencv_includedir=$(pkg-config --variable=includedir "${opencv_pc}" 2>/dev/null)"
  else
    echo "opencv_pkg_config_missing=${opencv_pc}"
  fi
done

echo "# python packages"
python3 - <<'PY' 2>&1 || true
import importlib

for package_name in (
    "yaml", "numpy", "cv2", "onnx", "tensorrt", "torch", "ultralytics"
):
    try:
        module = importlib.import_module(package_name)
        version = getattr(module, "__version__", "unknown")
        location = getattr(module, "__file__", "built-in")
        print("{}\tversion={}\tpath={}".format(package_name, version, location))
    except Exception as error:
        print("{}\tunavailable\t{}: {}".format(
            package_name, type(error).__name__, error
        ))
PY

echo "# nvidia/platform"
if [[ -r /etc/nv_tegra_release ]]; then
  head -n 3 /etc/nv_tegra_release
else
  echo "l4t_release=unavailable"
fi
if [[ -r /etc/nvpower_model.conf ]]; then
  head -n 8 /etc/nvpower_model.conf
else
  echo "nvpmodel_config=unavailable"
fi
command -v tegrastats 2>/dev/null || true
command -v nvpmodel 2>/dev/null || true
dji_app_ctl_path="$(command -v dji_app_ctl 2>/dev/null || true)"
echo "dji_app_ctl=${dji_app_ctl_path:-unavailable}"

echo "# network names and addresses (MAC omitted)"
ip -brief address 2>/dev/null | sed -E 's/[[:xdigit:]]{2}(:[[:xdigit:]]{2}){5}//g'

echo "# usb identifiers (serial fields omitted)"
lsusb 2>/dev/null | sed -E 's/ iSerial +[0-9]+ .*$//'

echo "# dji app tool read-only"
if [[ -n "${dji_app_ctl_path}" ]]; then
  "${dji_app_ctl_path}" list 2>&1 || true
  "${dji_app_ctl_path}" status 2>&1 || true
else
  echo "dji_app_ctl unavailable; list/status skipped"
fi
