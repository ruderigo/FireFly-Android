"""Second FireFly engine for run_air.py, on a Bluetooth-LE-style fake RNode.
Echoes every plain LXMF message it receives.   python3 radio_peer.py <workdir>"""
import os, sys, threading, time, json
os.environ["ANDROID_ROOT"] = "/system"
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python")); sys.path.insert(0, here)
from fake_rnode import FakeLinks, FakeRNodeLink
from firefly.core import Core

links = FakeLinks()
links.ble["AA:BB:CC:00:00:02"] = FakeRNodeLink(style="ble", air=(47611, 47610))
core = None

class Echo:
    def onEvent(self, s):
        e = json.loads(s)
        if e["type"] == "message":
            threading.Thread(target=core.send, args=(e["peer"], "echo: " + e["text"]), daemon=True).start()

core = Core(sys.argv[1], links=links, listener=Echo(), log=lambda *a: None)
core.settings.update({"auto_interface": False, "display_name": "Phone B", "log_level": 2,
                      "radio": {"ble_devices": [{"address": "AA:BB:CC:00:00:02", "name": "RNode B"}]}})
core.start()
while core.radio.status.state != "online":
    time.sleep(0.2)
print("PEER ready", core.address, flush=True)
while True:
    time.sleep(3); core.announce() if time.time() % 30 < 3 else None
