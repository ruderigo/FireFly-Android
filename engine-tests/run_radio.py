"""The radio path, with Reticulum in Android mode, against simulated RNodes.

Proves the RadioLink design: Reticulum's own RNodeInterface detects,
configures and verifies an RNode through a USB-style link and a
Bluetooth-LE-style link (chunked notifications, queued writes, latency),
retunes live, survives an unplug, reports old firmware, and never touches
Reticulum's platform check outside its own constructor.

    python3 engine-tests/run_radio.py
"""
import json, os, sys, tempfile, time, shutil
os.environ["ANDROID_ROOT"] = "/system"        # what every Android app process has
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python"))
sys.path.insert(0, here)

import RNS
from fake_rnode import FakeLinks, FakeRNodeLink
from firefly.core import Core
from firefly import api

ok = True


def check(cond, what):
    global ok
    print(("  PASS " if cond else "  FAIL ") + what)
    ok = ok and bool(cond)


def wait(cond, secs, what):
    t = time.time()
    while time.time() - t < secs:
        if cond():
            print(f"  PASS {what} ({time.time()-t:.1f}s)")
            return True
        time.sleep(0.2)
    check(False, what)
    return False


check(RNS.vendor.platformutils.is_android(), "Reticulum sees Android (ANDROID_ROOT set)")
R = sys.modules["RNS.Reticulum"]
check("Android" in R.RNodeInterface.__name__, "Reticulum loaded its Android interface set")

tmp = tempfile.mkdtemp(prefix="ff-radio-")
links = FakeLinks()
events = []


class L:
    def onEvent(self, s): events.append(s)


core = Core(tmp, links=links, listener=L(), log=lambda *a: None)
core.settings.update({"auto_interface": False, "log_level": 2})
core.start()
api._core = core

check(RNS.vendor.platformutils.is_android(), "platform check still says Android outside the RNode driver")
wait(lambda: core.radio.status.state == "no devices", 8, "no radio yet: status says so, app keeps running")

print("-- USB")
usb = FakeRNodeLink(style="usb")
links.usb["usb:/dev/bus/usb/001/004"] = usb
core.radio.search_now()
wait(lambda: core.radio.status.state == "online", 15, "USB RNode detected, configured and online")
check(usb.freq == 915_000_000 and usb.bw == 125_000 and usb.sf == 8 and usb.cr == 5 and usb.txp == 7,
      "radio set to the Stump defaults 915.0 / 125 kHz / SF8 / 4/5 / 7 dBm")
check(core.radio.status.board == "ESP32, firmware 1.82", f"board reported: {core.radio.status.board}")
check(any(i["name"].startswith("RNode") for i in core.interface_stats()), "interface visible to Reticulum")

core.settings.update({"radio": {"spreading_factor": 9}})
core.radio.request_apply(0.1)
wait(lambda: usb.sf == 9 and core.radio.status.state == "online", 15, "live retune to SF9, no restart")

core.settings.update({"radio": {"tx_power": 17}})
core.radio.request_apply(0.1)
wait(lambda: usb.txp == 17 and core.radio.status.state == "online", 15, "TX power 17 dBm applied to the radio")
wait(lambda: core.radio.snapshot().get("txpower") == 17, 5, "status reports 17 dBm as on air")
check(json.loads(api.status())["radio_wanted"]["tx_power"] == 17, "status carries the wanted settings too")

usb.unplug()
wait(lambda: core.radio.status.state in ("no devices", "searching"), 12, "cable pulled: detected, searching")
del links.usb["usb:/dev/bus/usb/001/004"]

print("-- Bluetooth LE")
ble = FakeRNodeLink(style="ble")
links.ble["AA:BB:CC:DD:EE:01"] = ble
api.add_ble_device("aa:bb:cc:dd:ee:01", "RNode 1A2B")
wait(lambda: core.radio.status.state == "online", 25, "paired BLE RNode online (chunked, latent link)")
check(core.radio.status.port == "ble:AA:BB:CC:DD:EE:01", f"port {core.radio.status.port}")
check(ble.sf == 9 and ble.txp == 17 and ble.freq == 915_000_000, "BLE RNode got the current settings (SF9, 17 dBm)")
snap = core.radio.snapshot()
check(snap["kind"] == "ble" and snap["sf"] == 9, "status snapshot reports the BLE radio")

ble.unplug()          # out of range / switched off
wait(lambda: core.radio.status.state != "online", 12, "BLE link lost: detected")
ble2 = FakeRNodeLink(style="ble")
links.ble["AA:BB:CC:DD:EE:01"] = ble2
core.radio.search_now()
wait(lambda: core.radio.status.state == "online", 25, "BLE RNode back in range: reconnected by itself")
check(core.radio.snapshot().get("device") == "RNode 1A2B", f"status names the RNode in use ({core.radio.snapshot().get('device')})")

