"""The window's rules about where it may go, without opening a window."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "window"))
import app_window as w  # noqa: E402

O = "https://apps.codeloves.me"


class Navigation(unittest.TestCase):
    def test_the_app_its_desktop_entry_and_signing_in(self):
        for path in ("/desktop/todo", "/todo", "/todo/", "/todo/tags", "/id", "/id/account", "/id/oauth/google"):
            self.assertTrue(w.allowed_navigation(O + path, O, "todo"), path)

    def test_not_other_apps_the_hub_or_lookalikes(self):
        for path in ("/", "/home", "/desktop/home", "/todos", "/todo-api", "/identity", "/desktop/connect"):
            self.assertFalse(w.allowed_navigation(O + path, O, "todo"), path)

    def test_not_other_origins(self):
        for url in ("http://apps.codeloves.me/todo", "https://apps.codeloves.me.evil.com/todo",
                    "https://user@apps.codeloves.me/todo", "https://apps.codeloves.me:8443/todo"):
            self.assertFalse(w.allowed_navigation(url, O, "todo"), url)

    def test_browser_sign_in_is_google_over_https_only(self):
        self.assertTrue(w.browser_sign_in("https://accounts.google.com/o/oauth2/v2/auth?x=1"))
        self.assertFalse(w.browser_sign_in("http://accounts.google.com/"))
        self.assertFalse(w.browser_sign_in("https://example.com/"))


class OwnSubdomain(unittest.TestCase):
    """An application with its own subdomain (ticket 093): the window shows
    it there, where "/" is the application alone."""

    T = "https://todo.codeloves.me"

    def test_the_subdomain_is_taken_only_as_its_own(self):
        self.assertEqual(w.app_origin({"slug": "todo", "origin": self.T}, O), self.T)
        self.assertEqual(w.app_origin({"slug": "todo"}, O), O)
        for wrong in ("https://home.codeloves.me", "http://todo.codeloves.me", "https://todo.codeloves.me.evil.com", "https://example.com"):
            self.assertEqual(w.app_origin({"slug": "todo", "origin": wrong}, O), O, wrong)

    def test_a_loopback_test_host_becomes_slug_dot_localhost(self):
        self.assertEqual(w.app_origin({"slug": "todo", "origin": self.T}, "http://127.0.0.1:8923"), "http://todo.localhost:8923")
        self.assertEqual(w.app_origin({"slug": "home"}, "http://127.0.0.1:8923"), "http://127.0.0.1:8923")

    def test_its_root_its_paths_and_signing_in_but_nothing_else(self):
        for path in ("/", "/todo", "/todo/tags", "/id", "/id/account", "/desktop/todo"):
            self.assertTrue(w.allowed_navigation(self.T + path, self.T, "todo", own=True), path)
        for path in ("/home", "/desktop/home", "/desktop/connect"):
            self.assertFalse(w.allowed_navigation(self.T + path, self.T, "todo", own=True), path)
        for url in (O + "/todo", O + "/", "https://home.codeloves.me/"):
            self.assertFalse(w.allowed_navigation(url, self.T, "todo", own=True), url)

    def test_the_hub_root_stays_closed_to_a_window_on_the_hub(self):
        self.assertFalse(w.allowed_navigation(O + "/", O, "todo"))

    def test_todo_names_its_subdomain(self):
        conf = w.read_config(Path(__file__).resolve().parents[1] / "apps" / "todo" / "app.conf")
        self.assertEqual(w.app_origin(conf, O), self.T)


class AppId(unittest.TestCase):
    def test_matches_the_menu_entry_cdlvsm_writes(self):
        # cdlvsm: $XDG_DATA_HOME/applications/codelovesme-<pkg>.desktop
        self.assertEqual(w.app_id_for("todo"), "codelovesme-todo")
        self.assertEqual(w.app_id_for("home"), "codelovesme-home")


class Origins(unittest.TestCase):
    def test_public_or_loopback(self):
        self.assertTrue(w.allowed_origin(O))
        self.assertTrue(w.allowed_origin("http://127.0.0.1:8923"))
        self.assertTrue(w.allowed_origin("http://localhost:8923"))
        for origin in ("http://apps.codeloves.me", "https://example.com", "http://127.0.0.1", "http://10.0.0.2:8923", "http://127.0.0.1:8923/x"):
            self.assertFalse(w.allowed_origin(origin), origin)


class Config(unittest.TestCase):
    def test_every_packaged_app_has_a_slug_name_and_icon(self):
        for conf in (Path(__file__).resolve().parents[1] / "apps").glob("*/app.conf"):
            c = w.read_config(conf)
            self.assertEqual(c["slug"], conf.parent.name)
            self.assertTrue((conf.parent / c["icon"]).is_file(), conf)


class Downloads(unittest.TestCase):
    def test_a_free_name(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self.assertEqual(w.download_path(folder, "a.pdf").name, "a.pdf")
            (folder / "a.pdf").write_text("x")
            (folder / "a (1).pdf").write_text("x")
            self.assertEqual(w.download_path(folder, "a.pdf").name, "a (2).pdf")
            self.assertEqual(w.download_path(folder, "../../etc/passwd").name, "passwd")
            self.assertEqual(w.download_path(folder, "").name, "download")


class Approvals(unittest.TestCase):
    def test_a_yes_is_kept_and_nothing_else_is(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "permissions.json"
            self.assertEqual(w.load_approvals(path), {})
            w.save_approval(path, "UserMediaPermissionRequest")
            self.assertEqual(w.load_approvals(path), {"UserMediaPermissionRequest": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('{"A": false, "B": "yes", "C": true}')
            self.assertEqual(w.load_approvals(path), {"C": True})
            path.write_text("not json")
            self.assertEqual(w.load_approvals(path), {})


if __name__ == "__main__":
    unittest.main()
