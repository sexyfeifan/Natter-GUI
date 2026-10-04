"""Small authenticated HTTP API and same-origin browser panel."""
from collections import defaultdict
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import platform
import secrets
import threading
import time
import urllib.parse
import urllib.request

from . import __version__
from .core import ROOT, ID


class App:
    def __init__(self, store, manager, secure_cookie=False):
        self.store = store
        self.manager = manager
        self.secure_cookie = secure_cookie
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = defaultdict(list)
        self.update = {"state": "idle"}

    def login(self, password, address):
        now = time.monotonic()
        with self.lock:
            self.failures = defaultdict(list, {k: [t for t in v if now - t < 60] for k, v in self.failures.items() if any(now - t < 60 for t in v)})
            if len(self.failures[address]) >= 5 or len(self.failures) > 4096:
                raise ValueError("登录失败次数过多，请稍后重试")
            if not self.store.authenticate(password):
                self.failures[address].append(now)
                return None
            self.failures.pop(address, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            if len(self.sessions) >= 128:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            session = {"csrf": secrets.token_urlsafe(24), "expires": now + 8 * 3600}
            self.sessions[token] = session
            return token, session

    def session(self, cookie):
        try:
            jar = http.cookies.SimpleCookie(cookie or "")
            token = jar["natter_gui"].value
        except (KeyError, http.cookies.CookieError):
            return None, None
        with self.lock:
            session = self.sessions.get(token)
            if not session or session["expires"] <= time.monotonic():
                self.sessions.pop(token, None)
                return None, None
            return token, dict(session)

    def check_update(self):
        with self.lock:
            if self.update.get("state") == "running":
                raise ValueError("更新检查正在运行")
            self.update = {"state": "running"}
        def job():
            try:
                request = urllib.request.Request("https://api.github.com/repos/MikeWang000000/Natter/releases/latest",
                                                 headers={"User-Agent": "Natter-GUI/" + __version__, "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    value = json.load(response)
                result = {"state": "finished", "tag": value["tag_name"], "url": value["html_url"]}
            except Exception as error:
                result = {"state": "error", "error": str(error)}
            with self.lock:
                self.update = result
        threading.Thread(target=job, daemon=True).start()

    def state(self):
        manifest = json.loads((ROOT / "upstream.lock.json").read_text(encoding="utf-8"))
        with self.lock:
            update = dict(self.update)
        with self.manager.lock:
            nat = dict(self.manager.nat)
        services = self.manager.snapshot()
        return {"gui_version": __version__, "upstream": manifest, "platform": platform.system() + " / " + platform.machine(),
                "services": services, "nat": nat, "update": update, "diagnostics": self.manager.diagnostics.snapshot(services)}


def make_server(app, host, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Natter-GUI"

        def log_message(self, fmt, *args):
            logging.info("%s %s", self.client_address[0], (fmt % args).replace("\n", " ").replace("\r", " "))

        def reply(self, status, data, cookie=None, kind="application/json; charset=utf-8"):
            body = json.dumps(data, ensure_ascii=False).encode() if kind.startswith("application/json") else data
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请求需要 application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("无效的 Content-Length") from None
            if not 0 < length <= 131072:
                raise ValueError("请求大小必须为 1–131072 字节")
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise ValueError("无效的 JSON") from None
            if not isinstance(data, dict):
                raise ValueError("请求需要 JSON 对象")
            return data

        def dispatch(self):
            path = urllib.parse.urlsplit(self.path).path
            if self.command == "GET" and path in ("/", "/app.js", "/style.css"):
                name, kind = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                              "/style.css": ("style.css", "text/css; charset=utf-8")}[path]
                return self.reply(200, (ROOT / "natter_gui" / "web" / name).read_bytes(), kind=kind)
            if self.command == "GET" and path == "/healthz":
                return self.reply(200, {"ok": True})
            if self.command != "GET":
                origin = self.headers.get("Origin")
                if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
                    return self.reply(403, {"error": "请求来源不匹配"})
            if self.command == "POST" and path == "/api/login":
                result = app.login(self.body().get("password"), self.client_address[0])
                if not result:
                    return self.reply(401, {"error": "密码错误"})
                token, session = result
                secure = "; Secure" if app.secure_cookie else ""
                return self.reply(200, {"csrf": session["csrf"]}, cookie=f"natter_gui={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800{secure}")
            token, session = app.session(self.headers.get("Cookie"))
            if self.command == "GET" and path == "/api/session":
                return self.reply(200, {"authenticated": bool(session), "csrf": session["csrf"] if session else None})
            if not session:
                return self.reply(401, {"error": "请先登录"})
            if self.command != "GET" and not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
                return self.reply(403, {"error": "CSRF 校验失败"})
            if self.command == "GET" and path == "/api/state":
                return self.reply(200, app.state())
            if self.command == "GET" and path == "/api/export":
                return self.reply(200, {"schema_version": 1, "services": app.store.services()})
            if self.command == "POST" and path == "/api/logout":
                with app.lock:
                    app.sessions.pop(token, None)
                return self.reply(200, {"ok": True}, cookie="natter_gui=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            if self.command == "POST" and path == "/api/password":
                data = self.body()
                if not app.store.authenticate(data.get("current_password")):
                    return self.reply(401, {"error": "当前密码错误"})
                app.store.change_password(data.get("new_password"))
                with app.lock:
                    app.sessions.clear()
                return self.reply(200, {"ok": True}, cookie="natter_gui=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            if self.command == "POST" and path == "/api/import":
                return self.reply(201, app.manager.import_services(self.body()))
            if self.command == "POST" and path == "/api/services":
                return self.reply(201, app.manager.upsert(self.body()))
            if self.command == "POST" and path == "/api/nat-check":
                app.manager.check_nat()
                return self.reply(202, {"ok": True})
            if self.command == "POST" and path == "/api/diagnostics":
                data = self.body()
                if set(data) - {"service_id"}:
                    raise ValueError("诊断只接受已配置的 service_id，不接受任意目标或命令")
                sid = data.get("service_id")
                if sid is not None and (not isinstance(sid, str) or not ID.fullmatch(sid)):
                    raise ValueError("服务 ID 无效")
                app.manager.check_diagnostics(sid)
                return self.reply(202, {"ok": True})
            if self.command == "POST" and path == "/api/upstream-check":
                app.check_update()
                return self.reply(202, {"ok": True})
            parts = path.split("/")
            if len(parts) in (4, 5) and parts[1:3] == ["api", "services"] and ID.fullmatch(parts[3]):
                sid = parts[3]
                if len(parts) == 4 and self.command == "PUT":
                    return self.reply(200, app.manager.upsert(self.body(), sid))
                if len(parts) == 4 and self.command == "DELETE":
                    app.manager.delete(sid)
                    return self.reply(200, {"ok": True})
                if len(parts) == 5 and self.command == "GET" and parts[4] == "logs":
                    return self.reply(200, app.manager.logs(sid))
                if len(parts) == 5 and self.command == "POST" and parts[4] == "action":
                    return self.reply(200, app.manager.action(sid, self.body().get("action")))
                if len(parts) == 5 and self.command == "POST" and parts[4] == "verify":
                    return self.reply(200, app.manager.verify(sid, self.body()))
            return self.reply(404, {"error": "接口不存在"})

        def handle_request(self):
            self.connection.settimeout(15)
            try:
                self.dispatch()
            except KeyError as error:
                self.reply(404, {"error": str(error)})
            except ValueError as error:
                self.reply(400, {"error": str(error)})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                logging.exception("API request failed")
                self.reply(500, {"error": "操作失败，请查看面板后台日志"})

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request
        do_DELETE = handle_request

    return ThreadingHTTPServer((host, port), Handler)
