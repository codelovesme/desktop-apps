#!/usr/bin/python3
"""One codelovesme application in a desktop window of its own.

The window shows the same web application the browser shows, at
https://apps.codeloves.me/<slug>, in a WebKit view that keeps to it: the
application's own addresses and signing in, nothing else. A link elsewhere
opens in the system browser. There is no bridge between the page and the
computer — the application talks to its server exactly as it does in a
browser, so nothing here needs Mike's device gateway.

Signing in happens in the window. A sign-in provider that will not run
inside an embedded view (Google) is handed to the system browser instead:
the person signs in there and approves this window, and the session comes
back through a one-time loopback callback, as Mike's does.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

PUBLIC_ORIGIN = "https://apps.codeloves.me"

# Sign-in providers whose pages refuse to run in an embedded browser.
BROWSER_SIGN_IN_HOSTS = ("accounts.google.com",)


def read_config(path: Path) -> dict:
    """`key=value` lines: slug, name."""
    config = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    if not config.get("slug") or not config.get("name"):
        raise SystemExit(f"{path}: needs slug= and name=")
    return config


def allowed_origin(origin: str) -> bool:
    """The public host, or a loopback test host — never anything else."""
    if origin == PUBLIC_ORIGIN:
        return True
    parts = urlsplit(origin)
    return (
        parts.scheme == "http"
        and parts.hostname in ("127.0.0.1", "localhost")
        and parts.port is not None
        and parts.path in ("", "/")
    )


def trusted_origin(target: str, origin: str) -> bool:
    """Only the exact configured origin may live in the window."""
    try:
        candidate, allowed = urlsplit(target), urlsplit(origin)
        return (
            candidate.scheme == allowed.scheme
            and candidate.hostname == allowed.hostname
            and candidate.port == allowed.port
            and candidate.username is None
            and candidate.password is None
        )
    except ValueError:
        return False


def allowed_navigation(target: str, origin: str, slug: str) -> bool:
    """The application's own addresses, its desktop entry, and signing in."""
    if not trusted_origin(target, origin):
        return False
    path = urlsplit(target).path.rstrip("/") or "/"
    return (
        path == "/desktop/" + slug
        or path == "/" + slug
        or path.startswith("/" + slug + "/")
        or path == "/id"
        or path.startswith("/id/")
    )


def browser_sign_in(target: str) -> bool:
    try:
        parts = urlsplit(target)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname in BROWSER_SIGN_IN_HOSTS


