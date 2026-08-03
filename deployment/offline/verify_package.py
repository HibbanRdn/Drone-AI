#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


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
    checked = 0
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        expected, separator, relative = line.partition("  ")
        if not separator:
            raise SystemExit("Malformed SHA256SUMS line")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("Unsafe checksum path: {}".format(relative))
        target = package_root / path
        if not target.is_file() or sha256(target) != expected:
            raise SystemExit("Checksum mismatch: {}".format(relative))
        checked += 1
    print("package_checksums_ok={}".format(checked))


if __name__ == "__main__":
    main()
