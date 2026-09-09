"""Verified, durable shared-session archives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


class ArchiveError(ValueError):
    pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace(source: Path, destination: Path) -> None:
    if os.name != "nt":
        os.replace(source, destination)
        _fsync_directory(destination.parent)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    kernel32.MoveFileExW.restype = wintypes.BOOL
    # MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
    if not kernel32.MoveFileExW(str(source), str(destination), 0x1 | 0x8):
        raise OSError(
            ctypes.get_last_error(), f"Could not durably install archive at {destination}"
        )


class ArchiveManager:
    """Writes archives atomically and acknowledges only verified read-back bytes."""

    def __init__(self, root: Path) -> None:
        self._base_root = Path(root)
        self._base_root.mkdir(parents=True, exist_ok=True)
        self._account_id = ""

    def bind_account(self, account_id: str) -> None:
        value = str(account_id).strip()
        if value and any(
            ch not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"
            for ch in value
        ):
            raise ArchiveError("The archive account ID is invalid.")
        self._account_id = value

    @property
    def root(self) -> Path:
        if not self._account_id:
            raise ArchiveError("Sign in before accessing shared archives.")
        path = self._base_root / self._account_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def digest(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _final_seq(manifest: dict[str, Any]) -> int:
        """Read the backend's canonical field, accepting the draft-era alias."""
        present = [key for key in ("final_seq", "final_sequence") if key in manifest]
        if not present:
            raise ArchiveError("The archive manifest is incomplete.")
        values = {int(manifest[key]) for key in present}
        if len(values) != 1:
            raise ArchiveError("The archive manifest has conflicting final sequences.")
        return values.pop()

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any]) -> None:
        required = (
            "version",
            "session_id",
            "membership_revision",
            "recipients",
            "events",
            "files",
        )
        if any(key not in manifest for key in required):
            raise ArchiveError("The archive manifest is incomplete.")
        if int(manifest["version"]) != 1:
            raise ArchiveError("This archive version is not supported.")
        final = ArchiveManager._final_seq(manifest)
        if final < 0 or int(manifest["membership_revision"]) < 1:
            raise ArchiveError("The archive manifest has invalid revisions.")
        events = manifest["events"]
        if not isinstance(events, list) or not isinstance(manifest["files"], list):
            raise ArchiveError("The archive manifest has invalid content lists.")
        if len(events) > 1_000 or len(manifest["files"]) > 100:
            raise ArchiveError("The archive exceeds the supported pilot limits.")
        sequences = [int(event.get("seq") or 0) for event in events]
        if sequences != list(range(1, final + 1)):
            raise ArchiveError("The archive event sequence is incomplete or unordered.")
        file_ids = [str(item.get("file_id") or "") for item in manifest["files"]]
        if len(file_ids) != len(set(file_ids)) or any(
            not item
            or any(
                ch not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"
                for ch in item
            )
            for item in file_ids
        ):
            raise ArchiveError("The archive contains invalid or duplicate file IDs.")
        sizes = [int(item.get("byte_length") or 0) for item in manifest["files"]]
        if (
            any(size < 0 or size > 5 * 1024 * 1024 for size in sizes)
            or sum(sizes) > 20 * 1024 * 1024
        ):
            raise ArchiveError("The archive files exceed the supported size limits.")

    def session_path(self, session_id: str) -> Path:
        safe = str(session_id).strip()
        if not safe or any(
            ch not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"
            for ch in safe
        ):
            raise ArchiveError("The archive session ID is invalid.")
        return self.root / safe

    def write(
        self,
        manifest_json: str,
        expected_digest: str,
        *,
        attachments: dict[str, Path] | None = None,
    ) -> dict[str, Any]:
        raw = manifest_json.encode("utf-8")
        if len(raw) > 16 * 1024 * 1024:
            raise ArchiveError("The archive manifest is too large.")
        if self.digest(raw) != expected_digest.lower():
            raise ArchiveError("The archive manifest did not match its digest.")
        try:
            manifest = json.loads(manifest_json)
        except (TypeError, ValueError) as error:
            raise ArchiveError("The archive manifest is not valid JSON.") from error
        if not isinstance(manifest, dict):
            raise ArchiveError("The archive manifest must be an object.")
        self._validate_manifest(manifest)
        session_dir = self.session_path(str(manifest["session_id"]))
        session_dir.mkdir(parents=True, exist_ok=True)
        expected_files = {str(item["file_id"]): item for item in manifest["files"]}
        supplied = attachments or {}
        if set(supplied) != set(expected_files):
            raise ArchiveError("The archive is missing one or more shared files.")
        temp_dir = Path(tempfile.mkdtemp(prefix=".incoming-", dir=session_dir))
        try:
            file_dir = temp_dir / "files"
            file_dir.mkdir()
            for file_id, descriptor in expected_files.items():
                source = Path(supplied[file_id])
                if not source.is_file():
                    raise ArchiveError("A shared archive file is missing.")
                data = source.read_bytes()
                if len(data) != int(descriptor["byte_length"]):
                    raise ArchiveError("A shared archive file has the wrong size.")
                if self.digest(data) != str(descriptor["sha256"]).lower():
                    raise ArchiveError("A shared archive file failed verification.")
                target = file_dir / file_id
                with target.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            _fsync_directory(file_dir)
            manifest_path = temp_dir / "manifest.json"
            with manifest_path.open("wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if manifest_path.read_bytes() != raw:
                raise ArchiveError("The archive manifest could not be read back.")
            _fsync_directory(temp_dir)
            final = session_dir / expected_digest.lower()
            if final.exists():
                self.verify(final)
                shutil.rmtree(temp_dir)
            else:
                _durable_replace(temp_dir, final)
            self.verify(final)
            return {
                "session_id": str(manifest["session_id"]),
                "digest": expected_digest.lower(),
                "byte_length": len(raw),
                "final_seq": self._final_seq(manifest),
                "path": str(final),
            }
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def verify(self, archive_dir: Path) -> dict[str, Any]:
        path = Path(archive_dir)
        raw = (path / "manifest.json").read_bytes()
        if self.digest(raw) != path.name.lower():
            raise ArchiveError("The local archive manifest is corrupt.")
        manifest = json.loads(raw.decode("utf-8"))
        self._validate_manifest(manifest)
        for descriptor in manifest["files"]:
            candidate = path / "files" / str(descriptor["file_id"])
            data = candidate.read_bytes()
            if (
                len(data) != int(descriptor["byte_length"])
                or self.digest(data) != str(descriptor["sha256"]).lower()
            ):
                raise ArchiveError("A local archive file is corrupt or missing.")
        return manifest

    def list(self) -> list[dict[str, Any]]:
        result = []
        for session in self.root.iterdir():
            if not session.is_dir():
                continue
            for archive in session.iterdir():
                if archive.is_dir() and not archive.name.startswith("."):
                    try:
                        manifest = self.verify(archive)
                    except (ArchiveError, OSError, ValueError):
                        continue
                    result.append(
                        {"path": str(archive), "manifest": manifest, "digest": archive.name}
                    )
        return result

    def export(self, archive_dir: Path, destination: Path) -> Path:
        source = Path(archive_dir)
        self.verify(source)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(f".{destination.name}.tmp")
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(source.rglob("*")):
                if file.is_file():
                    bundle.write(file, file.relative_to(source).as_posix())
        with temp.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        _durable_replace(temp, destination)
        return destination

    def import_bundle(self, source: Path) -> dict[str, Any]:
        source = Path(source)
        temp_root = Path(tempfile.mkdtemp(prefix=".import-", dir=self.root))
        try:
            with zipfile.ZipFile(source) as bundle:
                infos = bundle.infolist()
                if len(infos) > 102 or sum(item.file_size for item in infos) > 36 * 1024 * 1024:
                    raise ArchiveError("The archive bundle is too large.")
                names = {item.filename for item in infos}
                if "manifest.json" not in names:
                    raise ArchiveError("The archive bundle has no manifest.")
                for info in infos:
                    relative = Path(info.filename)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ArchiveError("The archive bundle contains an unsafe path.")
                    if info.external_attr >> 16 & 0o170000 == 0o120000:
                        raise ArchiveError("The archive bundle contains a symbolic link.")
                bundle.extractall(temp_root)
            raw = (temp_root / "manifest.json").read_bytes()
            manifest = json.loads(raw.decode("utf-8"))
            self._validate_manifest(manifest)
            expected_names = {"manifest.json"} | {
                f"files/{item['file_id']}" for item in manifest.get("files", [])
            }
            if {name.rstrip("/") for name in names if not name.endswith("/")} != expected_names:
                raise ArchiveError("The archive bundle contains unexpected files.")
            attachments = {
                str(item["file_id"]): temp_root / "files" / str(item["file_id"])
                for item in manifest.get("files", [])
            }
            return self.write(raw.decode("utf-8"), self.digest(raw), attachments=attachments)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
