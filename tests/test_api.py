import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from natter_gui.server import App, make_server
from natter_gui.store import Store
from natter_gui.supervisor import Manager
from tests.test_core import service


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.manager = Manager(self.store)
        self.app = App(self.store, self.manager)
        self.server = make_server(self.app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""
        self.csrf = ""
        self.password = (Path(self.temp.name) / "bootstrap-password.txt").read_text().strip()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.manager.close()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, **extra):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Cookie": self.cookie, "X-CSRF-Token": self.csrf, **extra}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        raw = response.read()
        cookie = response.getheader("Set-Cookie")
        result = json.loads(raw) if response.getheader("Content-Type").startswith("application/json") else raw.decode()
        status = response.status
        connection.close()
        return status, result, cookie

    def login(self):
        code, value, cookie = self.request("/api/login", "POST", {"password": self.password})
        self.assertEqual(code, 200)
        self.cookie = cookie.split(";")[0]
        self.csrf = value["csrf"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_auth_csrf_crud_export_and_password_rotation(self):
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertEqual(self.request("/api/export")[0], 401)
        self.login()
        csrf = self.csrf
        self.csrf = ""
        self.assertEqual(self.request("/api/services", "POST", service())[0], 403)
        self.csrf = csrf
        code, s, _ = self.request("/api/services", "POST", service())
        self.assertEqual(code, 201)
        self.assertEqual(self.request("/api/state")[1]["services"][0]["name"], "Emby")
        export = self.request("/api/export")[1]
        self.assertNotIn("auth", export)
        self.assertNotIn(self.password, json.dumps(export))
        self.assertEqual(self.request("/api/services/" + s["id"], "DELETE", {})[0], 200)
        self.assertEqual(self.request("/api/password", "POST", {"current_password": self.password, "new_password": "new-test-password-123"})[0], 200)
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertFalse((Path(self.temp.name) / "bootstrap-password.txt").exists())
        self.assertTrue(self.store.authenticate("new-test-password-123"))

    def test_cross_origin_login_rejected_and_static_paths_are_allowlisted(self):
        self.assertEqual(self.request("/api/login", "POST", {"password": self.password}, Origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/../upstream.lock.json")[0], 401)
        self.assertEqual(self.request("/vendor/natter/natter.py")[0], 401)
