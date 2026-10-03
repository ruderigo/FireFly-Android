"""Two FireFly engines, two processes, one simulated LoRa channel.

Phone A: USB RNode.  Phone B: Bluetooth LE RNode.  Every byte goes through
Reticulum's RNodeInterface and FireFly's RadioLink shim on both sides.

    python3 engine-tests/run_air.py
"""
import os, sys, subprocess, tempfile, time, shutil
os.environ["ANDROID_ROOT"] = "/system"
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python")); sys.path.insert(0, here)
from fake_rnode import FakeLinks, FakeRNodeLink
from firefly.core import Core

ok = True
def wait(cond, secs, what):
    global ok
    t = time.time()
    while time.time() - t < secs:
        if cond():
            print(f"  PASS {what} ({time.time()-t:.1f}s)"); return True
        time.sleep(0.2)
    print(f"  FAIL {what}"); ok = False; return False

tmp = tempfile.mkdtemp(prefix="ff-air-")
peer = subprocess.Popen([sys.executable, os.path.join(here, "radio_peer.py"), os.path.join(tmp, "b")],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
line = peer.stdout.readline()
while "PEER ready" not in line:
    line = peer.stdout.readline()
    if not line: print("peer died"); sys.exit(1)
b_addr = line.split()[2]
print("phone B (BLE RNode) is", b_addr)

links = FakeLinks()
usb = FakeRNodeLink(style="usb", air=(47610, 47611))
links.usb["usb:/dev/bus/usb/002/003"] = usb
a = Core(os.path.join(tmp, "a"), links=links, log=lambda *x: None)
a.settings.update({"auto_interface": False, "display_name": "Phone A", "log_level": 2})
a.start()
wait(lambda: a.radio.status.state == "online", 15, "phone A's USB RNode online")
wait(lambda: (a.store.peer(b_addr) or {}).get("name") == "Phone B", 40, "A heard B's announce over LoRa")
mid = a.send(b_addr, "Salut B, ça passe par la radio ✓")
wait(lambda: a.store.message(mid)["state"] == "delivered", 40, "A -> B delivered, proof came back over the air")
wait(lambda: any(m["content"].startswith("echo: Salut") for m in a.store.messages(b_addr) if not m["outgoing"]),
     40, "B's echo received by A")
got = [m for m in a.store.messages(b_addr) if not m["outgoing"]][-1:]
if got:
    print("      rssi", got[0]["rssi"], "snr", got[0]["snr"], "method", got[0]["method"])
snap = a.radio.snapshot()
print("      radio: rssi", snap.get("rssi"), "snr", snap.get("snr"), "rx", snap.get("rx"), "tx", snap.get("tx"))
big = "L" * 700
mid2 = a.send(b_addr, big)
wait(lambda: a.store.message(mid2)["state"] == "delivered", 90, "700-char message over a Reticulum link, via both radios")
print("      frames A put on air:", usb.frames_on_air)
a.stop(); peer.terminate()
shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED"); sys.stdout.flush(); os._exit(0 if ok else 1)
