"""A Stump that moved to a new address, seen from a phone with no radio (field report, Oct 18).

    python3 run_reach.py

The phone had met a computer Stump over LoRa (with an RNode) and over
Wi-Fi at 192.168.0.172. Days later: no RNode, and the router has given the
computer 192.168.0.176. The merged entry opened the LoRa side, which could
send nothing: /join did nothing, Verify waited forever, lines failed.
Now: the entry opens the side that can reach the Stump, the Stump found at
its new address replaces the old entry, the LoRa side says it has no
radio and offers Wi-Fi, and Verify over LoRa without a way there fails at
once instead of waiting.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
from fake_stump import FakeRRC, rns_verify, serve_http
from firefly import api

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    fails += 0 if ok else 1

LXMF = "b9b7de41a6b23b7a6108eb76cce3e8dd"         # the computer Stump's address
rrc = FakeRRC(rns_verify); rrc.node_lxmf = LXMF
serve_http(47620, rrc)                             # where it answers now
OLD, NEW = "http://127.0.0.1:47621", "http://127.0.0.1:47620"   # nothing answers at the old one

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-reach-"), None, None, L())
c = api._core
# What the phone remembered: a LoRa entry and a Wi-Fi entry at the old address, same Stump.
c.store.upsert_stump_node("mesh:" + LXMF, "mesh", LXMF, name="macOS", room="wifi-mesh", last_seen=time.time())
c.store.upsert_stump_node("wifi:" + OLD, "wifi", OLD, name="macOS", lxmf=LXMF, online=0, last_seen=time.time() - 60)

rows = json.loads(api.stump_nodes())
check("one entry for the Stump (LoRa + Wi-Fi)", len(rows) == 1 and rows[0]["transports"] == ["mesh", "wifi"], rows)
check("no radio: it doesn't open the LoRa side", rows[0]["key"] == "wifi:" + OLD, rows[0]["key"])

v = json.loads(api.stump_view("mesh:" + LXMF))
check("the LoRa side says nothing can reach the Stump, and offers its Wi-Fi entry",
      v["reachable"] is False and v["wifi_alt"] == "wifi:" + OLD, (v.get("reachable"), v.get("wifi_alt")))
check("Verify over LoRa with no way there fails at once, with the reason",
      api.stump_auth("mesh:" + LXMF) is False and json.loads(api.stump_view("mesh:" + LXMF))["auth"] == "failed")

key = api.stump_connect_wifi(NEW)                  # found again (Find Stumps, or typed)
t = time.time()
while time.time() - t < 15 and not json.loads(api.stump_view(key)).get("online"):
    time.sleep(0.3)
rows = json.loads(api.stump_nodes())
keys = [k for r in rows for k in [r["key"]] + [o["key"] for o in r["others"]]]
check("the Stump at its new address replaces the old entry", "wifi:" + OLD not in keys and key in keys, keys)
check("...and opens first, over Wi-Fi", rows[0]["key"] == key and rows[0]["transports"] == ["mesh", "wifi"], rows[0]["key"])
check("the LoRa side now offers the new Wi-Fi entry", json.loads(api.stump_view("mesh:" + LXMF))["wifi_alt"] == key)

api.stump_join(key, "main")
t = time.time()
while time.time() - t < 10 and json.loads(api.stump_view(key))["room"] != "main":
    time.sleep(0.3)
check("over Wi-Fi, /join works", json.loads(api.stump_view(key))["room"] == "main")

api.stop()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
