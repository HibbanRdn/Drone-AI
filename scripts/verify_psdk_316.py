#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse


EXPECTED_TAG = "3.16.0"
EXPECTED_COMMIT = "9af08536df3671ed77aee02f1f2e4173b0e0f558"
EXPECTED_REPOSITORY = "dji-sdk/Payload-SDK"
EXPECTED_ORIGIN = "https://github.com/dji-sdk/Payload-SDK.git"
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


def github_repository(remote: str) -> str:
    value = remote.strip()
    scp_match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?", value)
    if scp_match:
        return scp_match.group(1)
    parsed = urlparse(value)
    if parsed.hostname != "github.com":
        return ""
    repository = parsed.path.strip("/")
    if repository.endswith(".git"):
        repository = repository[:-4]
    return repository


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

    origin = git_output(root, "remote", "get-url", "origin")
    if github_repository(origin) != EXPECTED_REPOSITORY:
        raise VerificationError(
            "origin PSDK bukan repository resmi dji-sdk/Payload-SDK "
            f"(origin={origin})"
        )

    commit = git_output(root, "rev-parse", "HEAD")
    tag = git_output(root, "describe", "--tags", "--exact-match", "HEAD")
    if commit != EXPECTED_COMMIT or tag != EXPECTED_TAG:
        raise VerificationError(
            "PSDK_ROOT bukan checkout resmi exact tag 3.16.0 "
            f"(HEAD={commit}, tag={tag or 'none'})"
        )

    worktree_status = git_output(root, "status", "--porcelain", "--untracked-files=all")
    if worktree_status:
        raise VerificationError("Checkout PSDK harus bersih dan tidak dimodifikasi")
    if (root / ".gitmodules").exists():
        raise VerificationError(
            "PSDK 3.16.0 resmi tidak memakai Git submodule; checkout berbeda"
        )
    attributes = root / ".gitattributes"
    if attributes.is_file() and "filter=lfs" in attributes.read_text(
        encoding="utf-8", errors="replace"
    ):
        raise VerificationError(
            "PSDK 3.16.0 resmi tidak memakai Git LFS; checkout berbeda"
        )

    version_header = root / "psdk_lib/include/dji_version.h"
    library = root / "psdk_lib/lib/aarch64-linux-gnu-gcc/libpayloadsdk.a"
    usb_hal = (
        root
        / "samples/sample_c++/platform/linux/manifold3/hal/hal_usb_bulk.c"
    )
    osal = root / "samples/sample_c++/platform/linux/common/osal/osal.c"
    liveview_header = root / "psdk_lib/include/dji_liveview.h"
    h264_sample = root / "samples/sample_c++/module_sample/liveview/test_liveview.cpp"
    decoder_sample = (
        root
        / "samples/sample_c++/module_sample/liveview/dji_camera_stream_decoder.cpp"
    )
    object_detection_sample = (
        root
        / "samples/sample_c++/module_sample/liveview/dji_liveview_object_detection.cpp"
    )
    dpk_builder = root / "tools/build_dpk/build_dpk.sh"
    for required in (
        version_header,
        liveview_header,
        library,
        usb_hal,
        osal,
        h264_sample,
        decoder_sample,
        object_detection_sample,
        dpk_builder,
    ):
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

    liveview_api = liveview_header.read_text(encoding="utf-8")
    required_api_tokens = (
        "DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS = 1",
        "DJI_LIVEVIEW_CAMERA_SOURCE_M4E_4K = 3",
        "DjiLiveview_StartH264Stream",
        "DjiLiveview_StopH264Stream",
        "DjiLiveview_RequestIntraframeFrameData",
        "DjiLiveview_StartImageStream",
        "DjiLiveview_StopImageStream",
        "DjiLiveview_RegEncoderCallback",
        "DjiLiveview_EncodeAFrameToH264",
        "DjiLiveview_RegUserAiTargetLableList",
        "DjiLiveview_SendAiMetaToPilot",
    )
    missing = [token for token in required_api_tokens if token not in liveview_api]
    if missing:
        raise VerificationError(
            "Header Liveview 3.16.0 tidak memiliki kontrak yang diperlukan: "
            + ", ".join(missing)
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
    print(f"psdk_origin={EXPECTED_ORIGIN}")
    print("psdk_submodules=none")
    print("psdk_lfs=none")
    print(f"psdk_aarch64_library_sha256={EXPECTED_LIBRARY_SHA256}")


if __name__ == "__main__":
    main()
