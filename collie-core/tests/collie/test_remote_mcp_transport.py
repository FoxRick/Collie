"""Direct remote requests retain origin identity without process-global DNS state."""

import asyncio
import socket

import httpx
import pytest

from collie_core.connectors import remote


def test_pinned_transport_supports_concurrent_requests_across_probe_loops(monkeypatch):
    original_resolver = socket.getaddrinfo
    observed = []
    pools = []

    class Inner:
        def __init__(self):
            pools.append(self)

        async def handle_async_request(self, request):
            await asyncio.sleep(0.001)
            assert socket.getaddrinfo is original_resolver
            observed.append(
                (
                    self,
                    request.url.host,
                    request.headers["Host"],
                    request.extensions["sni_hostname"],
                )
            )
            return httpx.Response(200)

        async def aclose(self):
            pass

    monkeypatch.setattr(remote.httpx, "AsyncHTTPTransport", Inner)
    monkeypatch.setattr(remote, "validate_remote_endpoint", lambda *a, **kw: ("8.8.8.8",))

    async def run():
        transport = remote.RemoteEndpointTransport("https://one.example/mcp")
        try:
            requests = [
                httpx.Request("POST", f"https://{host}/mcp", content=b"request")
                for host in ["one.example", "one.example", "two.example"]
            ]
            responses = await asyncio.gather(*(transport.handle_async_request(r) for r in requests))
            assert [response.request for response in responses] == requests
        finally:
            await transport.aclose()

    asyncio.run(run())
    asyncio.run(run())
    assert len(pools) == 4
    assert all(address == "8.8.8.8" and host == sni for _, address, host, sni in observed)
    for pool in pools:
        assert len({host for candidate, _, host, _ in observed if candidate is pool}) == 1


@pytest.mark.asyncio
async def test_pinned_transport_falls_back_only_before_request_is_sent(monkeypatch):
    attempts = []
    failure = httpx.ConnectError

    class Inner:
        async def handle_async_request(self, request):
            attempts.append(request.url.host)
            if request.url.host == "2001:4860:4860::8888":
                raise failure("test transport failure", request=request)
            return httpx.Response(200)

        async def aclose(self):
            pass

    monkeypatch.setattr(remote.httpx, "AsyncHTTPTransport", Inner)
    monkeypatch.setattr(
        remote,
        "validate_remote_endpoint",
        lambda *a, **kw: (
            "2001:4860:4860::8888",
            "8.8.8.8",
        ),
    )
    transport = remote.RemoteEndpointTransport("https://one.example/mcp")
    try:
        await transport.handle_async_request(httpx.Request("POST", "https://one.example/mcp"))
        assert attempts == ["2001:4860:4860::8888", "8.8.8.8"]
        attempts.clear()
        failure = httpx.ReadError
        with pytest.raises(httpx.ReadError):
            await transport.handle_async_request(httpx.Request("POST", "https://one.example/mcp"))
        assert attempts == ["2001:4860:4860::8888"]
    finally:
        await transport.aclose()
