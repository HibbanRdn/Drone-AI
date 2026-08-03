#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from psdk_app_info import read_header, validate as validate_app_info  # noqa: E402
from verify_offline_archive import verify_archive, wheel_compatibility_error  # noqa: E402
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
SCHEMA_VERSION = "gap-plot-ai-offline-package/v2"
TEMPLATE_DIR = APP_ROOT / "deployment/offline"
DEFAULT_APP_INFO = APP_ROOT / "config/dji_sdk_app_info.local.h"
TEMPLATES = (
    "install_offline.sh",
    "verify_offline.sh",
    "verify_package.py",
    "build_manifold_offline.sh",
    "README_TRANSFER.md",
    "verify_offline_archive.py",
)
LF_SUFFIXES = {
    ".c", ".cc", ".cmake", ".cpp", ".h", ".hpp", ".in", ".json",
    ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml",
}
REQUIRED_WHEEL_PREFIXES = ("pyyaml-6.0.2-",)
REQUIRED_WHEEL_SHA256 = {
    "pyyaml-6.0.2-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl":
        "d7fded462629cfa4b685c5416b949ebad6cec74af5e2d42905d41e257e0869f5",
}


class PackageError(ValueError):
    pass


def run(command: Sequence[str], *, cwd: Path) -> str:
    try:
        return subprocess.run(
            list(command), cwd=str(cwd), check=True, capture_output=True, text=True
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


def forbidden_package_path(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.suffix.lower() in {".engine", ".mp4", ".mov"}:
        return True
    if not path.parts:
        return False
    if path.parts[0] in {".venv", "runtime", "data", "outputs", "datasets", "build"}:
        return True
    lower = path.as_posix().lower()
    if lower in {
        "config/secrets.env",
        "config/dji_sdk_app_info.h",
        "config/dji_sdk_app_info.local.h",
        "credentials.json",
    }:
        return True
    if ".secret." in lower or lower.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return False


def _tracked_app_info_secret(root: Path, path: str) -> bool:
    lower = path.lower()
    if "dji_sdk_app_info" not in lower or "example" in lower or "fixture" in lower:
        return False
    content = git(root, "show", "HEAD:{}".format(path))
    for macro in ("USER_APP_KEY", "USER_APP_LICENSE", "USER_DEVELOPER_ACCOUNT"):
        marker = '#define {} "'.format(macro)
        if marker in content and not any(
            token in content.upper() for token in ("REPLACE_WITH", "PLACEHOLDER", "YOUR_")
        ):
            return True
    return False


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
            "Final package must be created from {} (current={})".format(
                APP_BRANCH, branch or "detached"
            )
        )
    status = git(root, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise PackageError(
            "Application worktree is dirty. Review and commit the approved diff "
            "before creating the final package."
        )
    submodules = git(root, "submodule", "status", "--recursive")
    if submodules:
        raise PackageError("Application submodules are not supported")
    tracked = git(root, "ls-files").splitlines()
    forbidden = [path for path in tracked if forbidden_package_path(path)]
    if forbidden:
        raise PackageError(
            "Forbidden deployment artifacts are tracked: " + ", ".join(forbidden)
        )
    secret_paths = [path for path in tracked if _tracked_app_info_secret(root, path)]
    if secret_paths:
        raise PackageError(
            "DJI application credentials are tracked by Git: " + ", ".join(secret_paths)
        )
    lfs_pointers = [
        path for path in tracked if Path(path).suffix.lower() in {".pt", ".onnx"}
    ]
    return {
        "branch": branch,
        "commit": git(root, "rev-parse", "HEAD"),
        "commit_timestamp": int(git(root, "show", "-s", "--format=%ct", "HEAD")),
        "origin": APP_ORIGIN,
        "lfs_pointer_paths": lfs_pointers,
    }


def normalize_text_bytes(data: bytes, *, powershell: bool = False) -> bytes:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data.replace(b"\n", b"\r\n") if powershell else data


def normalize_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        is_cmake = path.name == "CMakeLists.txt"
        if suffix in LF_SUFFIXES or is_cmake or suffix == ".ps1":
            path.write_bytes(
                normalize_text_bytes(path.read_bytes(), powershell=suffix == ".ps1")
            )
        if suffix == ".sh":
            path.chmod(0o755)
        elif "credentials/" in relative:
            path.chmod(0o600)
        else:
            path.chmod(0o644)


def _safe_extract_local_tar(source: tarfile.TarFile, destination: Path) -> None:
    for member in source.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise PackageError("git archive contains an unsafe path")
    source.extractall(str(destination))


def _add_tree_to_tar(
    output: tarfile.TarFile, root: Path, prefix: str, *, mtime: int
) -> None:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        arcname = "{}/{}".format(prefix, relative)
        info = output.gettarinfo(str(path), arcname=arcname)
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = mtime
        if info.isfile():
            with path.open("rb") as source:
                output.addfile(info, source)
        else:
            output.addfile(info)


def create_source_archive(
    app_root: Path, destination: Path, *, commit_timestamp: int
) -> None:
    with tempfile.TemporaryDirectory(prefix="gap_plot_ai_source_") as temp_name:
        temp = Path(temp_name)
        raw_archive = temp / "git-archive.tar"
        source_tree = temp / "source"
        source_tree.mkdir()
        try:
            with raw_archive.open("wb") as output:
                subprocess.run(
                    ["git", "-C", str(app_root), "archive", "--format=tar", "HEAD"],
                    check=True,
                    stdout=output,
                    stderr=subprocess.PIPE,
                )
        except (OSError, subprocess.CalledProcessError) as error:
            raise PackageError("Unable to create the application source snapshot") from error
        with tarfile.open(str(raw_archive), "r:") as source:
            _safe_extract_local_tar(source, source_tree)
        for path in sorted(source_tree.rglob("*")):
            if path.is_file() and path.suffix.lower() in {
                ".engine", ".onnx", ".pt", ".mp4", ".mov"
            }:
                path.unlink()
        normalize_tree(source_tree)
        for forbidden in (
            source_tree / "config/dji_sdk_app_info.h",
            source_tree / "config/dji_sdk_app_info.local.h",
            source_tree / "config/secrets.env",
        ):
            if forbidden.exists():
                raise PackageError("Application source snapshot contains credentials")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(str(destination), "w:") as output:
            _add_tree_to_tar(output, source_tree, "Drone-AI", mtime=commit_timestamp)
        destination.chmod(0o644)


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
        source = SCRIPT_DIR / filename if filename == "verify_offline_archive.py" else TEMPLATE_DIR / filename
        if not source.is_file():
            raise PackageError("Missing deployment template: {}".format(source))
        destination = install_dir / filename
        destination.write_bytes(
            render_template(source.read_text(encoding="utf-8-sig"), values).encode(
                "utf-8"
            )
        )


def copy_app_info(source: Path, package_root: Path) -> Path:
    source = source.expanduser().resolve()
    validate_app_info(read_header(source))
    destination = package_root / "credentials/dji_sdk_app_info.local.h"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(normalize_text_bytes(source.read_bytes()))
    destination.chmod(0o600)
    return destination


def validate_wheel_directory(source: Optional[Path]) -> List[Path]:
    if source is None:
        raise PackageError(
            "--wheels is required; stage Python 3.8 Linux aarch64 PyYAML wheels"
        )
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise PackageError("Offline wheel directory not found: {}".format(source))
    wheels = sorted(source.glob("*.whl"))
    for wheel in wheels:
        error = wheel_compatibility_error(wheel.name)
        if error:
            raise PackageError("{}: {}".format(error, wheel.name))
        expected_hash = REQUIRED_WHEEL_SHA256.get(wheel.name.lower())
        if wheel.name.lower().startswith("pyyaml-6.0.2-"):
            if expected_hash is None or sha256(wheel) != expected_hash:
                raise PackageError(
                    "PyYAML wheel filename or SHA-256 is not the audited target artifact"
                )
    lower_names = [wheel.name.lower() for wheel in wheels]
    missing = [
        prefix for prefix in REQUIRED_WHEEL_PREFIXES
        if not any(name.startswith(prefix) for name in lower_names)
    ]
    if missing:
        raise PackageError(
            "Offline wheel directory is incomplete; missing: " + ", ".join(missing)
        )
    return wheels


def copy_wheels(source: Optional[Path], package_root: Path) -> List[str]:
    wheels = validate_wheel_directory(source)
    destination = package_root / "offline_wheels"
    copied: List[str] = []
    for wheel in wheels:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(wheel), str(destination / wheel.name))
        copied.append(wheel.name)
    return copied


def artifact_records(package_root: Path, paths: Iterable[Path]) -> List[Dict[str, object]]:
    return [
        {
            "path": path.relative_to(package_root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(paths)
    ]


def write_checksums(package_root: Path) -> None:
    files = [
        path for path in package_root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    lines = [
        "{}  {}".format(sha256(path), path.relative_to(package_root).as_posix())
        for path in sorted(files)
    ]
    checksum = package_root / "SHA256SUMS"
    checksum.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    checksum.chmod(0o644)


def create_package(
    app_root: Path,
    psdk_root: Path,
    output_dir: Path,
    *,
    app_info: Path = DEFAULT_APP_INFO,
    wheels: Optional[Path] = None,
) -> Path:
    app = check_application(app_root)
    verify_psdk(psdk_root)
    validate_app_info(read_header(app_info.expanduser().resolve()))
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    package_name = "gap_plot_ai_offline_{}_{}".format(str(app["commit"])[:12], timestamp)
    archive = output_dir / "{}.tar.gz".format(package_name)
    sidecar = output_dir / "{}.sha256".format(archive.name)
    if archive.exists() or sidecar.exists():
        raise PackageError("Output package already exists; refusing to overwrite")

    with tempfile.TemporaryDirectory(prefix="gap_plot_ai_package_", dir=str(output_dir)) as temp:
        package_root = Path(temp) / package_name
        package_root.mkdir(parents=True)
        source_archive = package_root / "sources/Drone-AI-source.tar"
        create_source_archive(
            app_root, source_archive, commit_timestamp=int(app["commit_timestamp"])
        )
        bundles = package_root / "bundles"
        bundles.mkdir()
        psdk_bundle = bundles / "Payload-SDK-3.16.0.bundle"
        git(psdk_root, "bundle", "create", str(psdk_bundle), "refs/tags/{}".format(PSDK_TAG))
        copy_app_info(app_info, package_root)
        wheel_names = copy_wheels(wheels, package_root)
        values = {
            "APP_COMMIT": str(app["commit"]),
            "APP_BRANCH": APP_BRANCH,
            "APP_ORIGIN": APP_ORIGIN,
            "PSDK_COMMIT": PSDK_COMMIT,
            "PSDK_TAG": PSDK_TAG,
            "PSDK_ORIGIN": PSDK_ORIGIN,
        }
        copy_install_assets(package_root, values)
        normalize_tree(package_root)
        artifacts = artifact_records(
            package_root, [path for path in package_root.rglob("*") if path.is_file()]
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "application": {
                "repository": APP_REPOSITORY,
                "origin": APP_ORIGIN,
                "branch": APP_BRANCH,
                "commit": app["commit"],
                "source_archive": "sources/Drone-AI-source.tar",
                "history_included": False,
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
            "credentials": {
                "path": "credentials/dji_sdk_app_info.local.h",
                "included_in_git": False,
                "values_logged": False,
            },
            "offline_wheels": wheel_names,
            "lfs_policy": {
                "tracked_pointer_paths": app["lfs_pointer_paths"],
                "included_objects": [],
                "reason": "The validated TensorRT engine remains read-only on Manifold.",
            },
            "excluded": [
                ".venv", "*.engine", "*.pt", "*.onnx", "datasets", "runtime sessions/logs",
                "inference output", "Git history", "build caches",
            ],
            "artifacts": artifacts,
        }
        manifest_path = package_root / "manifest.json"
        manifest_path.write_bytes(
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        manifest_path.chmod(0o644)
        write_checksums(package_root)
        with tarfile.open(str(archive), "w:gz") as output:
            _add_tree_to_tar(
                output,
                package_root,
                package_name,
                mtime=int(app["commit_timestamp"]),
            )
    archive.chmod(0o600)
    sidecar.write_bytes("{}  {}\n".format(sha256(archive), archive.name).encode("ascii"))
    sidecar.chmod(0o644)
    verify_archive(archive, sidecar)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a normalized offline Drone-AI + PSDK 3.16 package"
    )
    parser.add_argument("--app-root", type=Path, default=APP_ROOT)
    parser.add_argument("--psdk-root", type=Path)
    parser.add_argument("--credentials", type=Path, default=DEFAULT_APP_INFO)
    parser.add_argument("--wheels", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.psdk_root is None:
        raise SystemExit("Set --psdk-root explicitly")
    app_root = args.app_root.expanduser().resolve()
    psdk_root = args.psdk_root.expanduser().resolve()
    try:
        app = check_application(app_root)
        verify_psdk(psdk_root)
        validate_app_info(read_header(args.credentials.expanduser().resolve()))
        validate_wheel_directory(args.wheels)
        if args.check:
            print("package_ready=true")
            print("app_commit={}".format(app["commit"]))
            print("psdk_commit={}".format(PSDK_COMMIT))
            print("credentials_staged_from_git=false")
            return
        if args.output is None:
            raise PackageError("--output is required unless --check is used")
        archive = create_package(
            app_root,
            psdk_root,
            args.output.expanduser().resolve(),
            app_info=args.credentials,
            wheels=args.wheels,
        )
    except (PackageError, ValueError, OSError) as error:
        raise SystemExit("Offline package blocked: {}".format(error)) from None
    print("package={}".format(archive))
    print("sha256={}".format(sha256(archive)))


if __name__ == "__main__":
    main()
