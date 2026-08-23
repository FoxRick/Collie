"""DPAPI-backed OAuth token storage for the provider sign-in paths.

Replaces ``oauth_cli_kit.FileTokenStorage`` (plaintext JSON in the user's
profile) with the same interface but DPAPI-encrypted blobs under
``~/.collie/credentials``. Tokens previously saved as plaintext are migrated
on first read, and ``oauth_status``/refresh keep working unchanged because the
``oauth_cli_kit`` flows only rely on ``load``/``save``/``get_token_path``.

On non-Windows platforms DPAPI is unavailable; the storage falls back to the
original plaintext file so sign-in keeps working there.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from collie_core.services.credentials import CredentialStore, DpapiUnavailableError

__all__ = ["DpapiTokenStorage", "legacy_oauth_data_root"]


def legacy_oauth_data_root() -> Path:
    """Return the legacy ``oauth_cli_kit`` data root.

    Tests and packaged smoke runs can override this location without patching
    ``platformdirs`` or risking the real user profile.  The default preserves
    the path used by ``oauth_cli_kit.FileTokenStorage`` in production.
    """
    configured = os.environ.get("COLLIE_OAUTH_ROOT") or os.environ.get("COLLIE_LEGACY_OAUTH_ROOT")
    if configured:
        return Path(configured).expanduser()

    from platformdirs import user_data_dir

    return Path(user_data_dir("oauth-cli-kit", appauthor=False))


def _token_from_dict(data: dict[str, Any]) -> Any:
    from oauth_cli_kit.models import OAuthToken

    return OAuthToken(
        access=str(data["access"]),
        refresh=str(data["refresh"]),
        expires=int(data["expires"]),
        account_id=data.get("account_id"),
    )


def _token_to_dict(token: Any) -> dict[str, Any]:
    return {
        "access": token.access,
        "refresh": token.refresh,
        "expires": int(token.expires),
        "account_id": getattr(token, "account_id", None),
    }


class DpapiTokenStorage:
    """oauth_cli_kit-compatible token storage encrypted with Windows DPAPI."""

    def __init__(self, token_filename: str) -> None:
        self._token_filename = token_filename
        self._service_id = f"oauth-{Path(token_filename).stem}"
        self._store = CredentialStore()
        self._plain: Any = None

    def _plain_storage(self) -> Any:
        if self._plain is None:
            from oauth_cli_kit.storage import FileTokenStorage

            self._plain = FileTokenStorage(
                token_filename=self._token_filename,
                data_dir=legacy_oauth_data_root(),
            )
        return self._plain

    def get_token_path(self) -> Path:
        return self._store.path_for(self._service_id)

    def load(self) -> Any:
        stored = self._store.load(self._service_id)
        if stored is not None:
            return _token_from_dict(stored)
        legacy = self._plain_storage().load()
        if legacy is not None:
            self.save(legacy)
        return legacy

    def save(self, token: Any) -> None:
        token_data = _token_to_dict(token)
        try:
            self._store.save(self._service_id, token_data)
        except DpapiUnavailableError:
            self._plain_storage().save(token)
