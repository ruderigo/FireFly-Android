"""The Stumps list keeps its order while Stumps are heard (field report, Oct 19).

    python3 run_order.py

Stumps close to each other beacon and answer all the time; the list was
sorted by "last heard", so rows kept swapping. Now it's by name, whatever
is heard. Also: the status's Wi-Fi counts behind the Wi-Fi light.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
from firefly import api

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    fails += 0 if ok else 1

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-order-"), None, None, L())
c = api._core
for name, lx in (("LaBuche", "aa" * 16), ("macOS", "bb" * 16), ("Erable", "cc" * 16)):
    c.store.upsert_stump_node("mesh:" + lx, "mesh", lx, name=name, last_seen=time.time())
names = lambda: [r["name"] for r in json.loads(api.stump_nodes())]
first = names()
check("sorted by name", first == ["Erable", "LaBuche", "macOS"], first)
for lx in ("bb" * 16, "cc" * 16, "aa" * 16, "bb" * 16):
    time.sleep(0.05)
    c.store.upsert_stump_node("mesh:" + lx, "mesh", lx, last_seen=time.time())     # heard again
    if names() != first:
        break
check("being heard doesn't move them", names() == first, names())
rows = json.loads(api.stump_nodes())
check("each row has a steady group key", all(r.get("group") for r in rows), rows)
ws = json.loads(api.status()).get("wifi_stumps")
check("status: Wi-Fi Stump counts for the Wi-Fi light", ws == {"open": 0, "online": 0}, ws)
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
