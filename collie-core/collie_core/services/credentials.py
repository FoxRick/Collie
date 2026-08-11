"""Encrypted credential storage for connected services.

Windows DPAPI binds every credential blob to the signed-in Windows account.
No service token is persisted as plaintext.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from contextlib import suppress
from ctypes import POINTER, Structure, byref, c_char, c_void_p, cast, wintypes
from pathlib import Path
from typing import Any

from collie_core.db import collie_home

__all__ = ["CredentialStore"]


_MAGIC = b"COLLIE-DPAPI\x00"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


if sys.platform == "win32":

    class _DataBlob(Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", POINTER(c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = (c_char * len(data)).from_buffer_copy(data)
    return _DataBlob(len(data), cast(buffer, POINTER(c_char))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Encrypted service credentials require Windows DPAPI.")
    import ctypes

    source, source_buffer = _blob(data)
    destination = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        byref(source),
        "Collie service credential",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        byref(destination),
    )
    del source_buffer
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows could not encrypt the credential.")
    try:
        return bytes(destination.pbData[: destination.cbData])
    finally:
        ctypes.windll.kernel32.LocalFree(cast(destination.pbData, c_void_p))


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Encrypted service credentials require Windows DPAPI.")
    import ctypes

    source, source_buffer = _blob(data)
    destination = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        byref(destination),
    )
    del source_buffer
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows could not decrypt the credential.")
    try:
        return bytes(destination.pbData[: destination.cbData])
    finally:
        ctypes.windll.kernel32.LocalFree(cast(destination.pbData, c_void_p))


class CredentialStore:
    """Read/write per-service DPAPI credential blobs."""

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.base_dir = base_dir or (collie_home() / "credentials")
        self._protect = protect or _dpapi_protect
        self._unprotect = unprotect or _dpapi_unprotect

    def _path(self, service_id: str) -> Path:
        safe = "".join(c for c in service_id if c.isalnum() or c in "-_")
        return self.base_dir / f"{safe}.bin"

    def path_for(self, service_id: str) -> Path:
        """Public accessor for a service's encrypted blob path."""
        return self._path(service_id)

    def _legacy_path(self, service_id: str) -> Path:
        safe = "".join(c for c in service_id if c.isalnum() or c in "-_")
        return self.base_dir / f"{safe}.json"

    def save(self, service_id: str, credentials: dict[str, Any]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with suppress(OSError, NotImplementedError):
            os.chmod(self.base_dir, 0o700)
        payload = json.dumps(credentials, ensure_ascii=False).encode("utf-8")
        encrypted = _MAGIC + self._protect(payload)
        path = self._path(service_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, path)
        with suppress(OSError, NotImplementedError):
            os.chmod(path, 0o600)
        with suppress(FileNotFoundError):
            self._legacy_path(service_id).unlink()

    def load(self, service_id: str) -> dict[str, Any] | None:
        path = self._path(service_id)
        if not path.exists():
            return self._migrate_legacy(service_id)
        try:
            blob = path.read_bytes()
            if not blob.startswith(_MAGIC):
                return None
            data = json.loads(self._unprotect(blob[len(_MAGIC) :]).decode("utf-8"))
        except (OSError, RuntimeError, UnicodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _migrate_legacy(self, service_id: str) -> dict[str, Any] | None:
        legacy = self._legacy_path(service_id)
        if not legacy.exists():
            return None
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            self.save(service_id, data)
            return data
        except (OSError, RuntimeError, ValueError):
            return None

    def delete(self, service_id: str) -> None:
        with suppress(FileNotFoundError):
            self._path(service_id).unlink()
        with suppress(FileNotFoundError):
            self._legacy_path(service_id).unlink()
