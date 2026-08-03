#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_engine(path: Path) -> Dict[str, Any]:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("engine path is not a regular file")

    import tensorrt as trt

    logger = trt.Logger(trt.Logger.WARNING)
    trt.init_libnvinfer_plugins(logger, "")
    runtime = trt.Runtime(logger)
    with path.open("rb") as source:
        engine = runtime.deserialize_cuda_engine(source.read())
    if engine is None:
        raise RuntimeError("TensorRT could not deserialize the engine")

    bindings: List[Dict[str, Any]] = []
    for index in range(engine.num_bindings):
        bindings.append(
            {
                "index": index,
                "name": engine.get_binding_name(index),
                "is_input": bool(engine.binding_is_input(index)),
                "dtype": str(engine.get_binding_dtype(index)),
                "shape": [int(value) for value in engine.get_binding_shape(index)],
            }
        )
    implicit_batch = bool(engine.has_implicit_batch_dimension)
    result = {
        "schema_version": "gap-plot-ai-tensorrt-inspection/v1",
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "tensorrt_version": str(trt.__version__),
        "implicit_batch": implicit_batch,
        "bindings": bindings,
    }
    engine = None
    runtime = None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only TensorRT 8.5 engine metadata inspection"
    )
    parser.add_argument("engine", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect_engine(args.engine)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.expanduser().resolve().write_bytes(rendered.encode("utf-8"))
    print(rendered, end="")


if __name__ == "__main__":
    main()
