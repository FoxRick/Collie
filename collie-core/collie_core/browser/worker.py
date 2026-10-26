"""Local Edge worker. Fixed DOM adapters only; page content is never authority."""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import re
import sys
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import sync_playwright

from collie_core.browser.service import origin
from collie_core.browser.transfers import download, upload_payload

FINAL = re.compile(
    r"submit|send|delete|remove|buy|purchase|pay|checkout|confirm|publish|sign.?in|log.?in|transfer|save|post|order|subscribe|accept|agree",
    re.I,
)
SECRET = "input[type=password],input[autocomplete*=password i],input[autocomplete*=cc- i]"
# Bounded traversal, including exposed ARIA controls and open shadow roots.
COLLECT = """root => {
 const controls=[]; let text='', visited=0;
 const walk=(e,depth)=>{
  if (!e || visited++>=1600 || depth>24 || controls.length>=60) return;
  if(e.nodeType===3){ if(text.length<6000) text+=e.textContent.slice(0,6000-text.length)+' '; return; }
  if(e.nodeType!==1 || e.matches('script,style,iframe,[hidden]') || !e.checkVisibility()) return;
  if(e.matches('input[type=password],input[autocomplete*=password i],input[autocomplete*=cc- i]')) return;
  if(e.matches('a,button,input,textarea,select,summary,[role=button],[role=checkbox],[role=combobox],[role=link],[role=textbox],main,[role=region]')) controls.push(e);
  if(e.matches('input,textarea,[contenteditable]')) return;
  for(const n of e.childNodes) { if(visited>=1600) break; walk(n,depth+1); }
  if(e.shadowRoot) for(const n of e.shadowRoot.childNodes) { if(visited>=1600) break; walk(n,depth+1); }
 };
 walk(root,0); return {controls,text:text.slice(0,6000),visited,truncated:visited>=1600||controls.length>=60};
}"""
DESCRIBE = r"""e => {
 const text = root => {
  let out='', visited=0;
  const walk=(n,depth)=>{
   if(!n||visited++>=60||depth>8||out.length>=120) return;
   if(n.nodeType===3){out+=n.textContent.slice(0,120-out.length);return;}
   if(n.nodeType!==1||n.matches('input,select,textarea,script,style,[hidden],[contenteditable]')||!n.checkVisibility()) return;
   for(const c of n.childNodes){if(visited>=60||out.length>=120) break;walk(c,depth+1);}
  };walk(root,0);return out;
 };
 const named=(e.getAttribute('aria-labelledby')||'').slice(0,512).split(/\s+/).slice(0,4).map(id=>text(document.getElementById(id))).join(' ').trim();
 const label=(e.getAttribute('aria-label')?.slice(0,120)||named||text(e.labels?.[0])||text(e)||'').slice(0,120);
 return {tag:e.tagName.toLowerCase(), type:e.type||'', role:(e.getAttribute('role')||'').slice(0,40),label,
 secret:e.matches('input[type=password],input[autocomplete*=password i],input[autocomplete*=cc- i]'),
 signature:[e.tagName,e.type,e.getAttribute('role')?.slice(0,40),label,e.getAttribute('href')?.slice(0,2048),e.getAttribute('formaction')?.slice(0,2048),e.getAttribute('onclick')?.slice(0,2048),e.getAttribute('name')?.slice(0,120),e.disabled,e.getAttribute('aria-checked'),e.checked,e.selectedIndex,e.matches('input,textarea')?e.value.slice(0,4000):''].join('|')};
}"""


