#!/usr/bin/python3
"""Open the real window against a running host and sign in through it.

    CODELOVESME_HOST_URL=http://127.0.0.1:8923 E2E_EMAIL=… E2E_PASSWORD=… \
        xvfb-run -a tools/live-smoke.py todo out.png

Signs in with the window's own sign-in form, waits for the application,
checks the window kept to it, and saves a picture of what it shows. Run it
with a throwaway HOME: the window keeps its session under ~/.local/share.
"""

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "window"))
import app_window as w  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

app, picture = sys.argv[1], sys.argv[2]
ready = {"todo": ".fab", "home": ".home"}[app]
conf = Path(__file__).resolve().parents[1] / "apps" / app / "app.conf"
config = w.read_config(conf)
origin = os.environ["CODELOVESME_HOST_URL"]
window = w.AppWindow(config, origin)
steps = {"n": 0, "signed": os.environ.get("E2E_SIGNED_IN") == "1", "result": None}

FILL = """
(() => {
  const field = (label) => [...document.querySelectorAll('#guest-panel label')].find(l => l.textContent.trim() === label)?.nextElementSibling;
  const email = field('Email'), password = field('Password');
  if (!email || !password) return 'no form';
  for (const [el, v] of [[email, %s], [password, %s]]) { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }
  [...document.querySelectorAll('#guest-panel .card button')].find(b => b.textContent.trim() === 'Sign in').click();
  return 'sent';
})()
""" % (json.dumps(os.environ["E2E_EMAIL"]), json.dumps(os.environ["E2E_PASSWORD"]))


def run(script, then):
    def done(view, result):
        try:
            value = view.evaluate_javascript_finish(result)
            then(value.to_string() if value is not None else "")
        except GLib.Error as error:
            then("error: " + error.message)
    window.web.evaluate_javascript(script, -1, None, None, None, done)


def finish(code, message):
    print(message)
    def saved(view, result):
        surface = view.get_snapshot_finish(result)
        surface.write_to_png(picture)
        Gtk.main_quit()
        sys.exit(code)
    window.web.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.NONE, None, saved)


def tick():
    steps["n"] += 1
    if steps["n"] > 60:
        finish(1, "FAIL: timed out at " + (window.web.get_uri() or ""))
        return False
    if not steps["signed"]:
        def filled(answer):
            if answer == "sent":
                steps["signed"] = True
        run(FILL, filled)
        return True

    def checked(answer):
        state = json.loads(answer)
        if state["ready"]:
            path = state["path"]
            hub = state["hub"]
            if path != "/" + config["slug"] and not path.startswith("/" + config["slug"] + "/"):
                finish(1, f"FAIL: landed on {path}")
            elif hub:
                finish(1, "FAIL: the hub is showing")
            else:
                finish(0, f"OK: {config['name']} at {path}, no hub")
    run("JSON.stringify({ready: !!document.querySelector('#guest-panel %s'), path: location.pathname, hub: document.querySelectorAll('#hub-container .app').length})" % ready, checked)
    return True


GLib.timeout_add(500, tick)
Gtk.main()
