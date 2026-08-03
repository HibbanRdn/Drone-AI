from __future__ import annotations

import importlib.util
import json
import subprocess
import tarfile
from pathlib import Path


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
        source = (MODULE.TEMPLATE_DIR / filename).read_text(encoding="utf-8")
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


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(root: Path, branch: str) -> None:
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Offline Package Test")
    _git(root, "config", "user.email", "offline-package@example.invalid")
    _git(root, "checkout", "-b", branch)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "fixture")


def test_generator_creates_manifest_bundles_and_checksums(
    tmp_path: Path, monkeypatch
) -> None:
    app = tmp_path / "Drone-AI"
    psdk = tmp_path / "Payload-SDK-3.16.0"
    output = tmp_path / "packages"
    _repository(app, MODULE.APP_BRANCH)
    _git(app, "remote", "add", "origin", MODULE.APP_ORIGIN)
    _repository(psdk, "fixture")
    _git(psdk, "tag", MODULE.PSDK_TAG)
    monkeypatch.setattr(MODULE, "verify_psdk", lambda root: None)

    archive = MODULE.create_package(app, psdk, output)

    assert archive.is_file()
    assert archive.with_name(archive.name + ".sha256").is_file()
    extract_root = tmp_path / "extract"
    with tarfile.open(str(archive), "r:gz") as source:
        source.extractall(str(extract_root))
    package_root = next(extract_root.iterdir())
    manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["application"]["branch"] == MODULE.APP_BRANCH
    assert manifest["payload_sdk"]["commit"] == MODULE.PSDK_COMMIT
    assert (package_root / "bundles/Drone-AI.bundle").is_file()
    assert (package_root / "bundles/Payload-SDK-3.16.0.bundle").is_file()
    MODULE.run(
        ("python3", str(package_root / "install/verify_package.py"), str(package_root)),
        cwd=package_root,
    )
    MODULE.run(
        ("bash", str(package_root / "install/verify_offline.sh")),
        cwd=package_root,
    )
