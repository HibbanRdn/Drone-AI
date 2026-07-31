from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


SCRIPT = Path(__file__).parents[1] / "scripts/check_manifold_ai_runtime.py"
SPEC = importlib.util.spec_from_file_location("check_manifold_ai_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _fact(name: str, version: str, path: str, module: ModuleType = None) -> dict:
    return {
        "name": name,
        "status": "ok",
        "version": version,
        "path": path,
        "module": module or ModuleType(name),
    }


def test_platform_contract_matches_inventory() -> None:
    assert MODULE.platform_errors("Linux", "aarch64", (3, 8)) == []
    assert MODULE.platform_errors("Darwin", "arm64", (3, 10))


def test_compatibility_contract_accepts_inventory_versions() -> None:
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: True)
    torch.version = SimpleNamespace(cuda="11.4")
    facts = {
        "cv2": _fact(
            "cv2",
            "4.5.4",
            "/usr/local/lib/python3.8/dist-packages/cv2/__init__.py",
        ),
        "tensorrt": _fact(
            "tensorrt",
            "8.5.2.2",
            "/usr/lib/python3.8/dist-packages/tensorrt/__init__.py",
        ),
        "torch": _fact("torch", "target-build", "/target/torch/__init__.py", torch),
    }
    assert MODULE.compatibility_errors(facts) == []


def test_compatibility_contract_rejects_mixed_opencv_and_cuda() -> None:
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: True)
    torch.version = SimpleNamespace(cuda="12.0")
    facts = {
        "cv2": _fact("cv2", "4.2.0", "/usr/lib/python3/dist-packages/cv2.so"),
        "tensorrt": _fact("tensorrt", "10.0.0", "/target/tensorrt/__init__.py"),
        "torch": _fact("torch", "target-build", "/target/torch/__init__.py", torch),
    }
    errors = MODULE.compatibility_errors(facts)
    assert any("OpenCV 4.5" in error for error in errors)
    assert any("TensorRT" in error for error in errors)
    assert any("CUDA 11.4" in error for error in errors)
