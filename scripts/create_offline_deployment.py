#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from verify_psdk_316 import (  # noqa: E402
    EXPECTED_COMMIT as PSDK_COMMIT,
    EXPECTED_ORIGIN as PSDK_ORIGIN,
    EXPECTED_TAG as PSDK_TAG,
    github_repository,
    verify as verify_psdk,
)


APP_BRANCH = "feature/pilot-liveview-inference"
APP_REPOSITORY = "HibbanRdn/Drone-AI"
APP_ORIGIN = "https://github.com/HibbanRdn/Drone-AI.git"
SCHEMA_VERSION = "gap-plot-ai-offline-package/v1"
TEMPLATE_DIR = APP_ROOT / "deployment/offline"
TEMPLATES = (
    "install_offline.sh",
    "verify_offline.sh",
    "verify_package.py",
    "build_manifold_offline.sh",
    "README_TRANSFER.md",
)


class PackageError(ValueError):
    pass


def run(command: Sequence[str], *, cwd: Path) -> str:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        detail = ""
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or error.stdout or "").strip()
        raise PackageError(
            "Command failed: {}{}".format(
                " ".join(command), " ({})".format(detail) if detail else ""
            )
        ) from error


def git(root: Path, *arguments: str) -> str:
    return run(("git", "-C", str(root), *arguments), cwd=root)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_application(root: Path) -> Dict[str, object]:
    top_level = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root.resolve():
        raise PackageError("Application root is not the Drone-AI Git toplevel")
    origin = git(root, "remote", "get-url", "origin")
    if github_repository(origin) != APP_REPOSITORY:
        raise PackageError("Application origin is not HibbanRdn/Drone-AI")
    branch = git(root, "branch", "--show-current")
    if branch != APP_BRANCH:
        raise PackageError(
            "Final bundle must be created from {} (current={})".format(
                APP_BRANCH, branch or "detached"
            )
        )
    status = git(root, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise PackageError(
            "Application worktree is dirty. Review and commit the approved diff "
            "before creating the final Git bundle."
        )
    submodules = git(root, "submodule", "status", "--recursive")
    if submodules:
        raise PackageError(
            "Application submodules are not supported by this source-only package"
        )

    tracked = git(root, "ls-files").splitlines()
    forbidden = [path for path in tracked if forbidden_package_path(path)]
    if forbidden:
        raise PackageError(
            "Forbidden deployment artifacts are tracked: " + ", ".join(forbidden)
        )
    history_paths = []
    for line in git(root, "rev-list", "--objects", branch).splitlines():
        _object_id, separator, path = line.partition(" ")
        if separator and forbidden_package_path(path):
            history_paths.append(path)
    if history_paths:
        raise PackageError(
            "The Git bundle history contains forbidden deployment artifacts: "
            + ", ".join(sorted(set(history_paths)))
        )
    lfs_pointers = [
        path
        for path in tracked
        if Path(path).suffix.lower() in {".pt", ".onnx", ".engine"}
    ]
    return {
        "branch": branch,
        "commit": git(root, "rev-parse", "HEAD"),
        "origin": APP_ORIGIN,
        "lfs_pointer_paths": lfs_pointers,
    }


def forbidden_package_path(value: str) -> bool:
    path = Path(value)
    if path.suffix.lower() in {".engine", ".mp4", ".mov"}:
        return True
    if not path.parts:
        return False
    if path.parts[0] in {".venv", "runtime", "data", "outputs", "datasets"}:
        return True
    lower = value.lower()
    if lower in {"config/secrets.env", "credentials.json"}:
        return True
    if ".secret." in lower or lower.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return False


def render_template(source: str, values: Dict[str, str]) -> str:
    rendered = source
    for name, value in values.items():
        rendered = rendered.replace("@{}@".format(name), value)
    if "@APP_" in rendered or "@PSDK_" in rendered:
        raise PackageError("Unresolved offline deployment template placeholder")
    return rendered


def copy_install_assets(package_root: Path, values: Dict[str, str]) -> None:
    install_dir = package_root / "install"
    install_dir.mkdir(parents=True)
    for filename in TEMPLATES:
        source = TEMPLATE_DIR / filename
        if not source.is_file():
            raise PackageError("Missing deployment template: {}".format(source))
        destination = install_dir / filename
        destination.write_text(
            render_template(source.read_text(encoding="utf-8"), values),
            encoding="utf-8",
        )
        if destination.suffix == ".sh" or destination.suffix == ".py":
            destination.chmod(0o755)


def artifact_records(package_root: Path, paths: Iterable[Path]) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    for path in sorted(paths):
        records.append(
            {
                "path": path.relative_to(package_root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return records


def write_checksums(package_root: Path) -> None:
    files = [
        path
        for path in package_root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    lines = [
        "{}  {}".format(sha256(path), path.relative_to(package_root).as_posix())
        for path in sorted(files)
    ]
    (package_root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_package(app_root: Path, psdk_root: Path, output_dir: Path) -> Path:
    app = check_application(app_root)
    verify_psdk(psdk_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_name = "gap_plot_ai_offline_{}_{}".format(
        str(app["commit"])[:12], timestamp
    )
    archive = output_dir / "{}.tar.gz".format(package_name)
    checksum_sidecar = output_dir / "{}.sha256".format(archive.name)
    if archive.exists() or checksum_sidecar.exists():
        raise PackageError("Output package already exists; refusing to overwrite")

    with tempfile.TemporaryDirectory(prefix="gap_plot_ai_package_", dir=str(output_dir)) as temp:
        package_root = Path(temp) / package_name
        bundles = package_root / "bundles"
        bundles.mkdir(parents=True)
        app_bundle = bundles / "Drone-AI.bundle"
        psdk_bundle = bundles / "Payload-SDK-3.16.0.bundle"
        git(app_root, "bundle", "create", str(app_bundle), APP_BRANCH)
        git(psdk_root, "bundle", "create", str(psdk_bundle), "refs/tags/{}".format(PSDK_TAG))

        values = {
            "APP_COMMIT": str(app["commit"]),
            "APP_BRANCH": APP_BRANCH,
            "APP_ORIGIN": APP_ORIGIN,
            "PSDK_COMMIT": PSDK_COMMIT,
            "PSDK_TAG": PSDK_TAG,
            "PSDK_ORIGIN": PSDK_ORIGIN,
        }
        copy_install_assets(package_root, values)
        artifacts = artifact_records(
            package_root,
            [path for path in package_root.rglob("*") if path.is_file()],
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "application": {
                "repository": APP_REPOSITORY,
                "origin": APP_ORIGIN,
                "branch": APP_BRANCH,
                "commit": app["commit"],
                "bundle": "bundles/Drone-AI.bundle",
            },
            "payload_sdk": {
                "repository": "dji-sdk/Payload-SDK",
                "origin": PSDK_ORIGIN,
                "tag": PSDK_TAG,
                "commit": PSDK_COMMIT,
                "bundle": "bundles/Payload-SDK-3.16.0.bundle",
                "submodules": [],
                "lfs_objects": [],
            },
            "lfs_policy": {
                "tracked_pointer_paths": app["lfs_pointer_paths"],
                "included_objects": [],
                "reason": (
                    "The Manifold TensorRT engine already exists device-side; "
                    "PT/ONNX LFS objects are not needed for source build or runtime."
                ),
            },
            "excluded": [
                ".venv",
                "*.engine",
                "datasets",
                "runtime sessions/logs",
                "inference output",
                "untracked credential files and private config",
                "build caches",
            ],
            "artifacts": artifacts,
        }
        (package_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_checksums(package_root)
        with tarfile.open(str(archive), "w:gz") as output:
            output.add(str(package_root), arcname=package_name)

    checksum_sidecar.write_text(
        "{}  {}\n".format(sha256(archive), archive.name), encoding="utf-8"
    )
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an offline, source-only Drone-AI + official PSDK package"
    )
    parser.add_argument(
        "--app-root", type=Path, default=APP_ROOT, help="Drone-AI Git worktree"
    )
    parser.add_argument(
        "--psdk-root",
        type=Path,
        default=None,
        help="Official clean Payload-SDK exact tag 3.16.0 checkout",
    )
    parser.add_argument("--output", type=Path, help="Destination directory")
    parser.add_argument(
        "--check", action="store_true", help="Validate inputs without creating files"
    )
    args = parser.parse_args()
    if args.psdk_root is None:
        raise SystemExit("Set --psdk-root or PSDK_ROOT explicitly")
    app_root = args.app_root.expanduser().resolve()
    psdk_root = args.psdk_root.expanduser().resolve()
    try:
        app = check_application(app_root)
        verify_psdk(psdk_root)
        if args.check:
            print("bundle_ready=true")
            print("app_commit={}".format(app["commit"]))
            print("psdk_commit={}".format(PSDK_COMMIT))
            print("lfs_objects_included=0")
            return
        if args.output is None:
            raise PackageError("--output is required unless --check is used")
        archive = create_package(app_root, psdk_root, args.output.expanduser().resolve())
    except (PackageError, ValueError) as error:
        raise SystemExit("Offline package blocked: {}".format(error)) from None
    print("package={}".format(archive))
    print("sha256={}".format(sha256(archive)))


if __name__ == "__main__":
    main()
