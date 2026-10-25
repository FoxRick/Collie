"""Private Playwright worker. JSON lines only; never print site/runtime errors.

No arbitrary JS, keyboard, CDP, cookies, profile import, files, or uploads API.
"""

from __future__ import annotations

import base64
import json
import queue
import re
import sys
import threading
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from collie_core.browser.service import origin

FINAL = re.compile(
    r"submit|send|delete|remove|buy|purchase|pay|checkout|confirm|publish|sign.?in|log.?in|transfer|save|post|order|subscribe|accept|agree",
    re.I,
)


class BrowserWorker:
    def __init__(self, runtime):
        self.runtime = runtime
        self.browser = None
        self.context = None
        self.page = None
        self.site = ""
        self.handoff = False

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        same_site = f"{parsed.scheme}://{parsed.netloc.lower()}" == self.site
        if (self.handoff and parsed.scheme in {"http", "https"}) or (
            same_site and request.method in {"GET", "HEAD"}
        ):
            route.continue_()
        else:
            route.abort()

    def execute(self, command):
        op = command["operation"]
        if op == "open":
            self.site = origin(command["url"])
            self.browser = self.runtime.chromium.launch(headless=False, channel=command["channel"])
            self.context = self.browser.new_context(
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 1000, "height": 700},
            )
            self.context.route("**/*", self.route)
            self.context.route_web_socket("**/*", lambda socket: socket.close())
            self.context.on("page", self._new_page)
            self.page = self.context.new_page()
            self.page.set_default_timeout(8000)
            self.page.goto(command["url"], wait_until="domcontentloaded", timeout=20000)
            return self.observe()
        if op != "handoff":
            self._check_page_origin()
        if op == "handoff":
            self.handoff = True
            self.page.bring_to_front()
            return {
                "handed_off": True,
                "message": "You control this browser now. Sign in or finish the action visibly. Close it when done. Automation cannot resume.",
            }
        if op == "navigate":
            if origin(command["url"]) != self.site:
                raise ValueError("Origin changed")
            self.page.goto(command["url"], wait_until="domcontentloaded", timeout=20000)
        elif op in {"fill", "click"}:
            target = self.page.locator(command.get("selector", ""))
            if target.count() != 1 or not target.is_visible():
                raise ValueError("Choose exactly one visible element")
            # Inspection is not authority. Every interaction already passed the
            # central hard-approval gate; this extra check refuses final actions.
            info = target.evaluate("""e => ({tag:e.tagName, type:e.type || '',
                label: [e.textContent, e.getAttribute('aria-label'), e.name, e.id,
                        e.getAttribute('autocomplete')].join(' '),
                form: !!e.form, secret: e.matches('input[type=password],input[autocomplete*=password],input[autocomplete*=cc-]')})""")
            if info["secret"] or FINAL.search(info["label"]):
                return {
                    "blocked": True,
                    "message": "This may be a final or sensitive action. Use visible handoff.",
                }
            if op == "fill":
                if info["tag"] not in {"INPUT", "TEXTAREA"} or info["type"] not in {
                    "text",
                    "search",
                    "textarea",
                    "email",
                    "tel",
                    "url",
                }:
                    return {
                        "blocked": True,
                        "message": "Only ordinary non-secret text fields can be prepared.",
                    }
                target.fill(command.get("text", ""), timeout=8000)
            else:
                # An untyped/form button may submit even if its label looks safe.
                if info["type"] in {"submit", "image", "reset", "file", "password"} or (
                    info["tag"] == "BUTTON" and info["type"] != "button"
                ):
                    return {"blocked": True, "message": "Submission needs your visible handoff."}
                if info["tag"] not in {"BUTTON", "A", "SUMMARY"}:
                    return {
                        "blocked": True,
                        "message": "Choose a visible link or ordinary button, or use handoff.",
                    }
                target.click(timeout=8000, no_wait_after=True)
        elif op == "screenshot":
            picture = self.page.screenshot(
                type="jpeg",
                quality=45,
                full_page=False,
                mask=[self.page.locator("input,textarea,[contenteditable],iframe")],
            )
            if len(picture) > 150000:
                raise ValueError("Capture too large")
            return {"screenshot": "data:image/jpeg;base64," + base64.b64encode(picture).decode()}
        elif op != "observe":
            raise ValueError("Unknown operation")
        return self.observe()

    def _new_page(self, page):
        # One owned visible tab per session; prevent unscoped popups.
        if self.page is not None and not self.handoff:
            page.close()
        else:
            page.on("dialog", lambda dialog: dialog.dismiss())

    def observe(self):
        self._check_page_origin()
        # Never expose input values, DOM attributes, cookies, URL queries or
        # storage. Site text is untrusted content, bounded to 12 KB.
        text = self.page.locator("body").evaluate("""body => {
            const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
            let text = '', node;
            while ((node = walker.nextNode()) && text.length < 12000) {
                const e = node.parentElement;
                if (!e || e.closest('input,textarea,script,style,[contenteditable],iframe,[hidden]')) continue;
                if (e.checkVisibility()) text += node.textContent + ' ';
            }
            return text.slice(0,12000);
        }""")
        parsed = urlsplit(self.page.url)
        elements = self.page.locator("body").evaluate("""body => {
            return [...body.querySelectorAll('a,button,input,textarea,summary')]
                .filter(e => e.checkVisibility() && !e.matches('input[type=password],input[autocomplete*=password],input[autocomplete*=cc-]'))
                .slice(0,60).map(e => {
                    const parts = []; let node = e;
                    while (node && node !== body && parts.length < 15) {
                        const tag = node.tagName.toLowerCase();
                        const siblings = [...node.parentElement.children].filter(n => n.tagName === node.tagName);
                        parts.unshift(tag + ':nth-of-type(' + (siblings.indexOf(node)+1) + ')');
                        node = node.parentElement;
                    }
                    return {selector: 'body > ' + parts.join(' > '), tag: e.tagName.toLowerCase(),
                        label: (e.getAttribute('aria-label') || e.labels?.[0]?.innerText ||
                            (e.matches('input,textarea') ? 'Text field' : e.innerText) || '').slice(0,120)};
                });
        }""")
        return {
            "url": f"{parsed.scheme}://{parsed.netloc}",
            "text": text,
            "elements": elements,
            "untrusted_page_content": True,
        }

    def _check_page_origin(self):
        # A person may navigate the visible window manually between commands.
        # That must not turn an approved web session into local-file/internal
        # browser-page or different-origin observation authority.
        parsed = urlsplit(self.page.url)
        if f"{parsed.scheme}://{parsed.netloc.lower()}" != self.site:
            raise ValueError("Visible page left the approved origin")


def main():
    # A reader thread lets Playwright keep pumping network/dialog events while
    # idle, including during the user's visible handoff.
    inbox = queue.Queue(maxsize=1)

    def read():
        for line in sys.stdin:
            if len(line) > 64000:
                break
            inbox.put(line)
        inbox.put(None)

    threading.Thread(target=read, daemon=True).start()
    with sync_playwright() as runtime:
        worker = BrowserWorker(runtime)
        try:
            while True:
                try:
                    line = inbox.get(timeout=0.05) if worker.page is None else inbox.get_nowait()
                except queue.Empty:
                    if worker.page is not None:
                        worker.page.wait_for_timeout(50)
                    continue
                if line is None:
                    break
                try:
                    result = worker.execute(json.loads(line))
                except Exception:
                    # Runtime exceptions may contain typed values or site markup.
                    result = {"error": "Browser action failed. No automatic retry was performed."}
                print(json.dumps(result), flush=True)
        finally:
            if worker.browser is not None:
                worker.browser.close()


if __name__ == "__main__":
    main()
