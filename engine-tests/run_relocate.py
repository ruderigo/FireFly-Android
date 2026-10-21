"""A Stump kept by its identity, found again when the router moves it (Stump 1.0.0-beta.21).

    python3 run_relocate.py

The laptop Stump went .172 -> .175 -> .176 in a week, and FireFly kept
sending to the old address. Since 1.0.0-beta.21 every Stump answers
"STUMP?" on UDP 4244 with its name, its lxmf (which never changes) and its
current web addresses, says the addresses in its beacon, and lists the other
Stumps it heard in /stumps.json. Here, on loopback (127.0.0.2 and .3 stand in
for the Wi-Fi's broadcast):
  * Find Stumps lists the Stumps that answer, and the ones they heard;
  * a Wi-Fi Stump that stops answering is asked for again by its lxmf and
    followed to its new address: same entry, same history, a note saying so,
    and the screen of the old address follows it;
  * a line sent while it moved is carried to the new address;
  * a Stump known only over LoRa that answers on the Wi-Fi gets a Wi-Fi entry
    (so it's chatted with over Wi-Fi), and so does "Find over Wi-Fi";
  * the beacon's fifth item (its addresses) is read; older 4-item beacons as before.
"""
import json, os, sys, tempfile, time
os.environ["FIREFLY_BROADCAST"] = "127.0.0.2,127.0.0.3"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
from fake_stump import FakeRRC, rns_verify, serve_http, serve_udp
from firefly import api, discovery, stump
import RNS.vendor.umsgpack as msgpack

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    fails += 0 if ok else 1

def wait(cond, secs=20):
    t = time.time()
    while time.time() - t < secs:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False

A_LX, B_LX, C_LX = "a1" * 16, "b2" * 16, "c3" * 16
where = {"A": 47660}                                      # the laptop's port: "its address"
def base(port): return f"http://127.0.0.1:{port}"
def node(name, lx): return {"name": name, "lxmf": lx, "version": "1.0.0", "release": "1.0.0-beta.21",
                            "ap_ip": None, "chain": None, "features": ["chat"]}

rrcA = FakeRRC(rns_verify); rrcA.node_lxmf = A_LX
dirA = lambda: {"self": {}, "stumps": [{"name": "Shed", "lxmf": C_LX, "http": [base(47662) + "/"], "heard_s": 12}]}
srvA = serve_http(where["A"], rrcA, node=node("macOS", A_LX), directory=dirA)
serve_udp("127.0.0.2", lambda: {"stump": 1, "name": "macOS", "lxmf": A_LX, "release": "1.0.0-beta.21",
                                "http": [base(where["A"]) + "/"], "here": base(where["A"]) + "/"})
rrcB = FakeRRC(rns_verify); rrcB.node_lxmf = B_LX
serve_http(47661, rrcB, node=node("LaBuche", B_LX))
serve_udp("127.0.0.3", {"stump": 1, "name": "LaBuche", "lxmf": B_LX, "release": "1.0.0-beta.21",
                        "http": [base(47661) + "/", "http://192.168.4.1/"], "here": ""})
rrcC = FakeRRC(rns_verify); rrcC.node_lxmf = C_LX
serve_http(47662, rrcC, node=node("Shed", C_LX))          # an older Stump: no STUMP? answer

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-reloc-"), None, None, L())
c = api._core

# ---- asking the network
cards = discovery.ask(c.http)
check("STUMP? finds both Stumps that answer", sorted(x["name"] for x in cards) == ["LaBuche", "macOS"], cards)
lab = next(x for x in cards if x["name"] == "LaBuche")
check("an answer without 'here' uses the address it answered on", lab["base"] == base(47661), lab)
found = discovery.find_all(c.http, "127.0.0.1", 24, sweep_too=False)
names = {f["name"]: f.get("found_by") for f in found}
check("Find Stumps: the ones that answer, and the one they heard (/stumps.json)",
      names == {"macOS": "answer", "LaBuche": "answer", "Shed": "heard"}, names)
check("...each with its steadiness", all(f.get("steady_bars") is not None and f["stump"] for f in found))

