from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
    "generic_bearer": re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def environment_report() -> dict[str, object]:
    report: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "ffmpeg_path": shutil.which("ffmpeg"),
        "ffprobe_path": shutil.which("ffprobe"),
        "cwd": os.getcwd(),
        "audio_preserved": False,
    }
    try:
        import cv2
        import numpy
        import torch
        import ultralytics

        report["versions"] = {
            "opencv": cv2.__version__,
            "numpy": numpy.__version__,
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
        }
        report["mps"] = {
            "built": bool(torch.backends.mps.is_built()),
            "available": bool(torch.backends.mps.is_available()),
        }
    except ImportError as exc:
        report["dependency_error"] = str(exc)
    return report


def ffmpeg_codec_report() -> dict[str, object]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ffmpeg_available": False, "libx264_available": False}
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    )
    combined = result.stdout + result.stderr
    return {
        "ffmpeg_available": True,
        "ffmpeg_path": ffmpeg,
        "libx264_available": "libx264" in combined,
        "opencv_mp4v_fallback": True,
        "opencv_avi_fallback": True,
    }


def scan_for_credentials(paths: Iterable[str | Path]) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    scanned_files = 0
    for root_value in paths:
        root = Path(root_value)
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in {
                ".py",
                ".json",
                ".yaml",
                ".yml",
                ".md",
                ".sh",
                ".txt",
            }:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            scanned_files += 1
            for name, pattern in SECRET_PATTERNS.items():
                for match in pattern.finditer(content):
                    findings.append(
                        {
                            "path": str(path),
                            "line": content.count("\n", 0, match.start()) + 1,
                            "pattern": name,
                        }
                    )
    return {"scanned_files": scanned_files, "findings": findings, "passed": not findings}


def write_json(data: object, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
