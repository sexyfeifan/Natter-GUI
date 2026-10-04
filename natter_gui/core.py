"""Validation, command generation and lossless upstream status extraction."""
import ipaddress
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "vendor" / "natter"
ID = re.compile(r"^[a-f0-9]{12}$")


def validate_service(data):
    if not isinstance(data, dict):
        raise ValueError("服务必须是 JSON 对象")
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("名称需要 1–80 个字符")
    target = str(ipaddress.IPv4Address(data.get("target_ip", "")))
    if ipaddress.IPv4Address(target).is_unspecified or ipaddress.IPv4Address(target).is_multicast:
        raise ValueError("目标地址必须是单播 IPv4")
    bind = str(ipaddress.IPv4Address(data.get("bind_ip", "0.0.0.0")))
    if ipaddress.IPv4Address(bind).is_multicast:
        raise ValueError("绑定地址不能是组播地址")
    protocol = data.get("protocol", "tcp")
    if protocol not in ("tcp", "udp", "both"):
        raise ValueError("协议必须为 tcp、udp 或 both")
    result = {"name": name, "target_ip": target, "bind_ip": bind, "protocol": protocol}
    for key, default, low, high in (("target_port", 0, 1, 65535), ("bind_port", 0, 0, 65535), ("keepalive", 15, 1, 3600)):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} 必须为 {low}–{high} 的整数")
        result[key] = value
    for key, default in (("enabled", False), ("upnp", True), ("retry_target", True)):
        value = data.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须是布尔值")
        result[key] = value
    return result


def protocols(service):
    return ("tcp", "udp") if service["protocol"] == "both" else (service["protocol"],)


def validate_collection(services):
    if len(services) > 64:
        raise ValueError("最多配置 64 个服务")
    seen = set()
    for service in services:
        if not ID.fullmatch(service.get("id", "")) or service["id"] in seen:
            raise ValueError("服务 ID 无效或重复")
        seen.add(service["id"])
    for i, first in enumerate(services):
        if not first["enabled"] or not first["bind_port"]:
            continue
        for second in services[i + 1:]:
            overlap = first["bind_ip"] == second["bind_ip"] or "0.0.0.0" in (first["bind_ip"], second["bind_ip"])
            if second["enabled"] and overlap and first["bind_port"] == second["bind_port"] and set(protocols(first)) & set(protocols(second)):
                raise ValueError("启用的服务存在绑定端口冲突")


def command(service, protocol, python, core=CORE):
    args = [python, "-u", str(core / "natter.py"), "-m", "socket", "-i", service["bind_ip"], "-b", str(service["bind_port"]),
            "-t", service["target_ip"], "-p", str(service["target_port"]), "-k", str(service["keepalive"]),
            "-e", str(ROOT / "natter_gui" / "notify.py")]
    if protocol == "udp":
        args.append("-u")
    if service["upnp"]:
        args.append("-U")
    if service["retry_target"]:
        args.append("-r")
    return args


class CoreStatus:
    """Retain original results; a mapping notification is not a WAN probe."""
    def __init__(self):
        self.lan = {}
        self.wan = "NOT_CHECKED"
        self.warning = None

    def ingest(self, line):
        # A new route means new checks: discard results for the previous mapping.
        if "<--Natter-->" in line:
            self.lan = {}
            self.wan = "NOT_CHECKED"
            self.warning = None
        match = re.search(r"\b(LAN|WAN) >\s*(\S+)\s*\[\s*(OPEN|CLOSED|UNKNOWN)\s*\]", line)
        if match:
            kind, address, result = match.groups()
            if kind == "WAN":
                self.wan = result
            else:
                self.lan[address] = result
        for text in ("!! Target port is closed !!", "!! Hole punching failed !!", "!! You may be behind a firewall !!"):
            if text in line:
                self.warning = text

    def snapshot(self):
        return {"lan": dict(self.lan), "wan": self.wan, "core_warning": self.warning}
