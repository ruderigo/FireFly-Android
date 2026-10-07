"""A Stump node not yet updated for Opus: FireFly's Opus voice DM fails with the node's words.

    python3 engine-tests/run_stump_legacy.py
"""
import json, os, sys, tempfile, time, shutil
os.environ["FAKE_STUMP_CODEC2_ONLY"] = "1"
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python")); sys.path.insert(0, here)
from firefly.core import Core
from firefly import api
from fake_stump import FakeRRC, rns_verify, serve_http

ok = True
def check(c, w):
    global ok
    print(("  PASS " if c else "  FAIL ") + w); ok = ok and bool(c)
def wait(cond, secs, w):
    t = time.time()
    while time.time() - t < secs:
        try:
            if cond(): print(f"  PASS {w} ({time.time()-t:.1f}s)"); return True
        except Exception: pass
        time.sleep(0.2)
    check(False, w); return False

tmp = tempfile.mkdtemp(prefix="ff-legacy-")
core = Core(os.path.join(tmp, "me"), log=lambda *a: None)
core.settings.update({"auto_interface": False, "log_level": 2, "radio": {"enabled": False}})
core.start(); api._core = core
srv = serve_http(47560, FakeRRC(rns_verify))
k = api.stump_connect_wifi("127.0.0.1:47560")
wait(lambda: json.loads(api.stump_view(k))["online"], 15, "connected to a Codec 2-only node over Wi-Fi")
note = open(os.path.join(here, "kristoff_opus.ogg"), "rb").read()
d = api.stump_send_voice(k, "alice", 16, note)
row = lambda: next(x for x in json.loads(api.stump_dm_thread(k, "alice")) if x["id"] == d)
wait(lambda: row()["state"] == "failed", 20, "Opus voice DM to a node that refuses it: failed, not stuck pending")
print("      reason:", row()["reason"])
check("Codec 2 expected" in row()["reason"], "the reason is the node's own words")
srv.shutdown(); core.stop(); shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED"); sys.stdout.flush(); os._exit(0 if ok else 1)
