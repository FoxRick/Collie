"""Cross-suite test infrastructure."""

from __future__ import annotations

import os
import ssl
import sys
from collections.abc import Iterator

import certifi
import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_nanobot_runtime_data(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Keep process-global runtime and legacy OAuth data in the test sandbox."""
    from nanobot.config.loader import get_config_path, set_config_path

    original = get_config_path()
    original_oauth_root = os.environ.get("COLLIE_OAUTH_ROOT")
    os.environ["COLLIE_OAUTH_ROOT"] = str(
        tmp_path_factory.mktemp("legacy-oauth-runtime")
    )
    set_config_path(tmp_path_factory.mktemp("nanobot-runtime") / "config.json")
    try:
        yield
    finally:
        set_config_path(original)
        if original_oauth_root is None:
            os.environ.pop("COLLIE_OAUTH_ROOT", None)
        else:
            os.environ["COLLIE_OAUTH_ROOT"] = original_oauth_root


@pytest.fixture(scope="session", autouse=True)
def _use_windows_system_ca_for_default_http_clients() -> Iterator[None]:
    """Avoid reparsing certifi's CA bundle for every offline HTTP client.

    Loading certifi takes roughly 0.7 seconds per client on Windows. The test
    suite constructs hundreds of clients while mocking their I/O. System roots
    preserve certificate verification for accidental local requests; explicit
    ``cafile``, ``capath``, and ``cadata`` arguments still use the real loader.
    """
    if sys.platform != "win32":
        yield
        return

    original = ssl.create_default_context
    certifi_path = os.path.normcase(os.path.abspath(certifi.where()))

    def create_default_context(
        purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH,
        *,
        cafile: str | None = None,
        capath: str | None = None,
        cadata: str | bytes | None = None,
    ) -> ssl.SSLContext:
        requested_path = os.path.normcase(os.path.abspath(cafile)) if cafile else None
        if requested_path == certifi_path and capath is None and cadata is None:
            return original(purpose)
        return original(
            purpose,
            cafile=cafile,
            capath=capath,
            cadata=cadata,
        )

    ssl.create_default_context = create_default_context
    try:
        yield
    finally:
        ssl.create_default_context = original
