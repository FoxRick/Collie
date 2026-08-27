"""HTTP client for the Electron main-process OS keychain bridge.

The Electron shell owns the real platform keychain (DPAPI on Windows,
Keychain on macOS, libsecret/gnome-keyring on Linux) via Electron
``safeStorage``. Connector OAuth tokens must be encrypted with that same
keychain so they are recoverable only by the signed-in OS account.

On non-Windows platforms the Python core cannot call DPAPI directly, so it
asks the Electron shell to encrypt/decrypt over a loopback HTTP endpoint.
The shell binds the endpoint to 127.0.0.1 only and requires a bearer token
that it hands the core out-of-band (env vars set at core spawn), mirroring
the existing authenticated core IPC WebSocket.

If the bridge is not configured (e.g. the shell failed to obtain a real
keyring backend, so it intentionally did not start the endpoint), the
connector catalog reports those routes as coming-soon rather than letting a
connect attempt store a token in plaintext or fail mid-OAuth.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any

__all__ = ["KeychainUnavailableError", "keychain_configured", "HttpKeychain"]


class KeychainUnavailableError(RuntimeError):
    """Raised when the OS keychain bridge is not configured or unreachable."""


def keychain_configured() -> bool:
    """True when the Electron shell published a bridge address for the core."""
    return bool(os.environ.get("COLLIE_KEYCHAIN_PORT") and os.environ.get("COLLIE_KEYCHAIN_TOKEN"))


class HttpKeychain:
    """Encrypt/decrypt connector token blobs via the Electron shell."""

    _TIMEOUT = 5.0

    def __init__(self, *, port: int | None = None, token: str | None = None) -> None:
        if port is None:
            raw_port = os.environ.get("COLLIE_KEYCHAIN_PORT", "")
            self.port = int(raw_port) if raw_port.isdigit() and int(raw_port) > 0 else 0
        else:
            self.port = int(port)
        if token is None:
            token = os.environ.get("COLLIE_KEYCHAIN_TOKEN", "")
        self.token = token
        if self.port <= 0 or not self.token:
            raise KeychainUnavailableError(
                "The OS keychain bridge is not configured; connector credentials "
                "cannot be secured on this platform."
            )

    def _call(self, path: str, data_b64: str) -> str:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps({"data": data_b64}).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._TIMEOUT) as response:
                payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise KeychainUnavailableError(
                f"Could not reach the OS keychain bridge: {error}"
            ) from error
        if "error" in payload:
            raise KeychainUnavailableError(f"OS keychain bridge error: {payload['error']}")
        result = payload.get("data")
        if not isinstance(result, str) or not result:
            raise KeychainUnavailableError("OS keychain bridge returned no data.")
        return result

    def encrypt(self, data: bytes) -> bytes:
        return base64.b64decode(self._call("/encrypt", base64.b64encode(data).decode("ascii")))

    def decrypt(self, data: bytes) -> bytes:
        return base64.b64decode(self._call("/decrypt", base64.b64encode(data).decode("ascii")))
