from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict


class ManifestError(ValueError):
    pass


def validate_manifest(manifest: Dict[str, Any], staging_root: Path) -> None:
    required = {
        "user_app_id",
        "firmware_version",
        "is_ai_rendering",
        "platform",
        "name",
        "description",
        "maintainer",
        "ver_min",
        "ver_max",
        "bin",
        "userconfig",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ManifestError(f"DPK manifest fields hilang: {', '.join(missing)}")
    if manifest["platform"] != "manifold3":
        raise ManifestError("DPK platform harus manifold3")
    if manifest["user_app_id"] != "189927":
        raise ManifestError("DPK user_app_id tidak cocok dengan aplikasi DJI")
    version_pattern = re.compile(r"^(?:[0-9]{1,2}\.){3}[0-9]{1,2}$")
    for key in ("firmware_version", "ver_min", "ver_max"):
        value = str(manifest[key])
        if "@" in value or not version_pattern.fullmatch(value):
            raise ManifestError(f"DPK {key} belum valid untuk firmware Manifold")
    if manifest["is_ai_rendering"] != "true":
        raise ManifestError("DPK is_ai_rendering harus string 'true'")
    binary_relative = Path(str(manifest["bin"]))
    if binary_relative.is_absolute() or ".." in binary_relative.parts:
        raise ManifestError("DPK bin harus path relatif aman")
    binary = staging_root / binary_relative
    if not binary.is_file():
        raise ManifestError(f"DPK binary tidak ditemukan: {binary}")
    userconfig = manifest["userconfig"]
    if not isinstance(userconfig, list) or not userconfig:
        raise ManifestError("DPK userconfig harus list non-empty")
    for value in userconfig:
        relative = Path(str(value))
        if relative.is_absolute() or ".." in relative.parts:
            raise ManifestError("DPK userconfig harus path relatif aman")
        if not (staging_root / relative).exists():
            raise ManifestError(f"DPK userconfig tidak ditemukan: {relative}")
    forbidden_names = {
        "secrets.env",
        "dji_sdk_app_info.generated.h",
        "credentials.json",
        ".env",
    }
    forbidden = [
        path for path in staging_root.rglob("*") if path.is_file() and path.name in forbidden_names
    ]
    if forbidden:
        raise ManifestError(
            "Credential dilarang masuk staging: "
            + ", ".join(str(path) for path in forbidden)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate prepared DJI DPK staging")
    parser.add_argument("manifest")
    parser.add_argument("--staging-root")
    args = parser.parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    staging_root = (
        Path(args.staging_root).expanduser().resolve()
        if args.staging_root
        else manifest_path.parent
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest, staging_root)
    print(f"dpk_manifest_valid={manifest_path}")


if __name__ == "__main__":
    main()
