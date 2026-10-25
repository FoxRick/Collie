"""One local acceptance flow with the real browser worker and central broker.

Run explicitly with COLLIE_BROWSER_ACCEPTANCE=1 and the browser extra installed.
No accounts or public network actions; the fixture binds loopback only.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collie_core.browser import BrowserService
from collie_core.db import CollieDB
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext

pytestmark = pytest.mark.skipif(
    os.environ.get("COLLIE_BROWSER_ACCEPTANCE") != "1",
    reason="Explicit local visible-browser acceptance; requires optional Playwright + Edge",
)


@pytest.mark.asyncio
async def test_owned_browser_lifecycle_and_submission_boundary(tmp_path):
    requests = []

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""<html><body><h1>Local browser acceptance</h1>
                <input id="draft" type="text"><input id="secret" type="password" value="NEVER_EXPOSE">
                <button id="preview" type="button" onclick="document.querySelector('#result').textContent='Prepared locally'">Preview</button>
                <form action="/sent" method="post"><button id="submit">Submit</button></form>
                <button id="asyncwrite" type="button" onclick="fetch('/write',{method:'POST'})">Try endpoint</button>
                <p id="result">Waiting</p></body></html>""")

        def do_POST(self):
            requests.append("UNEXPECTED_POST")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db = CollieDB(tmp_path / "browser.db")
    reject_submit = True
    approvals = []

    async def review(event):
        if event["type"] != "approval_requested":
            return
        approval = next(
            row for row in db.list_pending_approvals() if row["id"] == event["approval"]["id"]
        )
        approvals.append(approval)
        selector = approval["display"]["parameters"]["selector"]
        resolution = "reject" if reject_submit and selector == "#submit" else "allow_once"
        await broker.resolve(approval["id"], resolution)

    broker = ApprovalBroker(db, PermissionEvaluator(), review, timeout_seconds=5)
    browser = BrowserService(broker)
    context = ExecutionContext(conversation_id="local-browser", run_id="task-one")
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        opened = await browser.execute(context, {"operation": "open", "url": url})
        session_id = opened["session_id"]

        async def act(operation, **args):
            return await browser.execute(
                context, {"operation": operation, "session_id": session_id, **args}
            )

        assert "Local browser acceptance" in opened["text"]
        await act("navigate", url=url + "/prepare")
        await act("fill", selector="#draft", text="ordinary draft")
        preview = next(e for e in opened["elements"] if e["label"] == "Preview")
        clicked = await act("click", selector=preview["selector"])
        assert "Prepared locally" in clicked["text"]
        observed = await act("observe")
        assert "NEVER_EXPOSE" not in observed["text"]
        capture = await act("screenshot")
        assert capture["screenshot"].startswith("data:image/jpeg;base64,/9j/")
        with pytest.raises(PermissionDeniedError):
            await act("click", selector="#submit")
        reject_submit = False
        assert (await act("click", selector="#submit"))["blocked"]
        assert (await act("fill", selector="#secret", text="not-a-real-password"))["blocked"]
        await act("click", selector="#asyncwrite")
        await act("observe")
        assert "UNEXPECTED_POST" not in requests
        assert "/sent" not in requests
        with pytest.raises(ValueError, match="different origin"):
            await act("navigate", url="https://example.com")
        with pytest.raises(ValueError, match="another task"):
            await browser.execute(
                ExecutionContext(conversation_id="local-browser", run_id="other"),
                {"operation": "click", "session_id": session_id, "selector": "#preview"},
            )
        with pytest.raises(ValueError, match="not owned"):
            await browser.execute(
                ExecutionContext(conversation_id="other-chat"),
                {"operation": "observe", "session_id": session_id},
            )
        with pytest.raises(ValueError, match="private local"):
            await browser.execute(
                ExecutionContext(conversation_id="local-browser", shared_session_id="shared"),
                {"operation": "list"},
            )
        assert all(a["display"]["parameters"].get("input") != "ordinary draft" for a in approvals)
        assert (await act("handoff"))["handed_off"]
        with pytest.raises(ValueError, match="cannot resume"):
            await act("observe")
        await act("close")
        assert (await browser.execute(context, {"operation": "list"}))["sessions"] == []
        # Crash recovery starts a fresh session; no implicit replay of an action.
        fresh = await browser.execute(context, {"operation": "open", "url": url})
        process = browser.sessions[fresh["session_id"]].process
        process.kill()
        await process.wait()
        with pytest.raises(ValueError, match="No action was retried"):
            await browser.execute(
                context, {"operation": "observe", "session_id": fresh["session_id"]}
            )
        assert not browser.sessions
    finally:
        await browser.close()
        db.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
