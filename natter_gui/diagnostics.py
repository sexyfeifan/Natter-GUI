"""Bounded, read-only diagnostics, separate from upstream NAT/WAN verdicts."""
import copy
import datetime
import errno
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor


NAT_TYPES = {
    -1: ("无法判定", "检测未取得足够结果，不能据此认定端口开放或关闭。"),
    0: ("开放互联网", "检测未发现地址转换；仍需核对防火墙和实际服务可达性。"),
    1: ("全锥形 NAT", "满足原版全锥形条件，适合 Natter 打洞；不代表应用或带宽已验证。"),
    2: ("地址受限型 NAT", "通常只接受内网先联系过的外部 IP 发来的数据。"),
    3: ("端口受限型 NAT", "通常只接受内网先联系过的外部 IP 和端口发来的数据。"),
    4: ("对称型 NAT", "访问不同外部目的地时映射可能变化，直接打洞受到限制。"),
    5: ("对称型 UDP 防火墙", "地址未转换，但 UDP 入站接收受到限制。"),
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def explain_nat(result):
    match = re.search(r"NAT Type:\s*(-?\d+)", result["detail"])
    value = int(match.group(1)) if match else None
    title, explanation = NAT_TYPES.get(value, ("检测异常或未知类型", "查看原版输出；不构造检测结论。"))
    return dict(result, nat_type=value, title=title, explanation=explanation)


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def command_json(args):
    """Fixed executable/arguments; no shell and no writable network operations."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=2, check=False)
        if proc.returncode:
            return None, "无法读取网络信息：" + (proc.stderr.strip()[:200] or "命令执行失败")
        if len(proc.stdout) > 262144:
            raise ValueError("诊断输出过大")
        return json.loads(proc.stdout), None
    except subprocess.TimeoutExpired:
        return None, "网络信息查询超过2秒，未取得结果。"
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return None, str(error)[:250]


def route_to(address, uid=None):
    address = str(ipaddress.IPv4Address(address))
    executable = shutil.which("ip")
    if not executable or platform.system() != "Linux":
        return {"available": False, "reason": "需要 Linux 和 iproute2；未取得路由信息。"}
    args = [executable, "-j", "-4", "route", "get", address]
    if uid is not None:
        args += ["uid", str(uid)]
    values, error = command_json(args)
    if error or not isinstance(values, list) or not values:
        return {"available": False, "reason": error or "没有路由结果"}
    row = values[0]
    return {"available": True, "destination": address, "gateway": row.get("gateway"),
            "interface": row.get("dev"), "source": row.get("prefsrc") or row.get("src"),
            "table": row.get("table", "main")}


def cpu_counters():
    raw = read_text("/proc/stat")
    try:
        return [int(x) for x in raw.splitlines()[0].split()[1:9]] if raw else None
    except (ValueError, IndexError):
        return None


def interface_counters(name):
    values = {}
    for key in ("rx_bytes", "tx_bytes", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"):
        value = read_text(Path("/sys/class/net") / name / "statistics" / key)
        values[key] = int(value) if value and value.isdigit() else None
    return values


def environment(cancel):
    result = {"platform": platform.system(), "architecture": platform.machine(), "python": platform.python_version(),
              "uid": os.getuid() if hasattr(os, "getuid") else None, "cpu_count": os.cpu_count(),
              "cpu_busy_percent": None, "memory": None, "uptime_seconds": None,
              "interfaces": [], "default_routes": [], "rules": [], "notes": []}
    try:
        result["load_average"] = list(os.getloadavg())
    except (OSError, AttributeError):
        result["load_average"] = None
    if platform.system() != "Linux":
        result["notes"].append("Linux 网卡、路由和资源信息在当前平台不可用。")
        result["effective_route"] = route_to("1.1.1.1", result["uid"])
        return result
    uptime = read_text("/proc/uptime")
    if uptime:
        result["uptime_seconds"] = float(uptime.split()[0])
    memory = read_text("/proc/meminfo")
    if memory:
        values = {m[0]: int(m[1]) * 1024 for m in re.findall(r"^(\w+):\s+(\d+) kB", memory, re.M)}
        if "MemTotal" in values and "MemAvailable" in values:
            result["memory"] = {"total_bytes": values["MemTotal"], "available_bytes": values["MemAvailable"]}
    executable = shutil.which("ip")
    if executable:
        addresses, error = command_json([executable, "-j", "-4", "addr", "show"])
        if error:
            result["notes"].append("网卡地址信息不可用：" + error)
        for row in (addresses or [])[:24]:
            name = row.get("ifname", "")
            # Sysfs names are observed locally, never taken from API inputs.
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", name):
                continue
            speed = read_text(Path("/sys/class/net") / name / "speed")
            result["interfaces"].append({"name": name, "state": row.get("operstate", "UNKNOWN"), "mtu": row.get("mtu"),
                "addresses": [f"{a['local']}/{a['prefixlen']}" for a in row.get("addr_info", []) if a.get("family") == "inet"],
                "link_mbps": int(speed) if speed and speed.isdigit() and int(speed) > 0 else None,
                "duplex": read_text(Path("/sys/class/net") / name / "duplex"), **interface_counters(name)})
        routes, error = command_json([executable, "-j", "-4", "route", "show", "table", "main", "default"])
        result["default_routes"] = [{"gateway": r.get("gateway"), "interface": r.get("dev"), "metric": r.get("metric")} for r in (routes or [])[:16]]
        if error:
            result["notes"].append("系统默认路由不可用：" + error)
        rules, error = command_json([executable, "-j", "-4", "rule", "show"])
        result["rules"] = (rules or [])[:40]
        if error:
            result["notes"].append("策略路由信息不可用：" + error)
    else:
        result["notes"].append("未安装 iproute2，网卡地址和策略路由信息不可用。")
    result["effective_route"] = route_to("1.1.1.1", result["uid"])
    result["notes"].append("出口根据当前面板 UID 的路由查询取得，不向 1.1.1.1 发送测试流量；按服务绑定网卡的路由仍需结合配置核对。")
    before_cpu = cpu_counters()
    before_net = {i["name"]: interface_counters(i["name"]) for i in result["interfaces"]}
    start = time.monotonic()
    cancel.wait(1)
    elapsed = max(time.monotonic() - start, 0.001)
    after_cpu = cpu_counters()
    if before_cpu and after_cpu:
        values = [b - a for a, b in zip(before_cpu, after_cpu)]
        total = sum(values)
        if total > 0 and min(values) >= 0:
            result["cpu_busy_percent"] = round(100 * (1 - (values[3] + values[4]) / total), 1)
    for row in result["interfaces"]:
        after = interface_counters(row["name"])
        for direction in ("rx", "tx"):
            first, last = before_net[row["name"]][direction + "_bytes"], after[direction + "_bytes"]
            row[direction + "_mbps"] = round((last - first) * 8 / elapsed / 1e6, 3) if first is not None and last is not None and last >= first else None
    result["sample_seconds"] = round(elapsed, 2)
    return result


def tcp_probe(address, port, deadline, cancel, http_path=None):
    if cancel.is_set() or time.monotonic() >= deadline:
        return {"status": "SKIPPED", "detail": "检测时限已到或任务停止。"}
    address = str(ipaddress.IPv4Address(address))
    if not 1 <= int(port) <= 65535:
        return {"status": "SKIPPED", "detail": "没有可检测的端口。"}
    start = time.monotonic()
    value = {"address": address, "port": int(port), "status": "UNKNOWN"}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(max(0.01, min(1.5, deadline - time.monotonic())))
            sock.connect((address, int(port)))
            value.update(status="OPEN", connect_ms=round((time.monotonic() - start) * 1000, 1))
            if http_path:
                sock.sendall(f"HEAD {http_path} HTTP/1.0\r\nHost: {address}:{port}\r\nConnection: close\r\n\r\n".encode())
                response = sock.recv(1024)
                match = re.match(rb"HTTP/\d(?:\.\d)?\s+(\d{3})", response)
                value["http"] = {"status": "RESPONDED" if match else "UNKNOWN", "code": int(match[1]) if match else None,
                                 "detail": "只检查 HTTP 响应，不跟随重定向，不证明已登录或可播放。"}
    except OSError as error:
        if value["status"] == "OPEN":
            value["http"] = {"status": "UNKNOWN", "code": None, "detail": str(error)[:200]}
        else:
            value.update(status="CLOSED" if error.errno == errno.ECONNREFUSED else "UNKNOWN", detail=str(error)[:200])
    value["duration_ms"] = round((time.monotonic() - start) * 1000, 1)
    return value


def parse_connections(raw):
    results = []
    for line in raw.splitlines():
        header = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)", line)
        if header:
            results.append({"receive_queue": int(header[1]), "send_queue": int(header[2]), "local": header[3], "peer": header[4]})
        if results:
            for name, pattern in {"rtt_ms": r"\brtt:([\d.]+)", "retransmitted_bytes": r"\bbytes_retrans:(\d+)",
                                  "receive_window_limited_percent": r"\brwnd_limited:\d+ms\(([\d.]+)%\)"}.items():
                match = re.search(pattern, line)
                if match:
                    results[-1][name] = float(match[1])
    return results[:16]


def connections(port):
    executable = shutil.which("ss")
    if not executable or platform.system() != "Linux":
        return {"available": False, "reason": "需要 Linux 和 ss；未取得 TCP 连接信息。"}
    try:
        result = subprocess.run([executable, "-tinH", "state", "established", "(", "sport", "=", f":{int(port)}", ")"],
                                capture_output=True, text=True, timeout=2, check=True)
        return {"available": True, "items": parse_connections(result.stdout[:65536])}
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": str(error)[:200]}


def fingerprint(services):
    return json.dumps([{**{k: s.get(k) for k in ("id", "target_ip", "target_port", "bind_ip", "bind_port", "protocol", "enabled", "upnp")},
                        "workers": [{"runtime": w.get("runtime"), "pid": w.get("pid"), "restarts": w.get("restarts"), "mapping_active": w.get("mapping_active"),
                            "mapping": {k: (w.get("mapping") or {}).get(k) for k in ("public_ip", "public_port")},
                            "local_address": w.get("local_address")} for w in s.get("workers", [])]} for s in services], sort_keys=True)


def service_report(service, deadline, cancel):
    value = {"id": service["id"], "name": service["name"], "target": f"{service['target_ip']}:{service['target_port']}",
             "enabled": service["enabled"], "tcp_thread_limit": service.get("tcp_thread_limit", 128), "checked_at": now(), "workers": []}
    has_tcp = any(w["protocol"] == "tcp" for w in service["workers"])
    path = "/web" if service["target_port"] == 32400 else "/" if service["target_port"] == 8096 else None
    value["target_probe"] = tcp_probe(service["target_ip"], service["target_port"], deadline, cancel, path) if has_tcp else {
        "status": "NOT_CHECKED", "detail": "UDP 无通用端口连通判定，需要应用协议或独立外网回包验证。"}
    for worker in service["workers"]:
        row = {"protocol": worker["protocol"], "runtime": worker["runtime"], "pid": worker.get("pid"), "restarts": worker.get("restarts", 0),
               "threads": worker.get("threads"), "thread_limit": worker.get("thread_limit"),
               "forward_error": worker.get("forward_error"), "forward_error_count": worker.get("forward_error_count", 0),
               "mapping_active": worker.get("mapping_active", False), "mapping": worker.get("mapping"),
               "original_wan": worker["wan"], "original_lan": worker["lan"], "original_checked_at": worker.get("last_check_at"),
               "upnp": {"requested": service["upnp"], **worker.get("upnp", {})}, "local_address": worker.get("local_address")}
        local = worker.get("local_address")
        if not local and service["bind_port"]:
            local = {"ip": "127.0.0.1" if service["bind_ip"] == "0.0.0.0" else service["bind_ip"], "port": service["bind_port"]}
        if worker["protocol"] != "tcp":
            row["bind_probe"] = row["public_lan_probe"] = {"status": "NOT_CHECKED", "detail": "未使用 TCP 探测 UDP 端口，UDP 外网可达性需单独验证。"}
        elif worker["runtime"] != "running":
            row["bind_probe"] = row["public_lan_probe"] = {"status": "SKIPPED", "detail": "服务未运行，不检测历史映射或其他进程的端口。"}
        else:
            row["bind_probe"] = tcp_probe(local["ip"], local["port"], deadline, cancel) if local else {"status": "UNKNOWN", "detail": "尚未取得实际绑定地址。"}
            mapping = worker.get("mapping")
            row["public_lan_probe"] = tcp_probe(mapping["public_ip"], mapping["public_port"], deadline, cancel) if worker.get("mapping_active") and mapping else {
                "status": "NOT_CHECKED", "detail": "没有活动公网映射。"}
            if local and time.monotonic() < deadline and not cancel.is_set():
                row["connections"] = connections(local["port"])
        value["workers"].append(row)
    value["manual_verification"] = service.get("manual_verification")
    return value


class Diagnostics:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.closed = False
        self.services = []
        self.signature = None
        self.all_services = True
        self.value = {"state": "idle", "report": None}

    def start(self, services, all_services=True):
        with self.lock:
            if self.closed:
                raise ValueError("面板正在关闭")
            if self.thread and self.thread.is_alive():
                raise ValueError("网络体检正在运行，请等待当前任务结束")
            self.services = copy.deepcopy(services)
            self.all_services = all_services
            self.signature = fingerprint(self.services)
            self.value = {"state": "running", "started_at": now(), "report": None}
            self.thread = threading.Thread(target=self.run, args=(self.services,), daemon=True, name="network-diagnostics")
            self.thread.start()

    def run(self, services):
        start = time.monotonic()
        try:
            system = environment(self.cancel)
            deadline = time.monotonic() + 30
            with ThreadPoolExecutor(max_workers=4) as pool:
                reports = list(pool.map(lambda s: service_report(s, deadline, self.cancel), services))
            report = {"environment": system, "services": reports, "checked_at": now(), "duration_seconds": round(time.monotonic() - start, 2),
                      "source": "GUI 主动检查与原版状态快照；不是独立外网测试或带宽测速。"}
            with self.lock:
                self.value.update(state="finished", finished_at=now(), report=report)
        except Exception as error:
            with self.lock:
                self.value.update(state="error", finished_at=now(), error=str(error)[:500])

    def snapshot(self, current):
        with self.lock:
            value = copy.deepcopy(self.value)
            ids = {s["id"] for s in self.services}
            value["stale"] = self.signature is not None and fingerprint([s for s in current if self.all_services or s["id"] in ids]) != self.signature
            return value

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel.set()
        if self.thread:
            self.thread.join(timeout=5)
