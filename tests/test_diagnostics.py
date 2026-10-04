import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time
import unittest
from unittest.mock import patch

from natter_gui.diagnostics import Diagnostics, explain_nat, fingerprint, parse_connections, route_to, service_report, tcp_probe
from tests.test_core import service
from tests.test_manager import wait_for


def snapshot(protocol="tcp", runtime="stopped", **overrides):
    return dict(service(protocol=protocol, **overrides), id="a" * 12, workers=[{
        "protocol": protocol, "runtime": runtime, "pid": 123 if runtime == "running" else None,
        "wan": "UNKNOWN" if protocol == "tcp" else "NOT_CHECKED", "lan": {},
        "mapping": {"public_ip": "203.0.113.1", "public_port": 34569}, "mapping_active": runtime == "running",
        "local_address": {"ip": "127.0.0.1", "port": 34569}, "restarts": 0}], manual_verification=None)


class DiagnosticTests(unittest.TestCase):
    def test_nat_explanation_preserves_original_verdict_and_unknown(self):
        for number, expected in [(-1, "无法判定"), (3, "端口受限型 NAT"), (4, "对称型 NAT")]:
            value = explain_nat({"protocol": "udp", "status": "NA" if number == -1 else "FAIL", "detail": f"NAT Type: {number}"})
            self.assertEqual(value["title"], expected)
            self.assertEqual(value["nat_type"], number)
            self.assertNotEqual(value["status"], "OK")
        error = explain_nat({"protocol": "tcp", "status": "FAIL", "detail": "No STUN server available"})
        self.assertIsNone(error["nat_type"])
        self.assertEqual(error["status"], "FAIL")

    def test_live_tcp_and_http_probe_does_not_follow_redirects(self):
        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/never-follow")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            report = tcp_probe("127.0.0.1", server.server_port, time.monotonic() + 4, threading.Event(), "/")
            self.assertEqual(report["status"], "OPEN")
            self.assertEqual(report["http"]["code"], 302)
            self.assertEqual(report["http"]["status"], "RESPONDED")
            self.assertGreaterEqual(report["connect_ms"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_timeout_is_unknown_refusal_closed_and_expired_probe_skipped(self):
        for error, verdict in [(TimeoutError("timed out"), "UNKNOWN"), (OSError(errno.ECONNREFUSED, "refused"), "CLOSED")]:
            with patch("natter_gui.diagnostics.socket.socket") as fake:
                fake.return_value.__enter__.return_value.connect.side_effect = error
                report = tcp_probe("127.0.0.1", 1234, time.monotonic() + 2, threading.Event())
                self.assertEqual(report["status"], verdict)
        with patch("natter_gui.diagnostics.socket.socket") as fake:
            self.assertEqual(tcp_probe("127.0.0.1", 1234, time.monotonic() - 1, threading.Event())["status"], "SKIPPED")
            fake.assert_not_called()

    def test_udp_never_uses_tcp_and_stopped_mapping_not_probed(self):
        with patch("natter_gui.diagnostics.tcp_probe") as probe:
            udp = service_report(snapshot("udp", "running"), time.monotonic() + 2, threading.Event())
            probe.assert_not_called()
            self.assertEqual(udp["target_probe"]["status"], "NOT_CHECKED")
            self.assertEqual(udp["workers"][0]["original_wan"], "NOT_CHECKED")
            self.assertEqual(udp["workers"][0]["public_lan_probe"]["status"], "NOT_CHECKED")
        with patch("natter_gui.diagnostics.tcp_probe", return_value={"status": "OPEN"}) as probe:
            stopped = service_report(snapshot(), time.monotonic() + 2, threading.Event())
            self.assertEqual(probe.call_count, 1)  # Target only; no historical public/bind probes.
            self.assertEqual(stopped["workers"][0]["bind_probe"]["status"], "SKIPPED")

    def test_connections_parser_keeps_queue_rtt_and_window_information(self):
        parsed = parse_connections("0 3768000 192.168.1.2:34569 203.0.113.3:4567\n\t cubic rtt:172.25/10.1 bytes_retrans:119520 rwnd_limited:147784ms(61.0%)\n")
        self.assertEqual(parsed[0]["peer"], "203.0.113.3:4567")
        self.assertEqual(parsed[0]["send_queue"], 3768000)
        self.assertEqual(parsed[0]["rtt_ms"], 172.25)
        self.assertEqual(parsed[0]["receive_window_limited_percent"], 61)
        same_line = parse_connections("0 100 127.0.0.1:34569 203.0.113.3:4567 cubic rtt:12.5/2 bytes_retrans:20\n")
        self.assertEqual(same_line[0]["rtt_ms"], 12.5)

    def test_routes_are_read_only_use_effective_uid_and_unavailable_is_explicit(self):
        with patch("natter_gui.diagnostics.platform.system", return_value="Linux"), patch("natter_gui.diagnostics.shutil.which", return_value="/sbin/ip"), patch("natter_gui.diagnostics.command_json", return_value=([{"gateway": "192.168.1.1", "dev": "end0", "table": 34568, "prefsrc": "192.168.1.2"}], None)) as command:
            route = route_to("1.1.1.1", 986)
            self.assertEqual(route["table"], 34568)
            self.assertEqual(command.call_args.args[0], ["/sbin/ip", "-j", "-4", "route", "get", "1.1.1.1", "uid", "986"])
        with patch("natter_gui.diagnostics.shutil.which", return_value=None):
            self.assertFalse(route_to("1.1.1.1")["available"])
        with self.assertRaises(ValueError):
            route_to("1.1.1.1;touch /tmp/injected")

    def test_async_single_flight_and_stale_snapshots_on_restart_or_added_service(self):
        diagnostic = Diagnostics()
        selected = snapshot()
        entered, release = threading.Event(), threading.Event()
        def env(cancel):
            entered.set()
            release.wait(2)
            return {"platform": "test"}
        try:
            with patch("natter_gui.diagnostics.environment", side_effect=env), patch("natter_gui.diagnostics.service_report", return_value={"id": selected["id"]}):
                diagnostic.start([selected])
                self.assertTrue(entered.wait(1))
                with self.assertRaises(ValueError):
                    diagnostic.start([selected])
                release.set()
                wait_for(lambda: diagnostic.snapshot([selected])["state"] == "finished")
                self.assertFalse(diagnostic.snapshot([selected])["stale"])
                extra = dict(selected, id="b" * 12)
                self.assertTrue(diagnostic.snapshot([selected, extra])["stale"])
                changed = snapshot()
                changed["workers"][0]["pid"] = 124
                self.assertNotEqual(fingerprint([selected]), fingerprint([changed]))
                self.assertTrue(diagnostic.snapshot([changed])["stale"])
                diagnostic.start([selected], all_services=False)
                wait_for(lambda: diagnostic.snapshot([selected])["state"] == "finished")
                self.assertFalse(diagnostic.snapshot([selected, extra])["stale"])
        finally:
            release.set()
            diagnostic.close()
