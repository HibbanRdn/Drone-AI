#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import re
from pathlib import Path
from typing import Dict


MACROS = {
    "USER_APP_NAME": (31, "DJI application name"),
    "USER_APP_ID": (16, "DJI application ID"),
    "USER_APP_KEY": (32, "DJI application key"),
    "USER_APP_LICENSE": (512, "DJI advanced license"),
    "USER_DEVELOPER_ACCOUNT": (63, "DJI developer account"),
    "USER_BAUD_RATE": (7, "DJI baud rate"),
}
EXPECTED_APP_NAME = "ggp-drone-ai"
EXPECTED_APP_ID = "189927"


class AppInfoError(ValueError):
    pass


def read_header(path: Path) -> Dict[str, str]:
    if not path.is_file():
        raise AppInfoError("PSDK app information file not found: {}".format(path))
    values: Dict[str, str] = {}
    pattern = re.compile(r'^\s*#define\s+(USER_[A-Z_]+)\s+"([^"]*)"\s*$')
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = pattern.match(line)
        if match and match.group(1) in MACROS:
            values[match.group(1)] = match.group(2)
    return values


def validate(values: Dict[str, str]) -> None:
    for macro, (limit, label) in MACROS.items():
        value = values.get(macro, "")
        if not value:
            raise AppInfoError("{} is missing".format(label))
        upper = value.upper()
        if "REPLACE" in upper or "YOUR_" in upper or "PLACEHOLDER" in upper:
            raise AppInfoError("{} still contains a placeholder".format(label))
        if not value.isascii() or any(character.isspace() for character in value):
            raise AppInfoError("{} must be non-whitespace ASCII".format(label))
        if len(value.encode("ascii")) > limit:
            raise AppInfoError("{} exceeds the PSDK buffer limit".format(label))
    if values["USER_APP_NAME"] != EXPECTED_APP_NAME:
        raise AppInfoError("application name does not match app.json")
    if values["USER_APP_ID"] != EXPECTED_APP_ID:
        raise AppInfoError("application ID does not match app.json")
    if not values["USER_APP_ID"].isdigit():
        raise AppInfoError("application ID must contain digits only")
    if not re.fullmatch(r"[0-9A-Fa-f]+", values["USER_APP_KEY"]):
        raise AppInfoError("application key has an unexpected character")
    try:
        base64.b64decode(values["USER_APP_LICENSE"], validate=True)
    except (binascii.Error, ValueError) as error:
        raise AppInfoError("advanced license is not valid base64") from error
    if "@" not in values["USER_DEVELOPER_ACCOUNT"]:
        raise AppInfoError("developer account must be the portal email address")
    if not values["USER_BAUD_RATE"].isdigit():
        raise AppInfoError("baud rate must contain digits only")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a local DJI PSDK application information header"
    )
    parser.add_argument("header", type=Path)
    args = parser.parse_args()
    try:
        values = read_header(args.header.expanduser().resolve())
        validate(values)
    except AppInfoError as error:
        raise SystemExit("PSDK app information validation failed: {}".format(error))
    print("psdk_app_info_valid=true")
    print("psdk_app_id={}".format(EXPECTED_APP_ID))
    print("sensitive_values_redacted=true")


if __name__ == "__main__":
    main()
