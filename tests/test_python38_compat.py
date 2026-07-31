from __future__ import annotations

import ast
import re
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
TARGET_PYTHON_FILES = sorted((APP_ROOT / "src" / "gap_plot_ai").glob("*.py")) + [
    APP_ROOT / "scripts" / "psdk_credentials.py",
    APP_ROOT / "scripts" / "verify_psdk_316.py",
]


def test_target_python_sources_parse_as_python38() -> None:
    for path in TARGET_PYTHON_FILES:
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 8),
        )


def test_target_python_annotations_avoid_newer_runtime_forms() -> None:
    builtin_generic = re.compile(r"\b(?:dict|list|set|tuple)\s*\[")
    pep604_optional = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*\s*\|\s*None\b")
    strict_zip = re.compile(r"\bzip\s*\([^)]*\bstrict\s*=", re.DOTALL)

    for path in TARGET_PYTHON_FILES:
        source = path.read_text(encoding="utf-8")
        assert builtin_generic.search(source) is None, str(path)
        assert pep604_optional.search(source) is None, str(path)
        assert strict_zip.search(source) is None, str(path)
