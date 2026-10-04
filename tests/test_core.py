import json
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest

from natter_gui.core import CORE, ROOT, CoreStatus, command, validate_service, validate_collection


def service(**overrides):
    return {"name": "Emby", "target_ip": "127.0.0.1", "target_port": 8096, "bind_ip": "0.0.0.0",
            "bind_port": 34569, "protocol": "tcp", "enabled": False, "upnp": False, "retry_target": True, "keepalive": 15, **overrides}


class CoreTests(unittest.TestCase):
    def test_executable_upstream_notification_writes_atomic_mapping(self):
        notify = ROOT / "natter_gui" / "notify.py"
        self.assertTrue(os.access(notify, os.X_OK))
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([str(notify), "tcp", "192.168.1.100", "8096", "203.0.113.1", "34569"],
                           env=dict(os.environ, NATTER_GUI_WORKER_DIR=directory), check=True)
            from pathlib import Path
            mapping = json.loads((Path(directory) / "mapping.json").read_text())
            self.assertEqual(mapping["target_port"], 8096)
            self.assertEqual(mapping["public_port"], 34569)
            self.assertEqual(mapping["protocol"], "tcp")
            self.assertFalse((Path(directory) / "mapping.tmp").exists())

    def test_original_unknown_does_not_become_failure_or_success(self):
        status = CoreStatus()
        status.ingest("tcp://192.168.1.2:34569 <--Natter--> tcp://203.0.113.1:34569")
        status.ingest("[I] LAN > 192.168.1.2:34569 [ OPEN ]")
        status.ingest("[I] WAN > 203.0.113.1:34569 [ UNKNOWN ]")
        self.assertEqual(status.snapshot()["wan"], "UNKNOWN")
        self.assertIsNone(status.snapshot()["core_warning"])

    def test_new_mapping_invalidates_previous_checks(self):
        status = CoreStatus()
        status.ingest("[I] WAN > 203.0.113.1:123 [ OPEN ]")
        status.ingest("tcp://192.168.1.2:1 <--Natter--> tcp://203.0.113.1:456")
        self.assertEqual(status.wan, "NOT_CHECKED")
        status.ingest("[W] !! Hole punching failed !!")
        self.assertEqual(status.warning, "!! Hole punching failed !!")

    def test_mapping_alone_and_udp_have_no_wan_verdict(self):
        status = CoreStatus()
        status.ingest("udp://192.168.1.2:123 <--Natter--> udp://203.0.113.1:123")
        self.assertEqual(status.wan, "NOT_CHECKED")

    def test_diagnostic_addresses_and_upnp_are_log_evidence_not_lease_verdicts(self):
        status = CoreStatus()
        status.ingest("[I] [UPnP] Found router 192.168.1.1")
        status.ingest("[I] tcp://192.168.1.100:8096 <--socket--> tcp://192.168.1.2:4567 <--Natter--> tcp://203.0.113.1:34569")
        status.ingest("[I] WAN > 203.0.113.1:34569 [ UNKNOWN ]")
        result = status.snapshot()
        self.assertEqual(result["local_address"], {"ip": "192.168.1.2", "port": 4567})
        self.assertEqual(result["upnp"]["router"], "192.168.1.1")
        self.assertNotIn("lease_open", result["upnp"])
        self.assertIsNotNone(result["last_check_at"])
        self.assertEqual(result["wan"], "UNKNOWN")

    def test_input_validation_and_bind_conflicts(self):
        for override in ({"target_ip": ";touch /tmp/injected"}, {"target_port": True}, {"protocol": "tcp;sh"}, {"enabled": "yes"}):
            with self.assertRaises(ValueError):
                validate_service(service(**override))
        first = dict(validate_service(service(enabled=True)), id="a" * 12)
        second = dict(validate_service(service(enabled=True, bind_ip="127.0.0.1")), id="b" * 12)
        with self.assertRaises(ValueError):
            validate_collection([first, second])
        second["protocol"] = "udp"
        validate_collection([first, second])

    def test_upstream_command_contract(self):
        args = command(validate_service(service(protocol="both", upnp=True)), "udp", sys.executable)
        self.assertIn("-u", args)
        self.assertIn("-U", args)
        help_text = subprocess.check_output([sys.executable, str(CORE / "natter.py"), "--help"], text=True)
        for flag in ("-m", "-t", "-p", "-b", "-i", "-e", "-k", "-r", "-u", "-U"):
            self.assertIn(flag, help_text)
        manifest = json.loads((ROOT / "upstream.lock.json").read_text())
        version = subprocess.check_output([sys.executable, str(CORE / "natter.py"), "--version"], text=True)
        self.assertIn(manifest["tag"].lstrip("v"), version)

    def test_adapter_against_actual_upstream_wan_check(self):
        spec = importlib.util.spec_from_file_location("upstream_natter", CORE / "natter.py")
        upstream = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(upstream)
        for first, second, expected in ((1, -1, "OPEN"), (-1, 1, "OPEN"), (-1, -1, "CLOSED"), (0, -1, "UNKNOWN"), (0, 0, "UNKNOWN")):
            probe = upstream.PortTest()
            probe._test_ifconfigco = lambda *args, value=first: value
            probe._test_transmission = lambda *args, value=second: value
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                probe.test_wan(("203.0.113.1", 34569), info=True)
            status = CoreStatus()
            status.ingest(output.getvalue())
            self.assertEqual(status.wan, expected, output.getvalue())