def load_approvals(path: Path) -> dict:
    """What the person said yes to, kept between windows: {kind: True}."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    return {kind: True for kind, yes in saved.items() if isinstance(kind, str) and yes is True}


def save_approval(path: Path, kind: str) -> None:
    approvals = load_approvals(path)
    approvals[kind] = True
    try:
        path.write_text(json.dumps(approvals), encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def download_path(folder: Path, suggested: str) -> Path:
    """A free name in `folder`: "a.pdf", then "a (1).pdf", …"""
    name = Path(suggested or "download").name or "download"
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = folder / name
    n = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


class AppWindow:
    def __init__(self, config: dict, origin: str):
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import GLib, Gtk, WebKit2

        self.GLib, self.Gtk, self.WebKit2 = GLib, Gtk, WebKit2
        self.slug = config["slug"]
        self.name = config["name"]
        self.origin = origin.rstrip("/")
        self.connect_state = ""

        self.window = Gtk.Window(title=self.name)
        self.window.set_default_size(1100, 780)
        self.window.set_size_request(380, 480)
        self.window.connect("destroy", self.close)
        icon = config.get("icon_path", "")
        if icon and Path(icon).is_file():
            try:
                self.window.set_icon_from_file(icon)
            except GLib.Error:
                pass
        self.header = Gtk.HeaderBar(title=self.name)
        self.header.set_show_close_button(True)
        self.window.set_titlebar(self.header)

        # Each application keeps its own session and storage, apart from
        # every other window and from the browser.
        data = Path.home() / ".local/share/codelovesme" / self.slug / "webkit"
        cache = Path.home() / ".cache/codelovesme" / self.slug / "webkit"
        data.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        # A yes is kept, so the camera and microphone are asked for once,
        # not every time the window opens (owner, 2026-10-02). A no lasts
        # only this window, so a slip of the finger is not for ever.
        self.approvals_path = data.parent / "permissions.json"
        self.allowed = load_approvals(self.approvals_path)
        manager = WebKit2.WebsiteDataManager(base_data_directory=str(data), base_cache_directory=str(cache))
        context = WebKit2.WebContext.new_with_website_data_manager(manager)
        context.connect("download-started", self.download_started)
        self.web = WebKit2.WebView.new_with_context(context)
        self.web.connect("decide-policy", self.decide_policy)
        self.web.connect("load-failed", self.load_failed)
        self.web.connect("permission-request", self.permission_request)
        self.window.add(self.web)

        self.start_callback()
        self.web.load_uri(self.origin + "/desktop/" + self.slug)
        self.window.show_all()

    # -- where the window may go ---------------------------------------------

    def decide_policy(self, _view, decision, decision_type) -> bool:
        WebKit2 = self.WebKit2
        if decision_type == WebKit2.PolicyDecisionType.RESPONSE:
            if not decision.is_mime_type_supported():
                decision.download()
                return True
            return False
        if decision_type not in (
            WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            return False
        target = decision.get_navigation_action().get_request().get_uri()
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION and allowed_navigation(target, self.origin, self.slug):
            return False
        decision.ignore()
        if browser_sign_in(target):
            self.connect_browser()
        elif target.startswith(("https://", "http://")) and not trusted_origin(target, self.origin):
            webbrowser.open(target)
        return True

    def load_failed(self, _view, _event, _uri, _error) -> bool:
        self.header.set_subtitle("Offline — check the connection and reopen the window")
        return False

    # -- camera, microphone, notifications: asked once, a yes kept ----------

    def permission_request(self, _view, request) -> bool:
        Gtk = self.Gtk
        kind = request.__class__.__name__
        if not trusted_origin(self.web.get_uri() or "", self.origin):
            request.deny()
            return True
        if kind in self.allowed:
            request.allow() if self.allowed[kind] else request.deny()
            return True
        words = {
            "UserMediaPermissionRequest": "the camera or microphone",
            "NotificationPermissionRequest": "notifications",
        }.get(kind)
        if words is None:
            request.deny()
            return True
        question = Gtk.MessageDialog(
            transient_for=self.window, modal=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=f"Allow {self.name} to use {words}?",
        )
        approved = question.run() == Gtk.ResponseType.YES
        question.destroy()
        self.allowed[kind] = approved
        if approved:
            save_approval(self.approvals_path, kind)
        request.allow() if approved else request.deny()
        return True

    # -- downloads land in ~/Downloads ---------------------------------------

    def download_started(self, _context, download) -> None:
        GLib = self.GLib

        def destination(item, suggested):
            folder = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or Path.home() / "Downloads")
            folder.mkdir(parents=True, exist_ok=True)
            path = download_path(folder, suggested)
            item.set_destination(path.as_uri())
            self.header.set_subtitle(f"Saving {path.name}…")
            return True

        def finished(item):
            uri = item.get_destination() or ""
            self.header.set_subtitle(f"Saved {Path(urlsplit(uri).path).name} in Downloads" if uri else "")
            GLib.timeout_add_seconds(6, lambda: self.header.set_subtitle("") or False)

        def failed(_item, _error):
            self.header.set_subtitle("The download failed")

        download.connect("decide-destination", destination)
        download.connect("finished", finished)
        download.connect("failed", failed)

    # -- signing in through the system browser -------------------------------

    def start_callback(self) -> None:
        owner = self

        class Callback(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/callback" or self.headers.get("Origin") not in (owner.origin, "null"):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if not 0 < length <= 4096:
                    self.send_error(400)
                    return
                try:
                    fields = parse_qs(self.rfile.read(length).decode("utf-8"), strict_parsing=True)
                except (UnicodeDecodeError, ValueError):
                    self.send_error(400)
                    return
                state = fields.get("state", [""])[0]
                token = fields.get("token", [""])[0]
                if not owner.connect_state or not secrets.compare_digest(state, owner.connect_state) or not token:
                    self.send_error(403)
                    return
                owner.connect_state = ""
                owner.GLib.idle_add(owner.accept_token, token)
                page = f"<!doctype html><title>{owner.name} connected</title><p>You are signed in. You can return to the {owner.name} window.</p>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *_args):
                pass

        self.callback = HTTPServer(("127.0.0.1", 0), Callback)
        threading.Thread(target=self.callback.serve_forever, daemon=True).start()

    def connect_browser(self) -> None:
        self.connect_state = secrets.token_urlsafe(32)
        query = urlencode({"port": self.callback.server_port, "state": self.connect_state, "app": self.name})
        webbrowser.open(self.origin + "/desktop/connect?" + query)
        self.header.set_subtitle("Finish signing in in your browser")

    def accept_token(self, token: str) -> bool:
        self.header.set_subtitle("")
        self.web.run_javascript(
            "localStorage.setItem('id:token', " + json.dumps(token) + "); location.replace('/desktop/" + self.slug + "')",
            None, None,
        )
        return False

    def close(self, *_args) -> None:
        self.callback.shutdown()
        self.callback.server_close()
        self.Gtk.main_quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="A codelovesme application in its own window.")
    parser.add_argument("--config", required=True, help="the app.conf of the application")
    arguments = parser.parse_args()
    config_path = Path(arguments.config)
    config = read_config(config_path)
    icon = config.get("icon", "")
    if icon:
        config["icon_path"] = str(config_path.parent / icon)
    origin = os.environ.get("CODELOVESME_HOST_URL", PUBLIC_ORIGIN).rstrip("/")
    if not allowed_origin(origin):
        raise SystemExit(f"{config['name']} connects only to {PUBLIC_ORIGIN} or a loopback test host")
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk  # noqa: F401
    except (ImportError, ValueError) as error:
        raise SystemExit(
            f"{config['name']} needs GTK and WebKitGTK: install python3-gi and gir1.2-webkit2-4.1 ({error})"
        )
    AppWindow(config, origin)
    from gi.repository import Gtk

    Gtk.main()


if __name__ == "__main__":
    main()
