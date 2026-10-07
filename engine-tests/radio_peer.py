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
        if e["type"] != "message":
            return
        if e.get("audio"):
            # Report exactly what arrived: mode, length and a hash of the voice note.
            import hashlib
            row = [m for m in core.store.messages(e["peer"]) if not m["outgoing"] and m["audio_mode"]][-1]
            mode, data = core.store.message_audio(row["id"])
            reply = f"voice {mode} {len(data)} {hashlib.sha256(data).hexdigest()[:16]}"
        else:
            reply = "echo: " + e["text"]
        threading.Thread(target=core.send, args=(e["peer"], reply), daemon=True).start()

core = Core(sys.argv[1], links=links, listener=Echo(), log=lambda *a: None)
core.settings.update({"auto_interface": False, "display_name": "Phone B", "log_level": 2,
                      "radio": {"ble_devices": [{"address": "AA:BB:CC:00:00:02", "name": "RNode B"}]}})
core.start()
while core.radio.status.state != "online":
    time.sleep(0.2)
print("PEER ready", core.address, flush=True)
while True:
    time.sleep(3); core.announce() if time.time() % 30 < 3 else None
