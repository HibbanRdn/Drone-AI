#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "models" / "model_manifest.json"
LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fail(message: str) -> None:
    raise RuntimeError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"JSON config tidak dapat dibaca: {path.relative_to(REPO_ROOT)} ({exc})")


def is_lfs_pointer(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(len(LFS_HEADER)) == LFS_HEADER


def check_lfs_attribute(relative: str) -> None:
    try:
        output = subprocess.check_output(
            ["git", "check-attr", "filter", "--", relative],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"Git LFS attribute tidak dapat diperiksa untuk {relative}: {exc}")
    if not output.rstrip().endswith(": lfs"):
        fail(f"{relative} tidak dikonfigurasi sebagai Git LFS")


def main() -> int:
    manifest = load_json(MANIFEST_PATH)
    configs = [
        REPO_ROOT / "models" / "configs" / "export.json",
        REPO_ROOT / "models" / "configs" / "postprocess.json",
        REPO_ROOT / "models" / "configs" / "training_provenance.json",
        REPO_ROOT / "config" / "app.yaml",
    ]
    for path in configs:
        if not path.is_file():
            fail(f"Config wajib tidak ditemukan: {path.relative_to(REPO_ROOT)}")
        if path.suffix == ".json":
            load_json(path)

    checked = 0
    for artifact in manifest["artifacts"]:
        relative = str(artifact["file"])
        path = REPO_ROOT / relative
        if not path.is_file():
            fail(
                f"Artifact wajib tidak ditemukan: {relative}. "
                "Jalankan git lfs pull lalu ulangi preflight."
            )
        if is_lfs_pointer(path):
            fail(
                f"{relative} masih berupa pointer Git LFS. "
                "Jalankan git lfs install lalu git lfs pull."
            )
        actual_size = path.stat().st_size
        if actual_size != int(artifact["size_bytes"]):
            fail(f"Ukuran {relative} berubah: {actual_size} != {artifact['size_bytes']}")
        actual_hash = sha256(path)
        if actual_hash != artifact["sha256"]:
            fail(f"SHA-256 {relative} tidak cocok: {actual_hash}")
        check_lfs_attribute(relative)
        checked += 1

    app_text = (REPO_ROOT / "config" / "app.yaml").read_text(encoding="utf-8")
    for artifact in manifest["artifacts"]:
        relative = str(artifact["file"])
        if relative not in app_text:
            fail(f"Config aplikasi belum menunjuk artifact: {relative}")

    print(f"model_preflight_ok artifacts={checked} repo={REPO_ROOT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"MODEL_PREFLIGHT_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
