from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import tarfile
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "create_offline_deployment",
    APP_ROOT / "scripts/create_offline_deployment.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_package_excludes_runtime_private_and_heavy_artifacts() -> None:
    forbidden = (
        ".venv/bin/python",
        "runtime/sessions/flight/detections.jsonl",
        "data/logs/app.log",
        "models/engine/plant.engine",
        "datasets/field/frame.jpg",
        "outputs/result.mp4",
        "config/secrets.env",
        "config/operator.secret.yaml",
    )
    assert all(MODULE.forbidden_package_path(path) for path in forbidden)
    assert not MODULE.forbidden_package_path("src/gap_plot/data/schemas.py")
    assert not MODULE.forbidden_package_path("models/source/model.pt")
    assert not MODULE.forbidden_package_path("models/onnx/model.onnx")


def test_offline_templates_render_all_commit_placeholders() -> None:
    values = {
        "APP_COMMIT": "a" * 40,
        "APP_BRANCH": MODULE.APP_BRANCH,
        "APP_ORIGIN": MODULE.APP_ORIGIN,
        "PSDK_COMMIT": MODULE.PSDK_COMMIT,
        "PSDK_TAG": MODULE.PSDK_TAG,
        "PSDK_ORIGIN": MODULE.PSDK_ORIGIN,
    }
    for filename in MODULE.TEMPLATES:
        source_path = (
            MODULE.SCRIPT_DIR / filename
            if filename == "verify_offline_archive.py"
            else MODULE.TEMPLATE_DIR / filename
        )
        source = source_path.read_text(encoding="utf-8")
        rendered = MODULE.render_template(source, values)
        assert "@APP_" not in rendered
        assert "@PSDK_" not in rendered


def test_github_identity_normalization_rejects_forks() -> None:
    assert MODULE.github_repository("git@github.com:dji-sdk/Payload-SDK.git") == (
        "dji-sdk/Payload-SDK"
    )
    assert MODULE.github_repository("https://github.com/other/Payload-SDK.git") != (
        "dji-sdk/Payload-SDK"
    )


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(root: Path, branch: str) -> None:
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Offline Package Test")
    _git(root, "config", "user.email", "offline-package@example.invalid")
    _git(root, "checkout", "-b", branch)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "fixture")


def _app_info(path: Path) -> None:
    license_value = base64.b64encode(b"fixture-license").decode("ascii")
    path.write_text(
        "\n".join(
            (
                '#define USER_APP_NAME "ggp-drone-ai"',
                '#define USER_APP_ID "189927"',
                '#define USER_APP_KEY "0123456789abcdef"',
                '#define USER_APP_LICENSE "{}"'.format(license_value),
                '#define USER_DEVELOPER_ACCOUNT "test@example.invalid"',
                '#define USER_BAUD_RATE "460800"',
                "",
            )
        ),
        encoding="utf-8",
    )


def test_generator_creates_source_snapshot_psdk_bundle_and_checksums(
    tmp_path: Path, monkeypatch
) -> None:
    app = tmp_path / "Drone-AI"
    psdk = tmp_path / "Payload-SDK-3.16.0"
    output = tmp_path / "packages"
    app_info = tmp_path / "dji_sdk_app_info.local.h"
    wheel_name = next(iter(MODULE.REQUIRED_WHEEL_SHA256))
    wheel_bytes = b"fixture-wheel"
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / wheel_name).write_bytes(wheel_bytes)
    _repository(app, MODULE.APP_BRANCH)
    model_pointer = app / "models/onnx/model.onnx"
    model_pointer.parent.mkdir(parents=True)
    model_pointer.write_text("version https://git-lfs.github.com/spec/v1\n", encoding="utf-8")
    _git(app, "add", str(model_pointer.relative_to(app)))
    _git(app, "commit", "-m", "fixture model pointer")
    _git(app, "remote", "add", "origin", MODULE.APP_ORIGIN)
    _repository(psdk, "fixture")
    _git(psdk, "tag", MODULE.PSDK_TAG)
    monkeypatch.setattr(MODULE, "verify_psdk", lambda root: None)
    monkeypatch.setattr(MODULE, "verify_archive", lambda archive, sidecar: {})
    monkeypatch.setattr(MODULE, "PSDK_COMMIT", _git(psdk, "rev-parse", "HEAD"))
    fixture_hash = hashlib.sha256(wheel_bytes).hexdigest()
    monkeypatch.setattr(MODULE, "REQUIRED_WHEEL_SHA256", {wheel_name: fixture_hash})
    original_copy_install_assets = MODULE.copy_install_assets

    def copy_install_assets(package_root: Path, values: dict) -> None:
        original_copy_install_assets(package_root, values)
        verifier = package_root / "install/verify_offline_archive.py"
        verifier.write_text(
            verifier.read_text(encoding="utf-8").replace(
                "d7fded462629cfa4b685c5416b949ebad6cec74af5e2d42905d41e257e0869f5",
                fixture_hash,
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(MODULE, "copy_install_assets", copy_install_assets)
    _app_info(app_info)

    archive = MODULE.create_package(
        app, psdk, output, app_info=app_info, wheels=wheels
    )

    assert archive.is_file()
    assert archive.with_name(archive.name + ".sha256").is_file()
    extract_root = tmp_path / "extract"
    with tarfile.open(str(archive), "r:gz") as source:
        source.extractall(str(extract_root))
    package_root = next(extract_root.iterdir())
    manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == MODULE.SCHEMA_VERSION
    assert manifest["application"]["branch"] == MODULE.APP_BRANCH
    assert manifest["application"]["history_included"] is False
    assert manifest["payload_sdk"]["commit"] == _git(psdk, "rev-parse", "HEAD")
    assert (package_root / "sources/Drone-AI-source.tar").is_file()
    assert not (package_root / "bundles/Drone-AI.bundle").exists()
    assert (package_root / "bundles/Payload-SDK-3.16.0.bundle").is_file()
    with tarfile.open(str(package_root / "sources/Drone-AI-source.tar"), "r:") as source:
        names = source.getnames()
    assert "Drone-AI/README.md" in names
    assert all("dji_sdk_app_info.local.h" not in name for name in names)
    assert all(not name.endswith((".onnx", ".pt", ".engine")) for name in names)
    MODULE.run(
        ("python3", str(package_root / "install/verify_package.py"), str(package_root)),
        cwd=package_root,
    )
    MODULE.run(
        ("bash", str(package_root / "install/verify_offline.sh")),
        cwd=package_root,
    )


def test_wheel_directory_rejects_unaudited_pyyaml_hash(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "PyYAML-6.0.2-cp38-cp38-manylinux2014_aarch64.whl").write_bytes(
        b"not-the-audited-wheel"
    )
    with pytest.raises(MODULE.PackageError, match="SHA-256"):
        MODULE.validate_wheel_directory(wheels)


def test_application_guard_rejects_tracked_psdk_credentials(tmp_path: Path) -> None:
    app = tmp_path / "Drone-AI"
    _repository(app, MODULE.APP_BRANCH)
    _git(app, "remote", "add", "origin", MODULE.APP_ORIGIN)
    secret = app / "config/dji_sdk_app_info.h"
    secret.parent.mkdir()
    _app_info(secret)
    _git(app, "add", str(secret.relative_to(app)))
    _git(app, "commit", "-m", "unsafe fixture")
    with pytest.raises(MODULE.PackageError, match="Forbidden deployment artifacts"):
        MODULE.check_application(app)
