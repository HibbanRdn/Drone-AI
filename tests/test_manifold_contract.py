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
    assert app_json["bin"] == "bin/gap_plot_ai"

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
