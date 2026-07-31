#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Dict


PORTAL_REQUIRED = (
    "DJI_APP_NAME",
    "DJI_APP_ID",
    "DJI_APP_KEY",
    "DJI_APP_LICENSE",
)
RUNTIME_REQUIRED = PORTAL_REQUIRED + (
    "DJI_DEVELOPER_ACCOUNT",
    "DJI_BAUD_RATE",
)
LIMITS = {
    "DJI_APP_NAME": (31, True),
    "DJI_APP_ID": (16, False),
    "DJI_APP_KEY": (32, False),
    "DJI_APP_LICENSE": (512, False),
    "DJI_DEVELOPER_ACCOUNT": (63, True),
    "DJI_BAUD_RATE": (7, False),
}
EXPECTED_APP_NAME = "ggp-drone-ai"
EXPECTED_APP_ID = "189927"


class CredentialError(ValueError):
    pass


def read_env(path: Path) -> Dict[str, str]:
    if not path.is_file():
        raise CredentialError(f"credential file not found: {path}")
    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions & 0o077:
        raise CredentialError("credential file permissions must be 600")
    values: Dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise CredentialError(f"invalid credential line {line_number}")
        if key in values:
            raise CredentialError(f"duplicate credential field: {key}")
        values[key] = value
    return values


def validate(values: Dict[str, str], *, require_runtime: bool) -> None:
    required = RUNTIME_REQUIRED if require_runtime else PORTAL_REQUIRED
    for key in required:
        if not values.get(key):
            raise CredentialError(f"{key} is required")
    for key, (limit, _nul_terminated) in LIMITS.items():
        value = values.get(key, "")
        if len(value.encode("utf-8")) > limit:
            raise CredentialError(f"{key} exceeds PSDK buffer limit")
        if any(character.isspace() for character in value):
            raise CredentialError(f"{key} must not contain whitespace")
        if value and not value.isascii():
            raise CredentialError(f"{key} must be ASCII")
    if values.get("DJI_APP_ID") and not values["DJI_APP_ID"].isdigit():
        raise CredentialError("DJI_APP_ID must contain digits only")
    if values.get("DJI_APP_ID") != EXPECTED_APP_ID:
        raise CredentialError("DJI_APP_ID does not match app.json")
    if values.get("DJI_APP_NAME") != EXPECTED_APP_NAME:
        raise CredentialError("DJI_APP_NAME does not match the agreed app identity")
    if values.get("DJI_APP_KEY") and not re.fullmatch(
        r"[0-9A-Fa-f]+", values["DJI_APP_KEY"]
    ):
        raise CredentialError("DJI_APP_KEY has an unexpected character")
    if values.get("DJI_APP_LICENSE") and not re.fullmatch(
        r"[A-Za-z0-9+/=]+", values["DJI_APP_LICENSE"]
    ):
        raise CredentialError("DJI_APP_LICENSE has an unexpected character")
    if values.get("DJI_BAUD_RATE") and not values["DJI_BAUD_RATE"].isdigit():
        raise CredentialError("DJI_BAUD_RATE must contain digits only")


def print_redacted_status(values: Dict[str, str]) -> None:
    print(f"app_id_configured={str(bool(values.get('DJI_APP_ID'))).lower()}")
    print(f"app_key_configured={str(bool(values.get('DJI_APP_KEY'))).lower()}")
    print(
        "app_license_configured="
        f"{str(bool(values.get('DJI_APP_LICENSE'))).lower()}"
    )


def c_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def generate_header(values: Dict[str, str], output: Path) -> None:
    macros = (
        ("USER_APP_NAME", "DJI_APP_NAME"),
        ("USER_APP_ID", "DJI_APP_ID"),
        ("USER_APP_KEY", "DJI_APP_KEY"),
        ("USER_APP_LICENSE", "DJI_APP_LICENSE"),
        ("USER_DEVELOPER_ACCOUNT", "DJI_DEVELOPER_ACCOUNT"),
        ("USER_BAUD_RATE", "DJI_BAUD_RATE"),
    )
    lines = [
        "#ifndef DJI_SDK_APP_INFO_H",
        "#define DJI_SDK_APP_INFO_H",
        "",
        "/* Generated from a local ignored credential file. Do not stage. */",
    ]
    lines.extend(
        f'#define {macro} "{c_string(values[field])}"'
        for macro, field in macros
    )
    lines.extend(("", "#endif", ""))
    output.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as target:
            target.write("\n".join(lines))
        os.replace(temporary_name, output)
        output.chmod(0o600)
    except Exception:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        Path(temporary_name).unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate/generate redacted PSDK application identity"
    )
    parser.add_argument("command", choices=("validate", "generate"))
    parser.add_argument("--secrets", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-runtime", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        values = read_env(args.secrets.expanduser().resolve())
        validate(values, require_runtime=args.require_runtime)
        if args.command == "validate":
            print_redacted_status(values)
        else:
            if args.output is None:
                raise CredentialError("--output is required for generate")
            if not args.require_runtime:
                raise CredentialError("generate requires --require-runtime")
            generate_header(values, args.output.expanduser().resolve())
    except CredentialError as error:
        raise SystemExit(f"credential validation failed: {error}") from None


if __name__ == "__main__":
    main()
