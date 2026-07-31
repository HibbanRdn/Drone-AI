from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/psdk_credentials.py"


def _write_secret(path: Path, *, developer_account: str) -> None:
    path.write_text(
        "\n".join(
            (
                "DJI_APP_NAME=ggp-drone-ai",
                "DJI_APP_ID=189927",
                "DJI_APP_KEY=0123456789abcdef0123456789abcdef",
                "DJI_APP_LICENSE=VEVTVF9MSUNFTNF",
                f"DJI_DEVELOPER_ACCOUNT={developer_account}",
                "DJI_BAUD_RATE=460800",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_redacted_validation_prints_only_boolean_status(tmp_path: Path) -> None:
    secret = tmp_path / "secrets.env"
    _write_secret(secret, developer_account="")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "validate", "--secrets", str(secret)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "app_id_configured=true",
        "app_key_configured=true",
        "app_license_configured=true",
    ]
    assert "0123456789abcdef" not in result.stdout
    assert "VEVTVF9" not in result.stdout


def test_generated_header_uses_official_macros_and_mode_600(tmp_path: Path) -> None:
    secret = tmp_path / "secrets.env"
    output = tmp_path / "generated/dji_sdk_app_info.generated.h"
    _write_secret(secret, developer_account="test@example.invalid")
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "generate",
            "--secrets",
            str(secret),
            "--output",
            str(output),
            "--require-runtime",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    content = output.read_text(encoding="utf-8")
    for macro in (
        "USER_APP_NAME",
        "USER_APP_ID",
        "USER_APP_KEY",
        "USER_APP_LICENSE",
        "USER_DEVELOPER_ACCOUNT",
        "USER_BAUD_RATE",
    ):
        assert f"#define {macro} " in content
    assert os.stat(output).st_mode & 0o077 == 0


def test_runtime_generation_identifies_missing_developer_account(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secrets.env"
    output = tmp_path / "generated.h"
    _write_secret(secret, developer_account="")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "generate",
            "--secrets",
            str(secret),
            "--output",
            str(output),
            "--require-runtime",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DJI_DEVELOPER_ACCOUNT is required" in result.stderr
    assert not output.exists()


def test_validation_rejects_app_id_that_differs_from_app_json(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secrets.env"
    _write_secret(secret, developer_account="")
    secret.write_text(
        secret.read_text(encoding="utf-8").replace(
            "DJI_APP_ID=189927", "DJI_APP_ID=123456"
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "validate", "--secrets", str(secret)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DJI_APP_ID does not match app.json" in result.stderr
