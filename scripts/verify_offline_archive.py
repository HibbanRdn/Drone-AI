#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, Optional, Tuple


AUDITED_WHEEL_SHA256 = {
    "pyyaml-6.0.2-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl":
        "d7fded462629cfa4b685c5416b949ebad6cec74af5e2d42905d41e257e0869f5",
}


class ArchiveError(ValueError):
    pass


LINUX_TEXT_SUFFIXES = {
    ".c", ".cc", ".cmake", ".cpp", ".h", ".hpp", ".in", ".json",
    ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise ArchiveError("archive path is empty or uses a backslash")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ArchiveError("unsafe archive path: {}".format(name))
    if re.match(r"^[A-Za-z]:", name):
        raise ArchiveError("Windows absolute archive path: {}".format(name))
    return path


def wheel_compatibility_error(filename: str) -> Optional[str]:
    if not filename.lower().endswith(".whl"):
        return None
    parts = Path(filename).name[:-4].split("-")
    if len(parts) < 5:
        return "malformed wheel filename"
    python_tag, _abi_tag, platform_tag = parts[-3:]
    lower_platform = platform_tag.lower()
    lower_python = python_tag.lower()
    if lower_python == "py3" and lower_platform == "any":
        return None
    if lower_python not in {"cp38", "py3"}:
        return "wheel Python tag is not compatible with Python 3.8"
    forbidden = ("win", "macosx", "x86_64", "amd64", "i686")
    if any(token in lower_platform for token in forbidden):
        return "wheel platform is not Linux aarch64"
    if lower_platform != "any" and not any(
        token in lower_platform for token in ("aarch64", "arm64")
    ):
        return "wheel platform is not Linux aarch64"
    return None


def _validate_shell(name: str, mode: int, data: bytes) -> None:
    if data.startswith(b"\xef\xbb\xbf"):
        raise ArchiveError("executable has UTF-8 BOM: {}".format(name))
    if b"\r" in data:
        raise ArchiveError("shell script is not LF-only: {}".format(name))
    if mode & 0o111 == 0:
        raise ArchiveError("shell script is not executable: {}".format(name))
    if not (
        data.startswith(b"#!/usr/bin/env bash\n")
        or data.startswith(b"#!/bin/bash\n")
    ):
        raise ArchiveError("unsupported shell shebang: {}".format(name))


def _validate_linux_text(name: str, data: bytes) -> None:
    if data.startswith(b"\xef\xbb\xbf"):
        raise ArchiveError("Linux text file has UTF-8 BOM: {}".format(name))
    if b"\r" in data:
        raise ArchiveError("Linux text file is not LF-only: {}".format(name))


def _validate_powershell(name: str, data: bytes) -> None:
    if data.startswith(b"\xef\xbb\xbf"):
        raise ArchiveError("PowerShell file has UTF-8 BOM: {}".format(name))
    remainder = data.replace(b"\r\n", b"")
    if b"\r" in remainder or b"\n" in remainder:
        raise ArchiveError("PowerShell file is not CRLF-normalized: {}".format(name))


def _regular_members(source: tarfile.TarFile) -> Dict[str, Tuple[tarfile.TarInfo, bytes]]:
    regular: Dict[str, Tuple[tarfile.TarInfo, bytes]] = {}
    seen = set()
    for member in source.getmembers():
        safe_member_path(member.name)
        if member.name in seen:
            raise ArchiveError("archive contains a duplicate member: {}".format(member.name))
        seen.add(member.name)
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ArchiveError("archive contains a link/device entry: {}".format(member.name))
        if not member.isfile() and not member.isdir():
            raise ArchiveError("archive contains unsupported entry: {}".format(member.name))
        if member.isfile():
            extracted = source.extractfile(member)
            if extracted is None:
                raise ArchiveError("cannot read archive member: {}".format(member.name))
            data = extracted.read()
            regular[member.name] = (member, data)
            lower = member.name.lower()
            suffix = PurePosixPath(member.name).suffix.lower()
            if suffix in LINUX_TEXT_SUFFIXES or PurePosixPath(member.name).name == "CMakeLists.txt":
                _validate_linux_text(member.name, data)
            elif suffix == ".ps1":
                _validate_powershell(member.name, data)
            if lower.endswith(".sh"):
                _validate_shell(member.name, member.mode, data)
            wheel_error = wheel_compatibility_error(member.name)
            if wheel_error:
                raise ArchiveError("{}: {}".format(wheel_error, member.name))
            wheel_name = PurePosixPath(member.name).name.lower()
            if wheel_name.startswith("pyyaml-6.0.2-"):
                expected = AUDITED_WHEEL_SHA256.get(wheel_name)
                if expected is None or sha256_bytes(data) != expected:
                    raise ArchiveError("PyYAML wheel is not the audited target artifact")
            parts = {part.lower() for part in PurePosixPath(member.name).parts}
            if ".venv" in parts or "site-packages" in parts:
                raise ArchiveError("archive contains a virtual environment: {}".format(member.name))
    return regular


def _validate_source_tar(name: str, data: bytes) -> None:
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as nested:
            members = _regular_members(nested)
    except tarfile.TarError as error:
        raise ArchiveError("invalid nested source tar: {}".format(name)) from error
    roots = {PurePosixPath(member_name).parts[0] for member_name in members}
    if roots != {"Drone-AI"}:
        raise ArchiveError("application source archive root must be Drone-AI")
    forbidden = (
        "config/dji_sdk_app_info.local.h",
        "config/dji_sdk_app_info.h",
        "config/secrets.env",
    )
    for member_name in members:
        parts = PurePosixPath(member_name).parts
        if any(part.lower() in {".git", ".venv", "site-packages"} for part in parts):
            raise ArchiveError("source archive contains Git history or an environment")
        normalized = "/".join(parts[1:]).lower()
        if normalized in forbidden:
            raise ArchiveError("source archive contains credential material")
        if PurePosixPath(member_name).suffix.lower() in {
            ".engine", ".onnx", ".pt", ".mp4", ".mov"
        }:
            raise ArchiveError("source archive contains a model or media artifact")


def _validate_app_info(data: bytes) -> None:
    text = data.decode("utf-8-sig")
    required = (
        "USER_APP_NAME",
        "USER_APP_ID",
        "USER_APP_KEY",
        "USER_APP_LICENSE",
        "USER_DEVELOPER_ACCOUNT",
        "USER_BAUD_RATE",
    )
    values = {}
    for macro in required:
        match = re.search(r'^\s*#define\s+{}\s+"([^"]+)"\s*$'.format(macro), text, re.M)
        if match is None:
            raise ArchiveError("staged PSDK application information is incomplete")
        upper = match.group(1).upper()
        if "REPLACE" in upper or "PLACEHOLDER" in upper or "YOUR_" in upper:
            raise ArchiveError("staged PSDK application information has placeholders")
        values[macro] = match.group(1)
    if values["USER_APP_NAME"] != "ggp-drone-ai" or values["USER_APP_ID"] != "189927":
        raise ArchiveError("staged PSDK application identity does not match app.json")
    if not re.fullmatch(r"[0-9A-Fa-f]+", values["USER_APP_KEY"]):
        raise ArchiveError("staged PSDK application key format is invalid")
    try:
        base64.b64decode(values["USER_APP_LICENSE"], validate=True)
    except (binascii.Error, ValueError) as error:
        raise ArchiveError("staged PSDK license is not valid base64") from error
    if "@" not in values["USER_DEVELOPER_ACCOUNT"]:
        raise ArchiveError("staged PSDK developer account format is invalid")
    if not values["USER_BAUD_RATE"].isdigit():
        raise ArchiveError("staged PSDK baud rate format is invalid")


def _parse_sidecar(sidecar: Path, archive: Path) -> None:
    data = sidecar.read_bytes()
    if data.startswith(b"\xef\xbb\xbf") or b"\r" in data:
        raise ArchiveError("outer checksum must be LF-only without BOM")
    lines = data.decode("ascii").splitlines()
    if len(lines) != 1:
        raise ArchiveError("outer checksum must contain exactly one line")
    expected, separator, filename = lines[0].partition("  ")
    if not separator or filename != archive.name or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ArchiveError("outer checksum format is invalid")
    if sha256_file(archive) != expected:
        raise ArchiveError("outer SHA-256 mismatch")


def verify_archive(archive: Path, sidecar: Optional[Path] = None) -> Dict[str, object]:
    archive = archive.expanduser().resolve()
    if sidecar is None:
        sidecar = archive.with_name(archive.name + ".sha256")
    if not archive.is_file() or not sidecar.is_file():
        raise ArchiveError("archive or outer checksum is missing")
    _parse_sidecar(sidecar, archive)
    try:
        with tarfile.open(str(archive), "r:gz") as source:
            members = _regular_members(source)
    except tarfile.TarError as error:
        raise ArchiveError("offline package is not a valid tar.gz") from error

    roots = {PurePosixPath(name).parts[0] for name in members}
    if len(roots) != 1:
        raise ArchiveError("offline package must have one top-level directory")
    root = next(iter(roots))
    checksum_name = "{}/SHA256SUMS".format(root)
    manifest_name = "{}/manifest.json".format(root)
    credential_name = "{}/credentials/dji_sdk_app_info.local.h".format(root)
    source_name = "{}/sources/Drone-AI-source.tar".format(root)
    for required in (checksum_name, manifest_name, credential_name, source_name):
        if required not in members:
            raise ArchiveError("required package member missing: {}".format(required))

    checksum_data = members[checksum_name][1]
    if checksum_data.startswith(b"\xef\xbb\xbf") or b"\r" in checksum_data:
        raise ArchiveError("internal checksum must be LF-only without BOM")
    expected_paths = set()
    for line in checksum_data.decode("ascii").splitlines():
        expected, separator, relative = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ArchiveError("malformed internal checksum line")
        safe_member_path(relative)
        full_name = "{}/{}".format(root, relative)
        if full_name not in members or sha256_bytes(members[full_name][1]) != expected:
            raise ArchiveError("internal checksum mismatch: {}".format(relative))
        expected_paths.add(full_name)
    actual_paths = set(members) - {checksum_name}
    if expected_paths != actual_paths:
        raise ArchiveError("internal checksum inventory is incomplete")

    manifest = json.loads(members[manifest_name][1].decode("utf-8"))
    if manifest.get("schema_version") != "gap-plot-ai-offline-package/v2":
        raise ArchiveError("unsupported package manifest schema")
    if manifest.get("application", {}).get("history_included") is not False:
        raise ArchiveError("application Git history must not be included")
    wheel_names = manifest.get("offline_wheels")
    if not isinstance(wheel_names, list):
        raise ArchiveError("offline wheel inventory is missing")
    for audited_name in AUDITED_WHEEL_SHA256:
        matching = [name for name in wheel_names if str(name).lower() == audited_name]
        member_name = (
            "{}/offline_wheels/{}".format(root, matching[0])
            if len(matching) == 1
            else ""
        )
        if not member_name or member_name not in members:
            raise ArchiveError("audited PyYAML wheel is missing from the package")
    credential_mode = members[credential_name][0].mode
    if credential_mode & 0o077:
        raise ArchiveError("staged credential permissions must be 0600")
    _validate_app_info(members[credential_name][1])
    _validate_source_tar(source_name, members[source_name][1])
    return {
        "archive": str(archive),
        "sha256": sha256_file(archive),
        "regular_files": len(members),
        "application_commit": manifest.get("application", {}).get("commit"),
        "psdk_commit": manifest.get("payload_sdk", {}).get("commit"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify an offline Drone-AI deployment tar without extracting it"
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sidecar", type=Path)
    args = parser.parse_args()
    try:
        result = verify_archive(args.archive, args.sidecar)
    except (ArchiveError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SystemExit("offline archive verification failed: {}".format(error))
    print("offline_archive_valid=true")
    print("application_commit={}".format(result["application_commit"]))
    print("psdk_commit={}".format(result["psdk_commit"]))
    print("regular_files={}".format(result["regular_files"]))


if __name__ == "__main__":
    main()
