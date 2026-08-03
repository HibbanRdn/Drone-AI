from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_dpk import ManifestError, validate_manifest


def _manifest() -> dict:
    return {
        "user_app_id": "189927",
        "firmware_version": "00.01.00.00",
        "is_ai_rendering": "true",
        "platform": "manifold3",
        "name": {},
        "description": {},
        "maintainer": {},
        "ver_min": "01.00.00.00",
        "ver_max": "01.99.99.99",
        "bin": "bin/gap_plot_ai_launcher",
        "userconfig": ["payload"],
    }


def test_manifest_validation_accepts_prepared_staging(tmp_path: Path) -> None:
    binary = tmp_path / "bin/gap_plot_ai_launcher"
    binary.parent.mkdir()
    binary.write_text("launcher", encoding="utf-8")
    (tmp_path / "payload").mkdir()
    validate_manifest(_manifest(), tmp_path)


def test_manifest_validation_rejects_placeholder_and_secret(tmp_path: Path) -> None:
    binary = tmp_path / "bin/gap_plot_ai_launcher"
    binary.parent.mkdir()
    binary.write_text("launcher", encoding="utf-8")
    (tmp_path / "payload").mkdir()
    manifest = _manifest()
    manifest["ver_min"] = "@MANIFOLD_VER_MIN@"
    with pytest.raises(ManifestError, match="ver_min"):
        validate_manifest(manifest, tmp_path)
    manifest["ver_min"] = "01.00.00.00"
    (tmp_path / "secrets.env").write_text("secret", encoding="utf-8")
    with pytest.raises(ManifestError, match="Credential"):
        validate_manifest(manifest, tmp_path)


def test_manifest_validation_rejects_path_traversal(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["bin"] = "../launcher"
    with pytest.raises(ManifestError, match="relatif aman"):
        validate_manifest(manifest, tmp_path)