# ---- the beacon's addresses
h = bytes.fromhex(A_LX)
check("beacon: fifth item read as its addresses",
      stump.beacon_http(msgpack.packb(["stump", "1.0.0", "macOS", h, ["http://192.168.0.176/"]])) == ["http://192.168.0.176/"])
check("beacon: four items as before (no addresses)",
      stump.beacon_http(msgpack.packb(["stump", "1.0.0", "macOS", h])) == []
      and stump.parse_beacon(msgpack.packb(["stump", "1.0.0", "macOS", h, ["http://x/"]])) == (A_LX, "macOS", "1.0.0"))

# ---- the router moves the laptop
r = json.loads(api.stump_connect_checked(base(47660)))
k1 = r["key"]
check("connected to the laptop", wait(lambda: json.loads(api.stump_view(k1)).get("online") == 1
                                      and json.loads(api.stump_view(k1)).get("lxmf") == A_LX))
api.stump_post(k1, "before the move")
check("a line there", wait(lambda: any(m["body"] == "before the move" for m in rrcA.rooms["main"])))

srvA.shutdown(); srvA.server_close()                      # gone from .172 ...
where["A"] = 47663
srvA2 = serve_http(where["A"], rrcA, node=node("macOS", A_LX), directory=dirA)     # ... and at .176
k2 = "wifi:" + base(47663)
check("it stops answering, FireFly asks where it went and follows it",
      wait(lambda: json.loads(api.stump_view(k1)).get("moved_to") == k2, 40),
      json.loads(api.stump_view(k1)).get("moved_to"))
keys = [n["key"] for n in json.loads(api.stump_nodes())]
check("one entry, at the new address", k2 in keys and k1 not in keys, keys)
v = json.loads(api.stump_view(k2))
bodies = [l["body"] for l in v["lines"]]
check("its history came along", "before the move" in bodies, bodies[-6:])
check("...with a note saying where it was found", any(b.startswith("found ") and "at its new address (http://127.0.0.1:47663)" in b for b in bodies), bodies[-4:])
check("...and nothing twice (what it had is carried over)", bodies.count("before the move") == 1, bodies)
check("and it's online there", wait(lambda: json.loads(api.stump_view(k2)).get("online") == 1))

# ---- a line sent while it moves again
srvA2.shutdown(); srvA2.server_close()
where["A"] = 47664
srvA3 = serve_http(where["A"], rrcA, node=node("macOS", A_LX), directory=dirA)
c._relocate_tried.clear()
api.stump_post(k2, "sent during the move")
check("a line sent to the old address is carried to the new one",
      wait(lambda: any(m["body"] == "sent during the move" for m in rrcA.rooms["main"]), 40))
check("...and not marked 'not sent'",
      not any(l["body"] == "sent during the move" and l.get("state") in ("failed", "error")
              for l in json.loads(api.stump_view("wifi:" + base(47664)))["lines"]))
check("an old screen two moves back still finds it", json.loads(api.stump_view(k1)).get("moved_to") == "wifi:" + base(47664))

# ---- a Stump known only over LoRa, answering on this Wi-Fi
c.store.upsert_stump_node("mesh:" + B_LX, "mesh", B_LX, name="LaBuche", last_seen=time.time())
c._refreshed_at = 0
got = json.loads(api.stump_refresh_wifi())
rows = json.loads(api.stump_nodes())
lb = next((r for r in rows if r.get("name") == "LaBuche"), None)
check("a LoRa-only Stump that answers on the Wi-Fi gets a Wi-Fi entry", "wifi:" + base(47661) in got, got)
check("...one row, LoRa + Wi-Fi, opening over Wi-Fi",
      wait(lambda: next(r for r in json.loads(api.stump_nodes()) if r.get("group") == B_LX)["key"] == "wifi:" + base(47661)),
      lb)
r = json.loads(api.stump_find_wifi(B_LX))
check("Find over Wi-Fi by identity", r.get("key") == "wifi:" + base(47661), r)
r = json.loads(api.stump_find_wifi("d4" * 16))
check("...and says so when it isn't on this Wi-Fi", "error" in r, r)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