class BrowserWorker:
    def __init__(self, runtime):
        self.runtime = runtime
        self.browser = self.context = self.page = None
        self.sites = set()
        self.handoff = False
        self.tabs = {}
        self.refs = {}
        self.observation = None
        self.observed_url = None
        self.observed_at = 0
        self.state_signature = None
        self.effects_until = 0
        self.blocked_requests = 0

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        site = f"{parsed.scheme}://{parsed.netloc.lower()}"
        allowed = (
            parsed.scheme in {"http", "https"}
            and not parsed.username
            and not parsed.password
            and (
                self.handoff
                or (
                    site in self.sites
                    and (
                        request.method in {"GET", "HEAD"}
                        or (
                            time.monotonic() < self.effects_until
                            and request.frame.page == self.page
                        )
                    )
                )
            )
        )
        if allowed:
            route.continue_()
        else:
            self.blocked_requests += 1
            route.abort()

    def execute(self, command):
        started = time.monotonic()
        try:
            result = self._execute(command)
            result["metrics"] = {
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "observation_bytes": len(json.dumps(result).encode()),
                "blocked_requests": self.blocked_requests,
            }
            return result
        finally:
            self.effects_until = 0

    def _execute(self, command):
        op = command["operation"]
        if op == "open":
            self.sites.add(origin(command["url"]))
            self.browser = self.runtime.chromium.launch(
                headless=False,
                channel=command["channel"],
                args=[
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                ],
            )
            self.context = self.browser.new_context(
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 1000, "height": 700},
            )
            self.context.route("**/*", self.route)
            self.context.route_web_socket("**/*", lambda socket: socket.close())
            self.context.on("page", self._new_page)
            self.page = self.context.new_page()
            self.page.bring_to_front()
            self.page.goto(command["url"], wait_until="domcontentloaded", timeout=20000)
            return self.observe()
        if op == "handoff":
            self.page = self.visible_page() or self.page
            self.handoff = True
            self.invalidate()
            self.page.bring_to_front()
            return {
                "handed_off": True,
                "message": "You control this browser. Sign in visibly, then choose Resume and approve the destination.",
            }
        if op == "resume":
            self.page = self.visible_page() or self.page
            site = origin(command["url"])
            if origin(self.page.url.split("?")[0].split("#")[0]) != site:
                return self.reject(
                    "The visible destination changed. Check the address and approve again."
                )
            self.sites = {site}
            for page in list(self.tabs.values()):
                if page != self.page and self._site(page.url) != site:
                    page.close()
            self.handoff = False
            self.invalidate()
            # Fresh observation only after the service's explicit reauthorization.
            return self.observe()
        if self.handoff:
            return self.reject("You have control. Resume explicitly before automating.")
        if op == "approve_destination":
            if origin(command["url"]) not in self.sites and len(self.sites) >= 8:
                return self.reject("Use up to eight approved destinations.")
            self.sites.add(origin(command["url"]))
            return {
                "message": "Destination approved for this task. Blocked requests are never replayed; navigate deliberately."
            }
        if op == "tab":
            page = self.tabs.get(command.get("tab_id"))
            if page is None or page.is_closed():
                return self.reject("That tab is closed. Read the current tabs again.")
            self.page = page
            self.invalidate()
            self.page.bring_to_front()
        elif op == "new_tab":
            if len(self.tabs) >= 5:
                return self.reject("Close a tab first (up to five tabs).")
            if origin(command["url"]) not in self.sites:
                return self.reject("Approve the destination first.")
            self.page = self.context.new_page()
            self.page.bring_to_front()
            self.page.goto(command["url"], wait_until="domcontentloaded", timeout=20000)
        elif op == "close_tab":
            if len(self.tabs) <= 1:
                return self.reject("Use Close / stop for the last tab.")
            self.page.close()
            self.page = next(iter(self.tabs.values()))
            self.page.bring_to_front()
            self.invalidate()
        self._check_page_origin()
        if op == "navigate":
            if origin(command["url"]) not in self.sites:
                return self.reject("Approve the destination first.")
            self.invalidate()
            self.page.goto(command["url"], wait_until="domcontentloaded", timeout=20000)
        elif op in {
            "describe",
            "fill",
            "click",
            "submit",
            "select",
            "check",
            "key",
            "upload",
            "download",
        }:
            target = self.target(command)
            if target is None:
                return self.reject("That observation is stale. Read the page again before acting.")
            info = target.evaluate(DESCRIBE)
            if info["secret"]:
                return self.reject("Enter passwords and payment details yourself using Take over.")
            if op == "describe":
                return {
                    "target": f"{info['tag']} {info['type']} {info['role']} (inspect the selected visible control)"
                }
            final = bool(FINAL.search(info["label"])) or info["type"] in {
                "submit",
                "image",
                "reset",
            }
            if final and op != "submit":
                return self.reject(
                    "This may be a final action. Choose Submit / final action for a separate explicit approval, or Take over."
                )
            # A short, explicit action lease permits JS form requests only to
            # approved destinations. Idle requests cannot inherit this authority.
            self.effects_until = time.monotonic() + 8
            self.observation = None
            if op == "upload":
                if info["type"] != "file":
                    return self.reject("Choose a file upload control.")
                target.set_input_files(upload_payload(command["file_path"]), timeout=8000)
                verified = target.evaluate("e => e.files.length") == 1
            elif op == "download":
                if info["tag"] != "a":
                    return self.reject("Choose a public download link.")
                address = target.evaluate("e => e.href.length<=2048 ? e.href : null")
                if not address:
                    return self.reject("Choose a public link up to 2,048 characters.")
                size = download(address, command["file_path"], self.sites)
                return {
                    "message": "Download saved without opening it.",
                    "download_bytes": size,
                    "postcondition": "verified",
                }
            elif op == "fill":
                if info["tag"] not in {"input", "textarea"} or info["type"] not in {
                    "text",
                    "search",
                    "textarea",
                    "email",
                    "tel",
                    "url",
                    "number",
                }:
                    return self.reject("Choose an ordinary text field.")
                target.fill(command.get("text", ""), timeout=8000)
                verified = target.input_value() == command.get("text", "")
            elif op == "select":
                target.select_option(label=command.get("text", ""), timeout=8000)
                verified = target.evaluate("e => e.selectedOptions[0]?.label") == command.get(
                    "text", ""
                )
            elif op == "check":
                target.set_checked(command.get("checked", True), timeout=8000)
                verified = target.is_checked() == command.get("checked", True)
            elif op == "key":
                key = command.get("key")
                if key not in {
                    "Tab",
                    "Shift+Tab",
                    "ArrowDown",
                    "ArrowUp",
                    "ArrowLeft",
                    "ArrowRight",
                    "Escape",
                }:
                    return self.reject("Choose an ordinary navigation key.")
                target.press(key, timeout=8000)
                verified = None
            else:
                if info["type"] in {"file", "password"}:
                    return self.reject("Choose Upload or Take over.")
                if info["tag"] not in {"button", "a", "summary", "input"} and info["role"] not in {
                    "button",
                    "link",
                    "checkbox",
                }:
                    return self.reject("Choose an exposed button or link.")
                target.click(timeout=8000, no_wait_after=True)
                verified = None
            # Pump already-dispatched local JS effects, with a fixed bound.
            self.page.wait_for_timeout(150)
            result = self.observe()
            result["postcondition"] = (
                "verified"
                if verified
                else "Inspect the fresh observation; business outcome is not inferred."
            )
            return result
        elif op == "scroll":
            self.invalidate()
            self.page.mouse.wheel(0, -550 if command.get("direction") == "up" else 550)
            self.page.wait_for_timeout(100)
        elif op == "screenshot":
            picture = self.page.screenshot(
                type="jpeg",
                quality=45,
                full_page=False,
                mask=[self.page.locator("input,textarea,[contenteditable],iframe")],
            )
            if len(picture) > 150000:
                return self.reject("Capture too large.")
            return {"screenshot": "data:image/jpeg;base64," + base64.b64encode(picture).decode()}
        elif op not in {"observe", "tab", "new_tab", "close_tab", "navigate"}:
            return self.reject("Unknown operation.")
        return self.observe(command if op == "observe" else None)

    def invalidate(self):
        for handle, _signature in self.refs.values():
            handle.dispose()
        self.refs.clear()
        self.observation = None

    def target(self, command):
        if (
            command.get("observation_id") != self.observation
            or not self.observation
            or self.page.url != self.observed_url
            or time.monotonic() - self.observed_at > 60
            or self.page.evaluate("document.visibilityState") != "visible"
        ):
            return None
        if self.fingerprint() != self.state_signature:
            return None
        item = self.refs.get(command.get("ref"))
        if not item:
            return None
        handle, signature = item
        try:
            info = handle.evaluate(DESCRIBE)
            current = hashlib.sha256(info["signature"].encode()).hexdigest()
            if not handle.is_visible() or not handle.is_enabled() or current != signature:
                return None
        except Exception:
            return None
        return handle

    def _new_page(self, page):
        if len(self.tabs) >= 5:
            page.close()
            return
        tab_id = uuid4().hex
        self.tabs[tab_id] = page
        page.set_default_timeout(8000)
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.on("download", lambda download: download.cancel())
        page.on("close", lambda: self.tabs.pop(tab_id, None))

    def fingerprint(self):
        state = self.page.locator("body").evaluate(
            "root => {const s=("
            + COLLECT
            + ")(root); return [s.text,s.controls.map(e => ("
            + DESCRIBE
            + ")(e).signature)];}"
        )
        return hashlib.sha256(json.dumps(state).encode()).hexdigest()

    def visible_page(self):
        # Edge can expose multiple visible windows. Preserve the explicitly
        # selected page when visible; visibility alone does not mean focus.
        if (
            self.page is not None
            and not self.page.is_closed()
            and self.page.evaluate("document.visibilityState") == "visible"
        ):
            return self.page
        for page in self.tabs.values():
            if not page.is_closed() and page.evaluate("document.visibilityState") == "visible":
                return page
        return None

    def observe(self, command=None):
        if not command or not command.get("ref"):
            visible = self.visible_page()
            if visible is not None and visible != self.page:
                self.page = visible
        self._check_page_origin()
        scoped = self.target(command) if command and command.get("ref") else None
        if command and command.get("ref") and scoped is None:
            return self.reject("That observation is stale. Read the page again.")
        root = (
            scoped.evaluate_handle("e => e").as_element()
            if scoped
            else self.page.locator("body").element_handle(timeout=8000)
        )
        self.invalidate()
        collected = root.evaluate_handle(COLLECT)
        text = collected.get_property("text").json_value()
        controls = collected.get_property("controls")
        elements = []
        for handle in controls.get_properties().values():
            info = handle.evaluate(DESCRIBE)
            ref = uuid4().hex
            self.refs[ref] = (
                handle.as_element(),
                hashlib.sha256(info.pop("signature").encode()).hexdigest(),
            )
            info.pop("secret")
            if info["tag"] == "select":
                info["options"] = handle.evaluate(
                    "e => Array.from({length:Math.min(e.options.length,30)},(_,i)=>e.options[i].label.slice(0,120))"
                )
            elements.append({"ref": ref, **info})
        truncated = collected.get_property("truncated").json_value()
        visited = collected.get_property("visited").json_value()
        controls.dispose()
        collected.dispose()
        root.dispose()
        self.observation = uuid4().hex
        self.observed_url = self.page.url
        self.observed_at = time.monotonic()
        self.state_signature = self.fingerprint()
        return {
            "url": self._site(self.page.url),
            "observation_id": self.observation,
            "text": text,
            "elements": elements,
            "truncated": truncated,
            "visited_nodes": visited,
            "tabs": [
                {"tab_id": i, "origin": self._site(p.url), "active": p == self.page}
                for i, p in self.tabs.items()
            ],
            "untrusted_page_content": True,
        }

    @staticmethod
    def _site(url):
        parsed = urlsplit(url)
        return f"{parsed.scheme}://{parsed.netloc.lower()}"

    def _check_page_origin(self):
        if self.page.is_closed() or self._site(self.page.url) not in self.sites:
            raise ValueError("Visible page left approved destinations")

    @staticmethod
    def reject(message):
        return {"rejected": True, "blocked": True, "message": message}


def main():
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
                        # Event dispatch only: no idle observations or captures.
                        time.sleep(
                            0.05
                        ) if worker.page.is_closed() else worker.page.wait_for_timeout(50)
                    continue
                if line is None:
                    break
                try:
                    result = worker.execute(json.loads(line))
                except Exception:
                    result = {"error": "Browser action failed. No automatic retry was performed."}
                print(json.dumps(result), flush=True)
        finally:
            if worker.browser is not None:
                worker.browser.close()


if __name__ == "__main__":
    main()
