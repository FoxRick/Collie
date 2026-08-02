#!/usr/bin/env python3
"""Validate the Windows artifacts produced for a Collie alpha release.

This intentionally uses only the Python standard library so it can run in CI
or on a release machine before any installer is uploaded.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
from pathlib import Path


SHA256_RE = re.compile(r"^([A-Fa-f0-9]{64})\s+[* ]?(.+?)\s*$")
KEY_RE = re.compile(r"^\s*(version|path|sha512|size):\s*['\"]?([^'\"\s]+)['\"]?\s*$")
FILE_URL_RE = re.compile(r"^\s*-\s+url:\s*['\"]?([^'\"\s]+)['\"]?\s*$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha512_base64(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(block)
    return base64.b64encode(digest.digest()).decode("ascii")


def parse_alpha_metadata(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    file_url: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := FILE_URL_RE.match(line):
            file_url = match.group(1)
        elif match := KEY_RE.match(line):
            key, value = match.groups()
            values.setdefault(key, value)
    if file_url:
        values["url"] = file_url
    required = {"version", "url", "path", "sha512", "size"}
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError(f"missing required alpha metadata field(s): {', '.join(missing)}")
    return values


def parse_sha256sums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    # Windows PowerShell's documented Set-Content command may emit a UTF-8
    # BOM. utf-8-sig accepts that output while remaining compatible with
    # ordinary UTF-8 manifests.
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = SHA256_RE.match(line)
        if not match:
            raise ValueError(f"invalid SHA256SUMS entry at line {line_number}")
        checksum, name = match.groups()
        if name in checksums:
            raise ValueError(f"duplicate SHA256SUMS entry for {name}")
        checksums[name.replace("\\", "/")] = checksum.lower()
    return checksums


def is_plain_filename(name: str) -> bool:
    """Return whether metadata names a file directly inside the release directory."""
    return (
        bool(name)
        and Path(name).name == name
        and "/" not in name
        and "\\" not in name
        and name not in {".", ".."}
        and not Path(name).drive
        and not Path(name).is_absolute()
    )


def validate(release_dir: Path, metadata_name: str, manifest_name: str) -> list[str]:
    """Return every consistency error for a release directory."""
    errors: list[str] = []
    metadata_path = release_dir / metadata_name
    manifest_path = release_dir / manifest_name
    if not metadata_path.is_file():
        return [f"missing update metadata: {metadata_name}"]
    if not manifest_path.is_file():
        return [f"missing checksum manifest: {manifest_name}"]

    try:
        metadata = parse_alpha_metadata(metadata_path)
    except ValueError as exc:
        return [f"invalid {metadata_name}: {exc}"]
    try:
        checksums = parse_sha256sums(manifest_path)
    except ValueError as exc:
        return [f"invalid {manifest_name}: {exc}"]

    installer_name = metadata["url"]
    if not is_plain_filename(installer_name):
        errors.append(f"metadata files URL must be a plain release-directory filename: {installer_name}")
        return errors
    if not is_plain_filename(metadata["path"]):
        errors.append(f"metadata path must be a plain release-directory filename: {metadata['path']}")
        return errors
    if metadata["path"] != installer_name:
        errors.append(f"metadata path ({metadata['path']}) does not match files URL ({installer_name})")
    installer_path = release_dir / installer_name
    blockmap_name = f"{installer_name}.blockmap"
    required_names = (installer_name, blockmap_name, metadata_name)
    for name in required_names:
        artifact = release_dir / name
        if not artifact.is_file():
            errors.append(f"missing required artifact: {name}")
            continue
        expected = checksums.get(name.replace("\\", "/"))
        if expected is None:
            errors.append(f"{manifest_name} does not reference {name}")
        elif sha256(artifact) != expected:
            errors.append(f"SHA-256 mismatch for {name}")

    if installer_path.is_file():
        try:
            metadata_size = int(metadata["size"])
        except ValueError:
            errors.append(f"metadata size is not an integer: {metadata['size']}")
            metadata_size = None
        if metadata_size is not None and installer_path.stat().st_size != metadata_size:
            errors.append(
                f"metadata size ({metadata['size']}) does not match {installer_name} "
                f"({installer_path.stat().st_size})"
            )
        if sha512_base64(installer_path) != metadata["sha512"]:
            errors.append(f"metadata SHA-512 does not match {installer_name}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a coherent Collie Windows release directory.")
    parser.add_argument("release_dir", type=Path, help="clean version-specific directory to validate")
    parser.add_argument("--metadata", default="alpha.yml", help="update metadata filename (default: alpha.yml)")
    parser.add_argument("--manifest", default="SHA256SUMS.txt", help="checksum manifest filename")
    args = parser.parse_args()
    errors = validate(args.release_dir, args.metadata, args.manifest)
    if errors:
        print("Release artifact validation failed:", file=sys.stderr)
        print(*(f"- {error}" for error in errors), sep="\n", file=sys.stderr)
        return 1
    print(f"Release artifact validation passed: {args.release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
