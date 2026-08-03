from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).parents[1]
AUDITED_WHEEL_NAME = (
    "pyyaml-6.0.2-cp38-cp38-"
    "manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
)
AUDITED_WHEEL_BYTES = b"fixture-audited-wheel"
SPEC = importlib.util.spec_from_file_location(
    "verify_offline_archive",
    APP_ROOT / "scripts/verify_offline_archive.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture(autouse=True)
def _fixture_wheel_hash(monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE,
        "AUDITED_WHEEL_SHA256",
        {AUDITED_WHEEL_NAME: hashlib.sha256(AUDITED_WHEEL_BYTES).hexdigest()},
    )


def _source_tar() -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:") as archive:
        data = b"fixture\n"
        member = tarfile.TarInfo("Drone-AI/README.md")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def _credential() -> bytes:
    license_value = base64.b64encode(b"fixture-license").decode("ascii")
    return (
        '\n'.join(
            (
                '#define USER_APP_NAME "ggp-drone-ai"',
                '#define USER_APP_ID "189927"',
                '#define USER_APP_KEY "0123456789abcdef"',
                '#define USER_APP_LICENSE "{}"'.format(license_value),
                '#define USER_DEVELOPER_ACCOUNT "test@example.invalid"',
                '#define USER_BAUD_RATE "460800"',
                '',
            )
        )
    ).encode("utf-8")


def _package(
    tmp_path: Path,
    *,
    shell: bytes = b"#!/usr/bin/env bash\nset -euo pipefail\n",
    shell_mode: int = 0o755,
    checksum_override: str = "",
) -> Path:
    root = "gap_plot_ai_offline_fixture"
    files = {
        "manifest.json": (
            json.dumps(
                {
                    "schema_version": "gap-plot-ai-offline-package/v2",
                    "application": {"commit": "a" * 40, "history_included": False},
                    "payload_sdk": {"commit": "b" * 40},
                    "offline_wheels": [AUDITED_WHEEL_NAME],
                }
            )
            + "\n"
        ).encode("utf-8"),
        "credentials/dji_sdk_app_info.local.h": _credential(),
        "sources/Drone-AI-source.tar": _source_tar(),
        "install/install_offline.sh": shell,
        "offline_wheels/{}".format(AUDITED_WHEEL_NAME): AUDITED_WHEEL_BYTES,
    }
    checksums = []
    for name, data in sorted(files.items()):
        digest = hashlib.sha256(data).hexdigest()
        if checksum_override and name == "manifest.json":
            digest = checksum_override
        checksums.append("{}  {}".format(digest, name))
    files["SHA256SUMS"] = ("\n".join(checksums) + "\n").encode("ascii")

    archive_path = tmp_path / "fixture.tar.gz"
    with tarfile.open(str(archive_path), "w:gz") as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo("{}/{}".format(root, name))
            member.size = len(data)
            member.mode = shell_mode if name.endswith(".sh") else 0o600 if "credentials/" in name else 0o644
            archive.addfile(member, io.BytesIO(data))
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_name(archive_path.name + ".sha256").write_bytes(
        "{}  {}\n".format(digest, archive_path.name).encode("ascii")
    )
    return archive_path


def test_archive_accepts_normalized_complete_package(tmp_path: Path) -> None:
    result = MODULE.verify_archive(_package(tmp_path))
    assert result["application_commit"] == "a" * 40


@pytest.mark.parametrize(
    "name",
    ("../escape", "/absolute", "C:/windows", "folder\\windows"),
)
def test_archive_rejects_unsafe_member_paths(name: str) -> None:
    with pytest.raises(MODULE.ArchiveError):
        MODULE.safe_member_path(name)


def test_archive_rejects_crlf_shell(tmp_path: Path) -> None:
    archive = _package(tmp_path, shell=b"#!/usr/bin/env bash\r\nexit 0\r\n")
    with pytest.raises(MODULE.ArchiveError, match="LF-only"):
        MODULE.verify_archive(archive)


def test_archive_rejects_non_executable_shell(tmp_path: Path) -> None:
    archive = _package(tmp_path, shell_mode=0o644)
    with pytest.raises(MODULE.ArchiveError, match="not executable"):
        MODULE.verify_archive(archive)


def test_archive_rejects_internal_checksum_mismatch(tmp_path: Path) -> None:
    archive = _package(tmp_path, checksum_override="0" * 64)
    with pytest.raises(MODULE.ArchiveError, match="checksum mismatch"):
        MODULE.verify_archive(archive)


@pytest.mark.parametrize(
    "filename",
    (
        "pkg-1.0-cp38-cp38-win_amd64.whl",
        "pkg-1.0-cp38-cp38-macosx_11_0_arm64.whl",
        "pkg-1.0-cp38-cp38-manylinux2014_x86_64.whl",
        "pkg-1.0-cp311-cp311-manylinux2014_aarch64.whl",
    ),
)
def test_wheel_validator_rejects_wrong_target(filename: str) -> None:
    assert MODULE.wheel_compatibility_error(filename)


@pytest.mark.parametrize(
    "filename",
    (
        "PyYAML-6.0.2-cp38-cp38-manylinux2014_aarch64.whl",
        "fixture-1.0-py3-none-any.whl",
    ),
)
def test_wheel_validator_accepts_python38_linux_aarch64(filename: str) -> None:
    assert MODULE.wheel_compatibility_error(filename) is None
