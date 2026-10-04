import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .core import CORE, CoreStatus, command, protocols, validate_service, validate_collection, ID
from .store import atomic_json
from .diagnostics import Diagnostics, explain_nat


class Worker:
    def __init__(self, service, protocol, state_dir, core=CORE):
        self.service = dict(service)
        self.protocol = protocol
        self.directory = state_dir / "workers" / (service["id"] + "-" + protocol)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.core = core
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.proc = None
        self.state = "starting"
        self.restarts = 0
        self.exit_code = None
        self.status = CoreStatus()
        self.last_error = None
        self.logger = logging.Logger(str(self.directory))
        handler = RotatingFileHandler(self.directory / "core.log", maxBytes=1_048_576, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        self.thread = threading.Thread(target=self.run, daemon=True, name=service["id"] + "-" + protocol)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    self.state = "starting"
                    self.status = CoreStatus()
                    (self.directory / "mapping.json").unlink(missing_ok=True)
                try:
                    env = dict(os.environ, NATTER_GUI_WORKER_DIR=str(self.directory), PYTHONDONTWRITEBYTECODE="1")
                    proc = subprocess.Popen(command(self.service, self.protocol, sys.executable, self.core), env=env,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace", bufsize=1)
                    with self.lock:
                        self.proc = proc
                        self.state = "running"
                    # stop() can arrive while Popen is being created.
                    if self.stop_event.is_set():
                        proc.terminate()
                    for line in proc.stdout:
                        self.logger.info(line.rstrip())
                        with self.lock:
                            self.status.ingest(line)
                    proc.stdout.close()
                    code = proc.wait()
                    with self.lock:
                        self.exit_code = code
                        self.proc = None
                except (OSError, ValueError) as error:
                    with self.lock:
                        self.last_error = str(error)
                    self.logger.error("GUI supervisor: %s", error)
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.state = "restarting"
                    self.restarts += 1
                self.stop_event.wait(15)
        finally:
            with self.lock:
                self.state = "stopped"
                proc = self.proc
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait()
            for handler in self.logger.handlers:
                handler.close()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass
        self.thread.join(timeout=7)
        if self.thread.is_alive():
            raise RuntimeError("Natter 进程未能停止")

    def snapshot(self):
        with self.lock:
            result = {"protocol": self.protocol, "runtime": self.state, "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
                      "restarts": self.restarts, "exit_code": self.exit_code, "last_error": self.last_error, **self.status.snapshot()}
            try:
                result["mapping"] = json.loads((self.directory / "mapping.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result["mapping"] = None
            result["mapping_active"] = result["runtime"] == "running" and result["mapping"] is not None
            return result


class Manager:
    def __init__(self, store, core=CORE):
        self.store = store
        self.core = core
        self.lock = threading.RLock()
        self.workers = {}
        self.nat = {"state": "idle", "raw": "", "results": []}
        self.nat_thread = None
        self.nat_proc = None
        self.closed = False
        self.diagnostics = Diagnostics()

    def boot(self):
        with self.lock:
            for service in self.store.services():
                if service["enabled"]:
                    self._start(service)

    def _start(self, service):
        for protocol in protocols(service):
            key = (service["id"], protocol)
            worker = Worker(service, protocol, self.store.directory, self.core)
            self.workers[key] = worker
            worker.start()

    def _stop(self, sid):
        for key in [k for k in self.workers if k[0] == sid]:
            self.workers[key].stop()
            del self.workers[key]

    def get(self, sid):
        for service in self.store.services():
            if service["id"] == sid:
                return service
        raise KeyError("服务不存在")

    def upsert(self, data, sid=None):
        service = validate_service(data)
        with self.lock:
            services = self.store.services()
            if sid:
                self.get(sid)
            service["id"] = sid or secrets.token_hex(6)
            services = [s for s in services if s["id"] != service["id"]] + [service]
            validate_collection(services)
            self._stop(service["id"])
            self.store.replace_services(services)
            # An edit invalidates the previous manual verification.
            verification = self.store.directory / (service["id"] + "-verification.json")
            verification.unlink(missing_ok=True)
            if service["enabled"]:
                self._start(service)
            return service

    def action(self, sid, action):
        with self.lock:
            service = self.get(sid)
            if action not in ("start", "stop", "restart"):
                raise ValueError("操作必须为 start、stop 或 restart")
            service["enabled"] = action != "stop"
            return self.upsert(service, sid)

    def delete(self, sid):
        with self.lock:
            self.get(sid)
            self._stop(sid)
            self.store.replace_services([s for s in self.store.services() if s["id"] != sid])
            for protocol in ("tcp", "udp"):
                shutil.rmtree(self.store.directory / "workers" / (sid + "-" + protocol), ignore_errors=True)
            (self.store.directory / (sid + "-verification.json")).unlink(missing_ok=True)

    def snapshot(self):
        with self.lock:
            services = self.store.services()
            for service in services:
                service["workers"] = [self.workers[(service["id"], p)].snapshot() if (service["id"], p) in self.workers
                                      else {"protocol": p, "runtime": "stopped", "mapping": None, "mapping_active": False,
                                            "wan": "NOT_CHECKED", "lan": {}, "core_warning": None} for p in protocols(service)]
                try:
                    service["manual_verification"] = json.loads((self.store.directory / (service["id"] + "-verification.json")).read_text())
                    verification = service["manual_verification"]
                    fields = ("protocol", "public_ip", "public_port", "target_ip", "target_port")
                    verification["stale"] = any(not w["mapping_active"] or
                                                any((verification["mappings"].get(w["protocol"]) or {}).get(f) != (w["mapping"] or {}).get(f) for f in fields)
                                                for w in service["workers"])
                except (OSError, ValueError):
                    service["manual_verification"] = None
            return services

    def verify(self, sid, data):
        with self.lock:
            self.get(sid)
            if not isinstance(data.get("success"), bool):
                raise ValueError("success 必须是布尔值")
            mappings = {p: self.workers[(sid, p)].snapshot()["mapping"] for p in protocols(self.get(sid)) if (sid, p) in self.workers}
            if not mappings or not any(mappings.values()):
                raise ValueError("暂无映射，不能记录外网验证")
            value = {"success": data["success"], "note": str(data.get("note", ""))[:500], "mappings": mappings,
                     "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
            atomic_json(self.store.directory / (sid + "-verification.json"), value)
            return value

    def logs(self, sid):
        self.get(sid)
        result = {}
        for p in protocols(self.get(sid)):
            path = self.store.directory / "workers" / (sid + "-" + p) / "core.log"
            result[p] = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]) if path.exists() else ""
        return result

    def import_services(self, data):
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("services"), list):
            raise ValueError("需要 schema_version: 1 的服务配置")
        # Imports never start processes without an explicit enable action.
        incoming = [dict(validate_service(s), id=secrets.token_hex(6), enabled=False) for s in data["services"]]
        with self.lock:
            services = self.store.services() + incoming
            validate_collection(services)
            self.store.replace_services(services)
        return incoming

    def check_nat(self):
        with self.lock:
            if self.nat_thread and self.nat_thread.is_alive():
                raise ValueError("NAT 检测正在运行")
            self.nat = {"state": "running", "raw": "", "results": [], "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
            self.nat_thread = threading.Thread(target=self._nat_job, daemon=True)
            self.nat_thread.start()

    def _nat_job(self):
        import re
        proc = None
        try:
            with self.lock:
                if self.closed:
                    return
                proc = subprocess.Popen([sys.executable, "-u", str(self.core / "natter-check.py")],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.nat_proc = proc
            output, _ = proc.communicate(timeout=150)
            raw = output.decode("utf-8", errors="replace")
            results = [explain_nat({"protocol": m[0].lower(), "status": m[1], "detail": m[2]}) for m in
                       re.findall(r"Checking (TCP|UDP) NAT\.\.\.\s*\[\s*(OK|FAIL|NA)\s*\]\s*\.\.\.\s*([^\n]*)", raw)]
            value = {"state": "finished" if proc.returncode == 0 else "error", "raw": raw[-16384:], "results": results}
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
            value = {"state": "error", "raw": output.decode(errors="replace")[-16384:] + "\nGUI: 检测超过 150 秒，已停止。", "results": []}
        except OSError as error:
            value = {"state": "error", "raw": str(error), "results": []}
        with self.lock:
            value["started_at"] = self.nat.get("started_at")
            value["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.nat = value
            self.nat_proc = None

    def check_diagnostics(self, sid=None):
        with self.lock:
            if self.closed:
                raise ValueError("面板正在关闭")
            if sid is not None:
                self.get(sid)
            self.diagnostics.start([s for s in self.snapshot() if sid is None or s["id"] == sid], all_services=sid is None)

    def close(self):
        with self.lock:
            self.closed = True
            for sid in {k[0] for k in self.workers}:
                self._stop(sid)
            proc = self.nat_proc
            if proc and proc.poll() is None:
                proc.terminate()
        if self.nat_thread:
            self.nat_thread.join(timeout=5)
        self.diagnostics.close()
