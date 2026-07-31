#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import platform
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


PHASE_MODULES = {
    "engine": ("yaml", "numpy", "cv2", "onnx", "tensorrt", "torch", "ultralytics"),
    "runtime": ("yaml", "numpy", "cv2", "tensorrt", "torch", "ultralytics"),
}


def platform_errors(
    system_name: str, machine: str, version: Tuple[int, int]
) -> List[str]:
    errors = []
    if system_name != "Linux" or machine != "aarch64":
        errors.append("target must be Linux aarch64 Manifold 3")
    if version != (3, 8):
        errors.append("target Python must be 3.8")
    return errors


def module_fact(module_name: str) -> Dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        return {
            "name": module_name,
            "status": "missing",
            "error": "{}: {}".format(type(error).__name__, error),
        }
    return {
        "name": module_name,
        "status": "ok",
        "version": str(getattr(module, "__version__", "unknown")),
        "path": str(getattr(module, "__file__", "built-in")),
        "module": module,
    }


def compatibility_errors(facts: Dict[str, Dict[str, Any]]) -> List[str]:
    errors = []
    cv2_fact = facts.get("cv2")
    if cv2_fact and cv2_fact["status"] == "ok":
        if not cv2_fact["version"].startswith("4.5."):
            errors.append("cv2 must use audited OpenCV 4.5.x")
        if "/usr/local/lib/python3.8/" not in cv2_fact["path"]:
            errors.append("cv2 must resolve to the audited /usr/local Python 3.8 install")

    tensorrt_fact = facts.get("tensorrt")
    if tensorrt_fact and tensorrt_fact["status"] == "ok":
        if not tensorrt_fact["version"].startswith("8.5.2"):
            errors.append("TensorRT Python must match audited 8.5.2")

    torch_fact = facts.get("torch")
    if torch_fact and torch_fact["status"] == "ok":
        torch_module = torch_fact["module"]
        cuda_module = getattr(torch_module, "cuda", None)
        if cuda_module is None or not bool(cuda_module.is_available()):
            errors.append("PyTorch CUDA must be available")
        torch_version = getattr(torch_module, "version", None)
        cuda_version = str(getattr(torch_version, "cuda", "unavailable"))
        torch_fact["cuda_version"] = cuda_version
        if not cuda_version.startswith("11.4"):
            errors.append("PyTorch CUDA must match audited CUDA 11.4")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only preflight for the Manifold 3 AI Python runtime"
    )
    parser.add_argument("--phase", choices=sorted(PHASE_MODULES), required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    errors = platform_errors(
        platform.system(), platform.machine(), sys.version_info[:2]
    )
    facts = {}
    for module_name in PHASE_MODULES[args.phase]:
        fact = module_fact(module_name)
        facts[module_name] = fact
        if fact["status"] != "ok":
            errors.append(
                "{} unavailable ({})".format(module_name, fact["error"])
            )

    errors.extend(compatibility_errors(facts))
    print(
        "target system={} machine={} python={}.{}.{}".format(
            platform.system(),
            platform.machine(),
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        )
    )
    for module_name in PHASE_MODULES[args.phase]:
        fact = facts[module_name]
        if fact["status"] == "ok":
            extra = ""
            if "cuda_version" in fact:
                extra = " cuda={}".format(fact["cuda_version"])
            print(
                "module={} status=ok version={} path={}{}".format(
                    module_name, fact["version"], fact["path"], extra
                )
            )
        else:
            print("module={} status=missing".format(module_name))

    if errors:
        for error in errors:
            print("BLOCKED: {}".format(error), file=sys.stderr)
        return 3
    print("manifold_ai_{}_ready=yes".format(args.phase))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
