import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import route_helper


class RouteTests(unittest.TestCase):
    def execute(self, action="start", gateway="192.168.1.1", rules="", uid=1234):
        self.calls = []

        def run(argv, **kwargs):
            self.calls.append(argv[1:])
            output = ""
            if argv[1:] == ["rule", "show"]:
                output = rules
            if argv[1:5] == ["-j", "-4", "addr", "show"]:
                output = '[{"addr_info":[{"family":"inet","local":"192.168.1.50","prefixlen":24}]}]'
            return subprocess.CompletedProcess(argv, 0, output, "")

        with patch("sys.argv", ["route-helper", action, "--gateway", gateway, "--interface", "end0"]), \
             patch.object(route_helper.os, "geteuid", return_value=0), \
             patch.object(route_helper.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=uid)), \
             patch.object(route_helper.shutil, "which", return_value="/usr/sbin/ip"), \
             patch.object(route_helper.subprocess, "run", side_effect=run):
            route_helper.main()

    def test_start_only_adds_dedicated_uid_rule_and_table(self):
        self.execute()
        self.assertIn(["rule", "add", "pref", "13457", "uidrange", "1234-1234", "lookup", "34568"], self.calls)
        routes = [call for call in self.calls if call[:2] == ["route", "replace"]]
        self.assertEqual(len(routes), 2)
        self.assertTrue(all(call[2:4] == ["table", "34568"] for call in routes))

    def test_unreachable_gateway_rejected_before_any_mutation(self):
        with self.assertRaises(SystemExit):
            self.execute(gateway="192.168.2.1")
        self.assertFalse(any("replace" in call or "add" in call for call in self.calls))

    def test_stop_requires_rule_ownership_and_is_idempotent(self):
        self.execute(action="stop")
        self.assertEqual(self.calls, [["rule", "show"]])
        self.execute(action="stop", rules="13457: from all uidrange 1234-1234 lookup 34568\n")
        self.assertIn(["route", "flush", "table", "34568"], self.calls)
        with self.assertRaises(SystemExit):
            self.execute(action="stop", rules="13457: from all uidrange 8888-8888 lookup 34568\n")
        self.assertEqual(self.calls, [["rule", "show"]])

    def test_root_uid_cannot_be_selected(self):
        with self.assertRaises(SystemExit):
            self.execute(uid=0)
        self.assertEqual(self.calls, [])
