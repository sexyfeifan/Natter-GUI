import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading

from .core import validate_service, validate_collection

DEFAULT_PASSWORD = "admin"


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.path = self.directory / "config.json"
        if not self.path.exists():
            password = DEFAULT_PASSWORD
            data = {"schema_version": 1, "services": [], "auth": self.password_hash(password)}
            atomic_json(self.path, data)
            bootstrap = self.directory / "bootstrap-password.txt"
            bootstrap.write_text(password + "\n", encoding="utf-8")
            os.chmod(bootstrap, 0o600)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if self.data.get("schema_version") != 1:
            raise ValueError("不支持的配置版本")
        self.data["services"] = [dict(validate_service(s), id=s["id"]) for s in self.data["services"]]
        validate_collection(self.data["services"])

    @staticmethod
    def password_hash(password):
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
        return {"salt": salt, "digest": digest, "iterations": 600_000}

    def authenticate(self, password):
        if not isinstance(password, str):
            return False
        with self.lock:
            auth = dict(self.data["auth"])
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(auth["salt"]), auth["iterations"]).hex()
        return hmac.compare_digest(digest, auth["digest"])

    def change_password(self, password):
        if not isinstance(password, str):
            raise ValueError("新密码必须是字符串")
        with self.lock:
            self.data["auth"] = self.password_hash(password)
            self.save()
            (self.directory / "bootstrap-password.txt").unlink(missing_ok=True)

    def save(self):
        atomic_json(self.path, self.data)

    def services(self):
        with self.lock:
            return [dict(s) for s in self.data["services"]]

    def replace_services(self, services):
        validate_collection(services)
        with self.lock:
            self.data["services"] = services
            self.save()