print("-- two paired RNodes: Use switches")
other = FakeRNodeLink(style="ble")
links.ble["AA:BB:CC:DD:EE:02"] = other
api.add_ble_device("aa:bb:cc:dd:ee:02", "RNode EA69")
time.sleep(3)
check(core.radio.status.port == "ble:AA:BB:CC:DD:EE:01", "pairing a second one doesn't take over the one in use")
api.use_ble_device("aa:bb:cc:dd:ee:02")
check(core.radio.snapshot()["busy"] is True, "Use: busy at once (the button waits, the light flashes)")
wait(lambda: core.radio.status.state == "online" and core.radio.status.port == "ble:AA:BB:CC:DD:EE:02", 25,
     "Use: switched to RNode EA69")
wait(lambda: core.radio.snapshot()["busy"] is False, 10, "...and no longer busy once it's done")
check(core.radio.snapshot().get("device") == "RNode EA69", "status names it")
check(other.sf == 9, "it got the current settings")
api.use_ble_device("aa:bb:cc:dd:ee:01")
wait(lambda: core.radio.status.state == "online" and core.radio.status.port == "ble:AA:BB:CC:DD:EE:01", 25,
     "Use: and back to RNode 1A2B")
api.remove_ble_device("AA:BB:CC:DD:EE:02")

api.radio_search_now()
check(core.radio.snapshot()["busy"] is True, "Search now: busy at once")
wait(lambda: core.radio.snapshot()["busy"] is False, 15, "...and not once the search is done")

print("-- the RNode must confirm its frequency (field report, Oct 25: set 907, still on 915)")
api.remove_ble_device("AA:BB:CC:DD:EE:01")
wait(lambda: core.radio.status.state != "online", 10, "the BLE RNode let go")
usb = FakeRNodeLink(style="usb")
links.usb["usb:/dev/bus/usb/001/005"] = usb
core.settings.update({"radio": {"frequency": 915_000_000}})
core.radio.search_now()
wait(lambda: core.radio.status.state == "online", 20, "online on 915 MHz")
snap = core.radio.snapshot()
check(snap.get("confirmed") is True and snap["frequency"] == 915_000_000, f"confirmed by the RNode itself ({snap.get('frequency')})")
core.settings.update({"radio": {"frequency": 907_000_000}})
core.radio.request_apply(0.2)
wait(lambda: core.radio.status.state == "online" and core.radio.snapshot().get("frequency") == 907_000_000, 20,
     "retuned to 907: the RNode reports 907, confirmed")
check(core.radio.snapshot().get("confirmed") is True, "...confirmed")
usb.unplug()
wait(lambda: core.radio.status.state != "online", 10, "unplugged")

stuck = FakeRNodeLink(style="usb", stuck_freq=915_000_000)       # stays on 915, and says so
links.usb["usb:/dev/bus/usb/001/005"] = stuck
core.radio.search_now()
wait(lambda: core.radio.status.state == "refused", 20, "an RNode stuck on 915 when 907 is asked: not online")
d = core.radio.status.detail or ""
check("915.000" in d and "907.000" in d, f"...and it says so: {d}")
stuck.unplug()
wait(lambda: core.radio.status.state != "refused" or True, 1, "")

silent = FakeRNodeLink(style="usb", echo_freq=False)               # never reports its frequency
links.usb["usb:/dev/bus/usb/001/006"] = silent
core.radio.search_now()
wait(lambda: core.radio.status.state == "refused" and "never confirmed" in (core.radio.status.detail or ""), 30,
     "an RNode that never reports its frequency: not online (Reticulum alone would have said online)")
check(silent.set_log.count(("freq", 907_000_000)) >= 2, "...after being asked twice")
silent.unplug()
for p in ("usb:/dev/bus/usb/001/005", "usb:/dev/bus/usb/001/006"):
    links.usb.pop(p, None)
core.settings.update({"radio": {"frequency": 915_000_000}})

print("-- old firmware")
api.remove_ble_device("AA:BB:CC:DD:EE:01")
wait(lambda: core.radio.status.state != "online", 10, "unpaired: radio detached")
old = FakeRNodeLink(style="usb", fw=(1, 40))
links.usb["usb:/dev/bus/usb/001/009"] = old
core.radio.search_now()
wait(lambda: core.radio.status.state == "refused", 15, "old firmware reported, app keeps running")
print("     ", core.radio.status.detail)

check(any('"type": "status"' in e for e in events), "status events reached the listener")
core.stop()
shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED")
sys.stdout.flush()
os._exit(0 if ok else 1)
