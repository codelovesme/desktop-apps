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
import base64
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

PUBLIC_ORIGIN = "https://apps.codeloves.me"

# Sign-in providers whose pages refuse to run in an embedded browser.
BROWSER_SIGN_IN_HOSTS = ("accounts.google.com",)


def app_id_for(slug: str) -> str:
    """The desktop's name for the window: CDLVSM's menu entry is <this>.desktop."""
    return "codelovesme-" + slug


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


def app_origin(config: dict, hub: str) -> str:
    """Where the window shows its application (ticket 093 in my-euglena-apps):
    its own subdomain when app.conf names one — `origin=https://todo.codeloves.me`
    — else the hub. Against a loopback test host the subdomain is
    <slug>.localhost on the same port, which the system sends to this machine.
    Only https://<slug>.codeloves.me is taken from app.conf."""
    own = config.get("origin", "").rstrip("/")
    if not own:
        return hub
    if hub == PUBLIC_ORIGIN:
        return own if own == f"https://{config['slug']}.codeloves.me" else hub
    parts = urlsplit(hub)
    return f"{parts.scheme}://{config['slug']}.localhost:{parts.port}"


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


def allowed_navigation(target: str, origin: str, slug: str, own: bool = False) -> bool:
    """The application's own addresses, its desktop entry, and signing in —
    and, at its own subdomain (`own`), the subdomain's root."""
    if not trusted_origin(target, origin):
        return False
    path = urlsplit(target).path.rstrip("/") or "/"
    return (
        (own and path == "/")
        or path == "/desktop/" + slug
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


# WebKitGTK cannot record audio on a stock Debian 13 desktop: its recorder
# needs GStreamer's fmp4 plugin (gst-plugins-rs, not packaged there) and hands
# back nothing, and its capture can agree on a format that is all silence. So
# the window records itself, with the system's own recorder, and the page's
# MediaRecorder is replaced for audio by one that asks the window. The page
# still asks for the microphone first (getUserMedia), so the permission is the
# same one; a recording with video keeps WebKit's own recorder.
RECORDER_JS = r"""
(() => {
  const port = window.webkit && webkit.messageHandlers && webkit.messageHandlers.codelovesmeAudio;
  if (!port) return;
  const Native = window.MediaRecorder;
  const pending = new Map();
  const asking = new Map();
  let next = 1;
  window.__codelovesmeAudio = {
    done(id, b64) { const r = pending.get(id); if (r) { pending.delete(id); r._finish(b64); } },
    failed(id, message) { const r = pending.get(id); if (r) { pending.delete(id); r._fail(message); } },
    allowed(id, yes) {
      const a = asking.get(id);
      if (!a) return;
      asking.delete(id);
      if (!yes) { a.reject(new DOMException('The microphone was not allowed.', 'NotAllowedError')); return; }
      // The window records, so WebKit's own capture (5 s to open the first
      // time, 1 s after) is not started, nor its audio engine (1.5 s for a
      // silent track): the page is handed an empty stream at once.
      a.resolve(new MediaStream());
    },
  };
  // Sound alone is asked of the window; anything with video stays WebKit's.
  // On the prototype: navigator.mediaDevices may not exist yet this early.
  const Devices = window.MediaDevices && MediaDevices.prototype;
  if (Devices && Devices.getUserMedia) {
    const real = Devices.getUserMedia;
    Devices.getUserMedia = function (constraints) {
      if (!constraints || !constraints.audio || constraints.video) return real.call(this, constraints);
      return new Promise((resolve, reject) => {
        const id = next++;
        asking.set(id, { resolve, reject });
        port.postMessage(JSON.stringify({ op: 'allow', id }));
      });
    };
  }
  class WindowRecorder extends EventTarget {
    constructor(stream) {
      super();
      this.stream = stream; this.mimeType = 'audio/wav'; this.state = 'inactive'; this.id = next++;
      this.ondataavailable = null; this.onstop = null; this.onerror = null; this.onstart = null;
    }
    static isTypeSupported(type) { return /^audio\/wav/.test(String(type)); }
    start() {
      if (this.state !== 'inactive') throw new DOMException('already recording', 'InvalidStateError');
      this.state = 'recording';
      pending.set(this.id, this);
      port.postMessage(JSON.stringify({ op: 'start', id: this.id }));
      this._emit('start', new Event('start'));
    }
    stop() {
      if (this.state === 'inactive') return;
      this.state = 'inactive';
      port.postMessage(JSON.stringify({ op: 'stop', id: this.id }));
    }
    pause() {} resume() {} requestData() {}
    _emit(type, event) {
      const handler = this['on' + type];
      if (typeof handler === 'function') handler.call(this, event);
      this.dispatchEvent(event);
    }
    _finish(b64) {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const event = new Event('dataavailable');
      event.data = new Blob([bytes], { type: 'audio/wav' });
      this._emit('dataavailable', event);
      this._emit('stop', new Event('stop'));
    }
    _fail(message) {
      this.state = 'inactive';
      const event = new Event('error');
      event.error = new DOMException(message);
      this._emit('error', event);
      this._emit('stop', new Event('stop'));
    }
  }
  const Recorder = function (stream, options) {
    if (Native && stream && stream.getVideoTracks && stream.getVideoTracks().length) return new Native(stream, options);
    return new WindowRecorder(stream, options);
  };
  Recorder.isTypeSupported = (type) => WindowRecorder.isTypeSupported(type) || (Native ? Native.isTypeSupported(type) : false);
  window.MediaRecorder = Recorder;
})();
"""


def recorder_command(path: Path) -> list[str] | None:
    """The system's own recorder, 16 kHz mono 16-bit WAV — what speech wants."""
    if shutil.which("pw-record"):
        return ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(path)]
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", str(path)]
    return None


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
        # The hub (apps.codeloves.me) is where signing in through the browser
        # happens — Google knows only that address; the window itself shows
        # the application's own subdomain when it has one, where "/" is the
        # application alone.
        self.hub = origin.rstrip("/")
        self.origin = app_origin(config, self.hub)
        self.own = self.origin != self.hub
        self.start = "/" if self.own else "/desktop/" + self.slug
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
        content = WebKit2.UserContentManager()
        # Every page the window holds is the host's (decide_policy keeps it so),
        # and the window answers only while its page is on the host.
        content.add_script(WebKit2.UserScript(
            RECORDER_JS, WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserScriptInjectionTime.START, None, None,
        ))
        content.register_script_message_handler("codelovesmeAudio")
        content.connect("script-message-received::codelovesmeAudio", self.audio_message)
        self.recording = None
        self.audio_dir = tempfile.TemporaryDirectory(prefix="codelovesme-audio-")
        self.web = WebKit2.WebView(web_context=context, user_content_manager=content)
        self.web.connect("decide-policy", self.decide_policy)
        self.web.connect("load-failed", self.load_failed)
        self.web.connect("permission-request", self.permission_request)
        self.window.add(self.web)

        self.start_callback()
        self.web.load_uri(self.origin + self.start)
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
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION and allowed_navigation(target, self.origin, self.slug, self.own):
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
        kind = request.__class__.__name__
        if not trusted_origin(self.web.get_uri() or "", self.origin):
            request.deny()
            return True
        request.allow() if self.ask(kind) else request.deny()
        return True

    def ask(self, kind: str) -> bool:
        """The person's answer for `kind`: kept, or asked now."""
        Gtk = self.Gtk
        if kind in self.allowed:
            return self.allowed[kind]
        words = {
            "UserMediaPermissionRequest": "the camera or microphone",
            "NotificationPermissionRequest": "notifications",
        }.get(kind)
        if words is None:
            return False
        question = Gtk.MessageDialog(
            transient_for=self.window, modal=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=f"Allow {self.name} to use {words}?",
        )
        approved = question.run() == Gtk.ResponseType.YES
        question.destroy()
        self.allowed[kind] = approved
        if approved:
            save_approval(self.approvals_path, kind)
        return approved

    # -- recording sound for the page ----------------------------------------

    def audio_message(self, _manager, message) -> None:
        try:
            asked = json.loads(message.get_js_value().to_string())
            op, rid = asked["op"], int(asked["id"])
        except (ValueError, KeyError, TypeError):
            return
        if not trusted_origin(self.web.get_uri() or "", self.origin):
            return
        if op == "allow":
            yes = self.ask("UserMediaPermissionRequest")
            self.web.run_javascript(f"window.__codelovesmeAudio && __codelovesmeAudio.allowed({rid}, {'true' if yes else 'false'})", None, None)
        elif op == "start":
            self.start_recording(rid)
        elif op == "stop":
            self.stop_recording(rid)

    def start_recording(self, rid: int) -> None:
        if not self.allowed.get("UserMediaPermissionRequest"):
            self.audio_failed(rid, "the microphone was not allowed")
            return
        self.drop_recording()
        path = Path(self.audio_dir.name) / f"recording-{rid}.wav"
        test_audio = os.environ.get("CODELOVESME_TEST_AUDIO", "")
        if test_audio:
            # Tests stand a file in for the microphone; nothing is started.
            shutil.copyfile(test_audio, path)
            self.recording = (rid, None, path)
            return
        command = recorder_command(path)
        if command is None:
            self.audio_failed(rid, "no recorder on this computer (pw-record or arecord)")
            return
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            self.audio_failed(rid, f"the recorder did not start: {error}")
            return
        self.recording = (rid, process, path)

    def stop_recording(self, rid: int) -> None:
        if self.recording is None or self.recording[0] != rid:
            self.audio_failed(rid, "nothing was recording")
            return
        _, process, path = self.recording
        self.recording = None
        if process is not None:
            process.send_signal(signal.SIGINT)  # lets the recorder finish its file
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        try:
            data = path.read_bytes()
            path.unlink()
        except OSError:
            data = b""
        if len(data) <= 44:  # a WAV header and no sound
            self.audio_failed(rid, "the microphone gave no sound")
            return
        encoded = base64.b64encode(data).decode("ascii")
        self.web.run_javascript(f"window.__codelovesmeAudio && __codelovesmeAudio.done({rid}, '{encoded}')", None, None)

    def audio_failed(self, rid: int, message: str) -> None:
        self.web.run_javascript(f"window.__codelovesmeAudio && __codelovesmeAudio.failed({rid}, {json.dumps(message)})", None, None)

    def drop_recording(self) -> None:
        if self.recording is not None:
            _, process, _ = self.recording
            if process is not None:
                process.kill()
            self.recording = None

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
        webbrowser.open(self.hub + "/desktop/connect?" + query)
        self.header.set_subtitle("Finish signing in in your browser")

    def accept_token(self, token: str) -> bool:
        self.header.set_subtitle("")
        self.web.run_javascript(
            "localStorage.setItem('id:token', " + json.dumps(token) + "); location.replace(" + json.dumps(self.start) + ")",
            None, None,
        )
        return False

    def close(self, *_args) -> None:
        self.drop_recording()
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

        from gi.repository import GLib

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("WebKit2", "4.1")

        # Who the window is, to the desktop — before GTK starts, or it is too
        # late. GNOME ties a window to its menu entry by this name (the Wayland
        # app_id, the X11 WM_CLASS) matching the entry's file name, which CDLVSM
        # writes as codelovesme-<app>.desktop; without it the window was
        # "Unknown" with a generic icon in Alt+Tab and the dock.
        app_id = app_id_for(config["slug"])
        GLib.set_prgname(app_id)
        GLib.set_application_name(config["name"])
        from gi.repository import Gdk

        Gdk.set_program_class(app_id)
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
