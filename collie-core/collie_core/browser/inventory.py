"""Offline inventory of the actual installed/staged browser runtime and notices."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import distribution
from pathlib import Path


def inventory() -> dict:
    packages = {}
    for name in ("playwright", "pyee", "greenlet"):
        dist = distribution(name)
        packages[name] = {
            "version": dist.version,
            "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
        }
    root = Path(distribution("playwright").locate_file("playwright/driver"))
    if (root / "package/.local-browsers").exists():
        raise ValueError("Browser binaries must not be bundled implicitly.")
    files = {}
    for name in (
        "node.exe" if (root / "node.exe").exists() else "node",
        "LICENSE",
        "package/package.json",
        "package/LICENSE",
        "package/NOTICE",
        "package/ThirdPartyNotices.txt",
    ):
        path = root / name
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        files[name] = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
    manifest = json.loads((root / "package/package.json").read_text())
    return {
        "packages": packages,
        "driver_package_version": manifest["version"],
        "driver_files": files,
        "browser_binary": "user-installed Microsoft Edge; not bundled",
        "automatic_downloads": False,
    }


if __name__ == "__main__":
    print(json.dumps(inventory(), indent=2))
