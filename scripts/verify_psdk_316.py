#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path


EXPECTED_TAG = "3.16.0"
EXPECTED_COMMIT = "9af08536df3671ed77aee02f1f2e4173b0e0f558"
EXPECTED_LIBRARY_SHA256 = (
    "c940d6d88f449ef6f48e535e0b765f4bf8486db44702d4d64e37da156d204426"
)
VERSION_MACROS = {
    "DJI_VERSION_MAJOR": "3",
    "DJI_VERSION_MINOR": "16",
    "DJI_VERSION_MODIFY": "0",
}


class VerificationError(ValueError):
    pass


def git_output(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise VerificationError(
            "PSDK_ROOT harus berupa checkout Git resmi tag 3.16.0"
        ) from error


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root: Path) -> None:
    if not root.is_dir():
        raise VerificationError(f"PSDK_ROOT tidak ditemukan: {root}")

    commit = git_output(root, "rev-parse", "HEAD")
    tag = git_output(root, "describe", "--tags", "--exact-match", "HEAD")
    if commit != EXPECTED_COMMIT or tag != EXPECTED_TAG:
        raise VerificationError(
            "PSDK_ROOT bukan checkout resmi exact tag 3.16.0 "
            f"(HEAD={commit}, tag={tag or 'none'})"
        )

    version_header = root / "psdk_lib/include/dji_version.h"
    library = root / "psdk_lib/lib/aarch64-linux-gnu-gcc/libpayloadsdk.a"
    usb_hal = (
        root
        / "samples/sample_c++/platform/linux/manifold3/hal/hal_usb_bulk.c"
    )
    osal = root / "samples/sample_c++/platform/linux/common/osal/osal.c"
    for required in (version_header, library, usb_hal, osal):
        if not required.is_file():
            raise VerificationError(f"File PSDK wajib tidak ditemukan: {required}")

    header = version_header.read_text(encoding="utf-8")
    for macro, expected in VERSION_MACROS.items():
        match = re.search(rf"^#define\s+{macro}\s+(\d+)\b", header, re.MULTILINE)
        if match is None or match.group(1) != expected:
            raise VerificationError(f"Versi header PSDK tidak sesuai pada {macro}")

    if sha256(library) != EXPECTED_LIBRARY_SHA256:
        raise VerificationError(
            "SHA-256 libpayloadsdk.a aarch64 tidak cocok dengan tag 3.16.0"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the exact official DJI PSDK 3.16.0 checkout"
    )
    parser.add_argument("psdk_root", type=Path)
    args = parser.parse_args()
    try:
        verify(args.psdk_root.expanduser().resolve())
    except VerificationError as error:
        raise SystemExit(f"PSDK verification failed: {error}") from None
    print(f"psdk_tag={EXPECTED_TAG}")
    print(f"psdk_commit={EXPECTED_COMMIT}")
    print(f"psdk_aarch64_library_sha256={EXPECTED_LIBRARY_SHA256}")


if __name__ == "__main__":
    main()
