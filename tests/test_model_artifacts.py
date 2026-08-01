from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_model_preflight_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(root / "scripts" / "model_preflight.py")],
        cwd=root,
        check=True,
    )
