from pathlib import Path
import tempfile
import unittest

from natter_gui.core import validate_service
from natter_gui.store import Store
from tests.test_core import service


class PasswordTests(unittest.TestCase):
    def test_fresh_install_defaults_to_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            self.assertTrue(store.authenticate("admin"))
            self.assertEqual((Path(directory) / "bootstrap-password.txt").read_text(), "admin\n")

    def test_unrestricted_passwords_preserve_services_and_survive_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            services = [dict(validate_service(service()), id="a" * 12)]
            store.replace_services(services)
            for password in ("a", "密码 " * 1024, ""):
                store.change_password(password)
                reopened = Store(directory)
                self.assertTrue(reopened.authenticate(password))
                self.assertFalse(reopened.authenticate("admin"))
                self.assertEqual(reopened.services(), services)
                self.assertFalse((Path(directory) / "bootstrap-password.txt").exists())
            for invalid in (None, 42, {}, False):
                self.assertFalse(store.authenticate(invalid))
                with self.assertRaises(ValueError):
                    store.change_password(invalid)
