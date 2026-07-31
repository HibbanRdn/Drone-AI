#!/usr/bin/env bash
set -u

echo "# identity"
hostname
uname -a
uname -m
sed -n '1,20p' /etc/os-release 2>/dev/null
date -Is

echo "# resources"
df -h /
free -h

echo "# toolchain"
python3 --version 2>&1
cc --version 2>&1 | head -n 1
c++ --version 2>&1 | head -n 1
cmake --version 2>&1 | head -n 1
nvcc --version 2>&1 | tail -n 4
trtexec --version 2>&1 | head -n 5

echo "# libraries"
ldconfig -p 2>/dev/null | grep -E 'libcudnn|libnvinfer|libopencv' | head -n 30
ffmpeg -version 2>&1 | head -n 3
gst-launch-1.0 --version 2>&1 | head -n 3
dpkg-query -W 2>/dev/null | grep -E 'cuda|cudnn|tensorrt|nvinfer|opencv|gstreamer|ffmpeg|payload|dji' | head -n 80

echo "# nvidia/platform"
test -r /etc/nv_tegra_release && head -n 3 /etc/nv_tegra_release
test -r /etc/nvpower_model.conf && head -n 8 /etc/nvpower_model.conf
command -v tegrastats 2>/dev/null
command -v nvpmodel 2>/dev/null
command -v dji_app_ctl 2>/dev/null

echo "# network names and addresses (MAC omitted)"
ip -brief address 2>/dev/null | sed -E 's/[[:xdigit:]]{2}(:[[:xdigit:]]{2}){5}//g'

echo "# usb identifiers (serial fields omitted)"
lsusb 2>/dev/null | sed -E 's/ iSerial +[0-9]+ .*$//'

echo "# dji app tool read-only"
dji_app_ctl list 2>&1
dji_app_ctl status 2>&1
