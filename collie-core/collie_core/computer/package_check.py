"""Verify the installed wheel's actual native payload, without executing it."""

import hashlib
import importlib.util
import json
from importlib.metadata import version
from pathlib import Path


def validate_artifacts() -> dict:
    manifest = json.loads(Path(__file__).with_name("runtime-artifacts.json").read_text())
    if version("cua-driver") != manifest["version"]:
        raise ValueError("Computer runtime version differs from the reviewed artifact.")
    spec = importlib.util.find_spec("cua_driver")
    if spec is None or spec.origin is None:
        raise ValueError("Computer runtime is missing; repair Collie.")
    root = Path(spec.origin).parent
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.suffix in {".dll", ".exe", ".so", ".dylib"}
    }
    if actual != set(manifest["files"]):
        raise ValueError("Unexpected native computer runtime payload; repair Collie.")
    for name, expected in manifest["files"].items():
        path = root / name
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if path.stat().st_size != expected["bytes"] or digest != expected["sha256"]:
            raise ValueError("Computer runtime integrity check failed; repair Collie.")
    return {
        "version": manifest["version"],
        "native_files": len(actual),
        "native_bytes": sum(f["bytes"] for f in manifest["files"].values()),
    }
