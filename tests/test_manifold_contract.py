from __future__ import annotations

import json
import re
from pathlib import Path


APP_ROOT = Path(__file__).parents[1]


def test_dpk_identity_and_binary_match_native_source() -> None:
    app_json = json.loads(
        (APP_ROOT / "dpk/app.json.in").read_text(encoding="utf-8")
    )
    assert app_json["user_app_id"] == "189927"
    assert app_json["firmware_version"] == "00.01.00.00"
    assert app_json["platform"] == "manifold3"
    assert app_json["name"]["name_en"] == "ggp-drone-ai"
    assert app_json["bin"] == "bin/gap_plot_ai_launcher"

    source = (APP_ROOT / "src/psdk/main.cpp").read_text(encoding="utf-8")
    expected_assignments = {
        "majorVersion": 0,
        "minorVersion": 1,
        "debugVersion": 0,
        "modifyVersion": 0,
    }
    for field, value in expected_assignments.items():
        assert re.search(
            r"version\.{}\s*=\s*{};".format(field, value), source
        )


def test_cmake_contract_matches_manifold_inventory() -> None:
    cmake = (APP_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "cmake_minimum_required(VERSION 3.16)" in cmake
    assert "project(gap_plot_ai LANGUAGES C CXX)" in cmake
    assert "aarch64-linux-gnu-gcc/libpayloadsdk.a" in cmake
    assert "target_compile_features(gap_plot_ai PRIVATE cxx_std_17)" in cmake
    assert 'DEFINED ENV{PSDK_ROOT}' in cmake
    assert "config/dji_sdk_app_info.local.h" in cmake


def test_only_placeholder_psdk_identity_is_tracked() -> None:
    example = (APP_ROOT / "config/dji_sdk_app_info.example.h").read_text(
        encoding="utf-8"
    )
    assert "REPLACE_WITH" in example
    assert not (APP_ROOT / "config/dji_sdk_app_info.h").exists()
    ignored = (APP_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/dji_sdk_app_info.local.h" in ignored


def test_psdk_callback_only_replaces_latest_frame() -> None:
    source = (APP_ROOT / "src/psdk/main.cpp").read_text(encoding="utf-8")
    callback = source.split("void ImageCallback", 1)[1].split("void StartLiveview", 1)[0]
    assert "g_latestFrame = std::move(packet)" in callback
    assert "DjiLiveview_EncodeAFrameToH264" not in callback
    assert "DjiLiveview_SendAiMetaToPilot" not in callback
    assert "if (g_stop.load())" in callback
    assert "DrawLine" not in callback
    assert "model" not in callback.lower()


def test_psdk_signal_and_reconnect_contract_is_safe() -> None:
    source = (APP_ROOT / "src/psdk/main.cpp").read_text(encoding="utf-8")
    signal_handler = source.split("void SignalHandler", 1)[1].split(
        "void EnsureRuntimeDirectories", 1
    )[0]
    assert "g_signalStop = 1" in signal_handler
    assert "notify" not in signal_handler
    assert "StartImageSubscription" in source
    assert "g_reconnectIntervalNs" in source
    assert "H.264 fallback is not compiled" in source


def test_official_sample_build_does_not_dirty_vendor_checkout() -> None:
    script = (APP_ROOT / "scripts/build_psdk_sample.sh").read_text(encoding="utf-8")
    assert "${APP_ROOT}/build/official_psdk_sample_3.16.0" in script
    assert "${PSDK_ROOT}/build_manifold3_official" not in script
    assert "config/dji_sdk_app_info.local.h" in script
    assert "psdk_app_info.py" in script


def test_windows_deployer_verifies_new_package_without_network_installers() -> None:
    script = (APP_ROOT / "scripts/deploy_manifold_windows.ps1").read_text(
        encoding="utf-8"
    )
    assert '$env:OS -ne "Windows_NT"' in script
    assert "[string]$PackagePath" in script
    assert "verify_offline_archive.py" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "gap_plot_ai_offline_21f56625f908_20260803T090552Z" in script
    assert "BatchMode=yes" in script and "scp" in script
    assert "Invoke-WebRequest" not in script
    assert "sudo" not in script
    assert "s3" not in script.lower()


def test_dpk_build_uses_external_read_only_engine_directory() -> None:
    script = (APP_ROOT / "scripts/build_dpk.sh").read_text(encoding="utf-8")
    assert "/home/dji/gap_plot_ai_assets/models/engine" in script
    assert "plant_detector.engine" not in script
    assert "mv \"${STAGING}/payload/models" not in script
    assert "staging must not contain TensorRT engines" in script


def test_legacy_worktree_deployer_is_fail_closed() -> None:
    script = (APP_ROOT / "scripts/deploy_dev.sh").read_text(encoding="utf-8")
    assert "Direct working-tree deployment is disabled" in script
    assert "ssh " not in script and "scp " not in script
    assert "tar -" not in script
