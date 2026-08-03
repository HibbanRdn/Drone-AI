#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Tuple


HIGH_CONFIDENCE_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b")),
)
DJI_MACRO = re.compile(
    rb'^\s*#define\s+(USER_APP_KEY|USER_APP_LICENSE|USER_DEVELOPER_ACCOUNT)\s+"([^"]+)"',
    re.M,
)
PLACEHOLDERS = (b"REPLACE", b"PLACEHOLDER", b"YOUR_")


def tracked_paths(root: Path) -> List[str]:
    output = subprocess.run(
        [
            "git", "-C", str(root), "ls-files", "-z",
            "--cached", "--others", "--exclude-standard",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return [item.decode("utf-8") for item in output.split(b"\0") if item]


def path_finding(path: str) -> str:
    lower = PurePosixPath(path).as_posix().lower()
    if lower == "config/dji_sdk_app_info.local.h":
        return "tracked_dji_local_header"
    if lower.endswith((".pem", ".p12", ".pfx")):
        return "tracked_private_key_container"
    if PurePosixPath(lower).name in {".env", "credentials.json"}:
        return "tracked_secret_filename"
    return ""


def scan_file(path: str, data: bytes) -> Iterable[str]:
    finding = path_finding(path)
    if finding:
        yield finding
    for label, pattern in HIGH_CONFIDENCE_PATTERNS:
        if pattern.search(data):
            yield label
    is_explicit_fixture = (
        PurePosixPath(path).parts[:2] == ("tests", "fixtures")
        and b"example.invalid" in data
    )
    for match in DJI_MACRO.finditer(data):
        value = match.group(2).upper()
        if (
            value
            and not is_explicit_fixture
            and not any(marker in value for marker in PLACEHOLDERS)
        ):
            yield "tracked_dji_credential"
            break


def scan_worktree(root: Path) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    for relative in tracked_paths(root):
        path = root / Path(*PurePosixPath(relative).parts)
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            continue
        data = path.read_bytes()
        findings.extend((relative, label) for label in scan_file(relative, data))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="High-confidence tracked-file secret scan with redacted output"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    findings = scan_worktree(root)
    if findings:
        for path, label in findings:
            print("finding={} path={}".format(label, path))
        raise SystemExit("tracked_secret_scan_passed=false")
    print("tracked_secret_scan_passed=true")
    print("secret_values_printed=false")


if __name__ == "__main__":
    main()
