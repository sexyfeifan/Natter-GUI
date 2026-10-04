import json
import tempfile
import time
import unittest
from pathlib import Path

from natter_gui.store import Store
from natter_gui.supervisor import Manager
from tests.test_core import service

FAKE_CORE = '''
import os,sys,time,json
from pathlib import Path
p = Path(os.environ['NATTER_GUI_WORKER_DIR'])
proto = 'udp' if '-u' in sys.argv else 'tcp'
p.joinpath('mapping.json').write_text(json.dumps({'protocol':proto,'public_ip':'203.0.113.1','public_port':34569,'target_ip':'127.0.0.1','target_port':8096}))
print('tcp://127.0.0.1:34569 <--Natter--> tcp://203.0.113.1:34569',flush=True)
if proto == 'tcp': print('[I] WAN > 203.0.113.1:34569 [ UNKNOWN ]',flush=True)
while True: time.sleep(.05)
'''


def wait_for(fn, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return
        time.sleep(.03)
    raise AssertionError("condition did not become true")


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        core = base / "core"
        core.mkdir()
        (core / "natter.py").write_text(FAKE_CORE)
        (core / "natter-check.py").write_text("print('Checking TCP NAT... [ NA ] ... NAT Type: -1')\nprint('Checking UDP NAT... [ FAIL ] ... NAT Type: 3')")
        self.store = Store(base / "state")
        self.manager = Manager(self.store, core)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_crud_real_processes_and_independent_protocol_states(self):
        s = self.manager.upsert(service(enabled=True, protocol="both"))
        wait_for(lambda: all(w["mapping_active"] for w in self.manager.snapshot()[0]["workers"]))
        workers = self.manager.snapshot()[0]["workers"]
        self.assertEqual(len(workers), 2)
        wait_for(lambda: self.manager.snapshot()[0]["workers"][0]["wan"] == "UNKNOWN")
        self.assertEqual(workers[1]["wan"], "NOT_CHECKED")
        self.manager.verify(s["id"], {"success": True})
        self.assertFalse(self.manager.snapshot()[0]["manual_verification"]["stale"])
        self.manager.action(s["id"], "stop")
        self.assertFalse(self.manager.workers)
        self.assertFalse(self.store.services()[0]["enabled"])
        self.manager.action(s["id"], "start")
        wait_for(lambda: self.manager.snapshot()[0]["workers"][0]["mapping_active"])
        self.manager.delete(s["id"])
        self.assertEqual(self.store.services(), [])
        self.assertEqual(self.manager.workers, {})
        with self.assertRaises(KeyError):
            self.manager.action(s["id"], "start")

    def test_import_validation_is_atomic_and_does_not_autostart(self):
        with self.assertRaises(ValueError):
            self.manager.import_services({"schema_version": 1, "services": [service(), service(target_port=0)]})
        self.assertEqual(self.store.services(), [])
        values = self.manager.import_services({"schema_version": 1, "services": [service(enabled=True)]})
        self.assertFalse(values[0]["enabled"])
        self.assertFalse(self.manager.workers)

    def test_nat_job_keeps_original_unknown_and_fail(self):
        self.manager.check_nat()
        wait_for(lambda: self.manager.nat["state"] == "finished")
        self.assertEqual([r["status"] for r in self.manager.nat["results"]], ["NA", "FAIL"])
        self.assertIn("NAT Type: -1", self.manager.nat["raw"])

    def test_manual_validation_becomes_stale_when_mapping_changes(self):
        s = self.manager.upsert(service(enabled=True))
        wait_for(lambda: self.manager.snapshot()[0]["workers"][0]["mapping_active"])
        self.manager.verify(s["id"], {"success": True})
        worker = self.manager.workers[(s["id"], "tcp")]
        mapping = json.loads((worker.directory / "mapping.json").read_text())
        mapping["public_port"] = 45678
        (worker.directory / "mapping.json").write_text(json.dumps(mapping))
        self.assertTrue(self.manager.snapshot()[0]["manual_verification"]["stale"])
