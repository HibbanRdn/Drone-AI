#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

from verify_offline_archive import (
    AUDITED_WHEEL_SHA256,
    ArchiveError,
    _validate_app_info,
    _validate_linux_text,
    _validate_powershell,
    _validate_shell,
    _validate_source_tar,
    LINUX_TEXT_SUFFIXES,
    wheel_compatibility_error,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    package_root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    checksum_file = package_root / "SHA256SUMS"
    if not checksum_file.is_file():
        raise SystemExit("SHA256SUMS not found")
    checksum_bytes = checksum_file.read_bytes()
    if checksum_bytes.startswith(b"\xef\xbb\xbf") or b"\r" in checksum_bytes:
        raise SystemExit("SHA256SUMS must be LF-only without BOM")
    expected_paths = set()
    checked = 0
    for line in checksum_bytes.decode("ascii").splitlines():
        expected, separator, relative = line.partition("  ")
        path = PurePosixPath(relative)
        if not separator or path.is_absolute() or ".." in path.parts:
            raise SystemExit("Malformed or unsafe SHA256SUMS line")
        target = package_root / Path(*path.parts)
        if not target.is_file() or sha256(target) != expected:
            raise SystemExit("Checksum mismatch: {}".format(relative))
        expected_paths.add(target.resolve())
        checked += 1
    actual_paths = {
        path.resolve()
        for path in package_root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if expected_paths != actual_paths:
        raise SystemExit("SHA256SUMS inventory is incomplete")

    try:
        for path in sorted(actual_paths):
            relative = path.relative_to(package_root).as_posix()
            if (
                path.suffix.lower() in LINUX_TEXT_SUFFIXES
                or path.name == "CMakeLists.txt"
            ):
                _validate_linux_text(relative, path.read_bytes())
            elif path.suffix.lower() == ".ps1":
                _validate_powershell(relative, path.read_bytes())
            if path.suffix.lower() == ".sh":
                _validate_shell(
                    relative,
                    stat.S_IMODE(path.stat().st_mode),
                    path.read_bytes(),
                )
            error = wheel_compatibility_error(path.name)
            if error:
                raise ArchiveError("{}: {}".format(error, relative))
        credential = package_root / "credentials/dji_sdk_app_info.local.h"
        source_archive = package_root / "sources/Drone-AI-source.tar"
        if not credential.is_file() or not source_archive.is_file():
            raise ArchiveError("credential or application source snapshot is missing")
        if stat.S_IMODE(credential.stat().st_mode) & 0o077:
            raise ArchiveError("staged credential permissions must be 0600")
        _validate_app_info(credential.read_bytes())
        _validate_source_tar(source_archive.name, source_archive.read_bytes())
        manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "gap-plot-ai-offline-package/v2":
            raise ArchiveError("unsupported package manifest schema")
        if manifest.get("application", {}).get("history_included") is not False:
            raise ArchiveError("application Git history must not be included")
        wheel_names = manifest.get("offline_wheels")
        if not isinstance(wheel_names, list):
            raise ArchiveError("offline wheel inventory is missing")
        for audited_name in AUDITED_WHEEL_SHA256:
            matching = [name for name in wheel_names if str(name).lower() == audited_name]
            target = (
                package_root / "offline_wheels" / str(matching[0])
                if len(matching) == 1
                else None
            )
            if target is None or not target.is_file():
                raise ArchiveError("audited PyYAML wheel is missing")
    except (ArchiveError, UnicodeError, json.JSONDecodeError) as error:
        raise SystemExit("Package validation failed: {}".format(error))
    print("package_checksums_ok={}".format(checked))
    print("package_linux_text_ok=true")
    print("package_credentials_redacted=true")


if __name__ == "__main__":
    main()
