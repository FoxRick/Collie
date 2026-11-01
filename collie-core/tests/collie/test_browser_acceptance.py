"""Real Edge + broker + text-only tool journeys on loopback, no model/API required."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from collie_core.browser import BrowserService
from collie_core.db import CollieDB
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext
from collie_core.tools.browser import BrowserTool, bind_browser_service
from nanobot.agent.tools.context import RequestContext, request_context

pytestmark = pytest.mark.skipif(
    os.environ.get("COLLIE_BROWSER_ACCEPTANCE") != "1",
    reason="Opt-in real installed Edge acceptance",
)


@pytest.mark.asyncio
async def test_text_only_local_journey(tmp_path):
    expected = os.environ.get("COLLIE_EXPECT_BROWSER_ROOT")
    if expected:
        from collie_core.browser import service as loaded_service

        assert Path(loaded_service.__file__).resolve().is_relative_to(Path(expected).resolve())
    calls = []
    html = b"""<html><body><main aria-label="Main form"><h1>Local journey</h1>
      <label>Draft<input id="draft"></label><input type="password" value="NEVER_EXPOSE">
      <label>Colour<select><option>Blue</option><option>Green</option></select></label>
      <label>Optional<input type="checkbox"></label>
      <label>Attachment<input type="file" onchange="document.querySelector('#result').textContent='File ready'"></label>
      <button type="button" onclick="document.querySelector('#result').textContent='Prepared locally'">Preview</button>
      <button type="button" onclick="document.querySelector('#preview2').textContent='Changed'">Change target</button>
      <button id="preview2" type="button">Old target</button>
      <button type="button" onclick="while(true){}">Hang</button>
      <button type="button" onclick="fetch('/write',{method:'POST'}).then(()=>document.querySelector('#result').textContent='Write verified')">Try endpoint</button>
      <button type="button" onclick="window.open('/popup')">Open tab</button>
      <button type="button" onclick="fetch('https://example.invalid/blocked',{method:'POST'}).catch(()=>{})">Blocked destination</button>
      <form action="/sent" method="post"><button>Submit</button></form>
      <a href="/file.txt" download>Public file</a><p id="result">Waiting</p></main></body></html>"""

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(("GET", self.path))
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/plain" if self.path == "/file.txt" else "text/html"
            )
            self.end_headers()
            body = b"local download" if self.path == "/file.txt" else html
            if self.path == "/mutating":
                body += b"<script>setTimeout(()=>document.querySelector('#preview2').textContent='Changed while approving',700)</script>"
            if self.path == "/large":
                body = b"<html><body>" + b"<span>bounded text </span>" * 2200 + b"</body></html>"
            if self.path == "/deep":
                body = (
                    b"<html><body>"
                    + b"<div>" * 24
                    + b"<span>excluded deep text</span>" * 2200
                    + b"</div>" * 24
                    + b"</body></html>"
                )
            self.wfile.write(body)

        def do_POST(self):
            calls.append(("POST", self.path))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Submission verified</body></html>")

        def log_message(self, *_args):
            pass

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Page) for _ in range(2)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    db = CollieDB(tmp_path / "browser.db")
    rejected = set()
    approvals = []
    slow_review = False

    async def review(event):
        if event["type"] == "approval_requested":
            row = event["approval"]
            approvals.append(row)
            if slow_review and row["action"] == "browser.click":
                await asyncio.sleep(1)
            await broker.resolve(row["id"], "reject" if row["action"] in rejected else "allow_once")

    broker = ApprovalBroker(db, PermissionEvaluator(), review, timeout_seconds=5)
    browser = BrowserService(broker)
    bind_browser_service(browser)
    ctx = ExecutionContext(conversation_id="journey", run_id="one")
    ui = ExecutionContext(conversation_id="journey", origin="browser_ui")
    tool = BrowserTool()
    local = f"http://127.0.0.1:{servers[0].server_port}"
    other = f"http://127.0.0.1:{servers[1].server_port}"
    metrics = []
    latest = {}
    session = None

    async def act(op, label=None, *, context=ctx, **kwargs):
        nonlocal latest, session
        command = {"operation": op, **kwargs}
        if session:
            command["session_id"] = session
        if label:
            element = next(e for e in latest["elements"] if e["label"] == label)
            command.update(ref=element["ref"], observation_id=latest["observation_id"])
        result = await browser.execute(context, command)
        if result.get("session_id"):
            session = result["session_id"]
        if result.get("observation_id"):
            latest = result
        if result.get("metrics"):
            metrics.append(result["metrics"])
        return result

    try:
        # The actual agent tool uses the same controller without a vision/provider call.
        with request_context(
            RequestContext(
                channel="desktop", chat_id="journey", metadata={"permission_context": asdict(ctx)}
            )
        ):
            opened = await tool.execute(operation="open", url=local)
        assert not opened.is_error
        latest = json.loads(opened)
        session = latest["session_id"]
        assert "Local journey" in latest["text"]
        assert "NEVER_EXPOSE" not in json.dumps(latest)
        assert not any("selector" in e for e in latest["elements"])
        stale = {
            "operation": "click",
            "session_id": session,
            "ref": next(e["ref"] for e in latest["elements"] if e["label"] == "Preview"),
            "observation_id": latest["observation_id"],
        }
        filled = await act("fill", "Draft", text="ordinary draft")
        assert filled["postcondition"] == "verified"
        assert (await browser.execute(ctx, stale))["rejected"]
        assert (await act("select", "Colour", text="Green"))["postcondition"] == "verified"
        assert (await act("check", "Optional", checked=True))["postcondition"] == "verified"
        assert "Prepared locally" in (await act("click", "Preview"))["text"]
        assert "Write verified" in (await act("click", "Try endpoint"))["text"]
        assert ("POST", "/write") in calls
        blocked = await act("click", "Blocked destination")
        assert blocked["metrics"]["blocked_requests"] >= 1
        rejected.add("browser.submit")
        with pytest.raises(PermissionDeniedError):
            await act("submit", "Submit")
        assert ("POST", "/sent") not in calls
        rejected.clear()
        assert (await act("click", "Submit"))["blocked"]
        submitted = await act("submit", "Submit")
        assert "Submission verified" in submitted["text"]
        await act("navigate", url=local)
        await act("navigate", url=local + "/mutating")
        slow_review = True
        assert (await act("click", "Old target"))["rejected"]
        slow_review = False
        await act("observe")
        await act("navigate", url=local + "/large")
        assert (
            latest["truncated"] and latest["visited_nodes"] <= 1600 and len(latest["text"]) <= 6000
        )
        await act("navigate", url=local + "/deep")
        assert latest["truncated"] and latest["visited_nodes"] <= 1600
        await act("navigate", url=local)
        # Keyboard and scrolling are bounded, non-arbitrary operations.
        await act("key", "Draft", key="Tab")
        await act("scroll", direction="down")
        await act("click", "Open tab")
        assert len(latest["tabs"]) == 2
        popup = next(t for t in latest["tabs"] if not t["active"])
        await act("tab", tab_id=popup["tab_id"])
        await act("close_tab")
        with pytest.raises(ValueError, match="Approve"):
            await act("new_tab", url=other)
        rejected.add("browser.approve_destination")
        with pytest.raises(PermissionDeniedError):
            await act("approve_destination", url=other)
        rejected.clear()
        await act("approve_destination", url=other)
        await act("new_tab", url=other)
        assert latest["url"] == other
        # Only the UI path may upload/download a selected file.
        upload = tmp_path / "selected.txt"
        upload.write_text("selected local file")
        with pytest.raises(ValueError, match="Browser panel"):
            await act("upload", "Attachment", file_path=str(upload))
        assert (
            "File ready"
            in (await act("upload", "Attachment", context=ui, file_path=str(upload)))["text"]
        )
        dest = tmp_path / "download.txt"
        saved = await act("download", "Public file", context=ui, file_path=str(dest))
        assert saved["download_bytes"] == 14 and dest.read_text() == "local download"
        await act("observe")
        capture = await act("screenshot")
        assert capture["screenshot"].startswith("data:image/jpeg;base64,/9j/")
        with pytest.raises(ValueError, match="another task"):
            await act("observe", context=ExecutionContext(conversation_id="journey", run_id="two"))
        with pytest.raises(ValueError, match="not owned"):
            await act("observe", context=ExecutionContext(conversation_id="other"))
        with pytest.raises(ValueError, match="private local"):
            await browser.execute(
                ExecutionContext(conversation_id="journey", shared_session_id="shared"),
                {"operation": "list"},
            )
        old_observation = latest["observation_id"]
        assert (await act("handoff", context=ui))["handed_off"]
        with pytest.raises(ValueError, match="You have control"):
            await act("observe")
        rejected.add("browser.resume")
        with pytest.raises(PermissionDeniedError):
            await act("resume", context=ui, url=other)
        assert browser.sessions[session].handed_off
        rejected.clear()
        await act("resume", context=ui, url=other)
        assert latest["observation_id"] != old_observation
        # Explicit approved resume can bind a later task, never silent adoption.
        ctx2 = ExecutionContext(conversation_id="journey", run_id="two")
        await act("resume", context=ctx2, url=other)
        assert browser.sessions[session].task_id == "two"
        assert all(
            "ordinary draft" not in json.dumps(a) and "Local journey" not in json.dumps(a)
            for a in approvals
        )
        await act("close", context=ui)
        assert not browser.sessions
        # Pending approval can be stopped without executing it later.
        pending_event = asyncio.Event()

        async def wait_review(event):
            if event["type"] == "approval_requested":
                pending_event.set()

        broker.broadcaster = review
        session = None
        await act("open", url=local)
        broker.broadcaster = wait_review
        pending = asyncio.create_task(act("click", "Preview"))
        await asyncio.wait_for(pending_event.wait(), 5)
        broker.broadcaster = review
        assert (await act("handoff", context=ui))["handed_off"]
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not db.list_pending_approvals()
        await act("resume", context=ui, url=local)
        pending_event.clear()
        broker.broadcaster = wait_review
        pending = asyncio.create_task(act("click", "Preview"))
        await asyncio.wait_for(pending_event.wait(), 5)
        await act("close", context=ui)
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not db.list_pending_approvals()
        assert not browser.sessions
        broker.broadcaster = review
        session = None
        await act("open", url=local)
        running = asyncio.create_task(act("click", "Hang"))
        await asyncio.sleep(0.5)
        await act("close", context=ui)
        with pytest.raises(ValueError, match="No action was retried"):
            await running
        assert not browser.sessions
        print("BROWSER_CONTENT_FREE_METRICS", json.dumps(metrics))
    finally:
        await browser.close()
        db.close()
        for server in servers:
            server.shutdown()
            server.server_close()


def test_fixture_user_sign_in_surrogate():
    """Harness drives the user's side of takeover; no credential tool/API is exposed."""
    from playwright.sync_api import sync_playwright

    from collie_core.browser.worker import BrowserWorker

    class Login(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if self.path == "/private":
                self.wfile.write(
                    b"<html><body>Signed in continuation</body></html>"
                    if "fixture_session=local" in self.headers.get("Cookie", "")
                    else b"Denied"
                )
            else:
                self.wfile.write(
                    b'<html><body><form method="post"><input aria-label="Password" type="password"><button>Sign in</button></form></body></html>'
                )

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "fixture_session=local; HttpOnly; SameSite=Lax")
            self.end_headers()
            self.wfile.write(
                b'<html><body>Login complete<a href="/private">Continue</a></body></html>'
            )

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Login)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as runtime:
            worker = BrowserWorker(runtime)
            try:
                before = worker.execute({"operation": "open", "url": url, "channel": "msedge"})
                assert not any(e["type"] == "password" for e in before["elements"])
                worker.execute({"operation": "handoff"})
                # Test harness emulates manual input, outside the browser tool contract.
                worker.page.locator("input[type=password]").fill("fixture-secret")
                worker.page.locator("button").click()
                resumed = worker.execute({"operation": "resume", "url": url})
                assert "Login complete" in resumed["text"]
                assert "fixture-secret" not in json.dumps(resumed)
                assert "fixture_session" not in json.dumps(resumed)
                link = next(e for e in resumed["elements"] if e["label"] == "Continue")
                continued = worker.execute(
                    {
                        "operation": "click",
                        "ref": link["ref"],
                        "observation_id": resumed["observation_id"],
                    }
                )
                assert "Signed in continuation" in continued["text"]
            finally:
                if worker.browser:
                    worker.browser.close()
    finally:
        server.shutdown()
        server.server_close()
