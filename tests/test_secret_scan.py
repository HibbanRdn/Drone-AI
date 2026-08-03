from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/scan_repository_secrets.py"
SPEC = importlib.util.spec_from_file_location("scan_repository_secrets", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_dji_template_is_not_a_secret() -> None:
    template = b'#define USER_APP_KEY "REPLACE_WITH_DJI_APP_KEY"\n'
    assert list(MODULE.scan_file("config/dji_sdk_app_info.example.h", template)) == []


def test_real_dji_value_is_reported_without_value() -> None:
    content = b'#define USER_APP_KEY "0123456789abcdef"\n'
    findings = list(MODULE.scan_file("config/header.h", content))
    assert findings == ["tracked_dji_credential"]
    assert "0123456789abcdef" not in findings


def test_forbidden_secret_paths_are_reported() -> None:
    assert MODULE.path_finding("config/dji_sdk_app_info.local.h")
    assert MODULE.path_finding("credentials.json")
