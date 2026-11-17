"""/auth over LoRa only when it's needed (Nov 13, with the Stump team; Stump 1.0.0-beta.71).

    python3 run_lora_auth.py

Every LXMF message FireFly sends is signed. From Stump 1.0.0-beta.71 a LoRa sender whose
signature checks out counts as verified for everything (room tiers, /invite, /whoami), so
FireFly sends it no /auth over LoRa at all. Up to beta.70, room tiers go by the /auth itself,
which the Stump forgets when it restarts: FireFly doesn't ask again while the Stump runs on the
boot id that accepted it (FireFly restarting included), and asks again once it restarted.
FireFly also announces itself when it first hears a Stump, so its first message checks out.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
from firefly import api, stump

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

check("release_beta: 1.0.0-beta.71 -> 71, a release above every beta, junk -> None",
      stump.release_beta("1.0.0-beta.71") == 71 and stump.release_beta("1.0.0") > 1000
      and stump.release_beta("1.0.0-beta.9") == 9 and stump.release_beta(None) is None
      and stump.release_beta("soon") is None)

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-lauth-"), None, None, L())
c = api._core
c.can_reach = lambda addr: True
# Entering as the app does: again=True (StumpScreens VerifyGate), then reads with False.
gate = lambda k, again=True: json.loads(api.stump_verify(k, again))
announces = []
def _announce():
    announces.append(time.time()); c.last_announce = time.time()
c.announce = _announce

def session(k):
    s = c.session(k)
    s.pipe.send = lambda line, control=False: sent.append(line) if line != "/names" else None   # (entering says "here": run_lora_here)
    return s
def restart_firefly(k):
    c.sessions.pop(k).stop()
    return session(k)
def verify(s):
    s.on_mesh_content("AUTH-CHALLENGE " + "5f" * 16)
    s.on_mesh_content("AUTH-OK " + c.identity.hash.hex())

# ---- a beta.70 Stump: /auth the first time, and again after it restarts
sent = []
LX = "a1" * 16; K = "mesh:" + LX
c.last_announce = 0
c._on_stump_beacon(LX, "Vieux", "1.0.0-beta.70", [], boot="b0071")
time.sleep(0.2)
check("first hearing a Stump: FireFly announces itself", len(announces) == 1, announces)
c._on_stump_beacon(LX, "Vieux", "1.0.0-beta.70", [], boot="b0071")
time.sleep(0.2)
check("...once, not on each beacon", len(announces) == 1, announces)
c.store.upsert_stump_node(K, "mesh", LX, name="Vieux", last_seen=time.time())
s = session(K)
check("beta.70, first time: /auth", gate(K)["state"] == "verifying" and sent == ["/auth"], sent)
verify(s)
check("...verified", gate(K)["state"] == "ok")
check("...under the Stump's boot id", c.store.stump_node(K).get("auth_boot") == "b0071", c.store.stump_node(K).get("auth_boot"))
sent.clear()
s = restart_firefly(K)
check("FireFly restarts, the Stump still on that boot: straight in, no /auth", gate(K)["state"] == "ok" and sent == [], sent)
c._on_stump_beacon(LX, "Vieux", "1.0.0-beta.70", [], boot="b0072")       # it restarted
check("the beta.70 Stump restarts: verified again by itself (its room tiers need the /auth)",
      sent == ["/auth"], sent)
verify(s)
sent.clear()
saved = c.boots.pop(LX)
s = restart_firefly(K)
check("FireFly restarts, the Stump's beacon not heard yet: /auth (can't tell it didn't restart)",
      gate(K)["state"] == "verifying" and sent == ["/auth"], sent)
c.boots[LX] = saved

# ---- a beta.71 Stump: never /auth over LoRa
sent = []
LX2 = "b2" * 16; K2 = "mesh:" + LX2
c.last_announce = 0
c._on_stump_beacon(LX2, "Neuf", "1.0.0-beta.71", [], boot="n0001")
time.sleep(0.2)
check("a beta.71 Stump heard: FireFly announces itself", len(announces) == 2, announces)
c.store.upsert_stump_node(K2, "mesh", LX2, name="Neuf", last_seen=time.time())
s2 = session(K2)
check("beta.71, first time: in at once, no /auth", gate(K2)["state"] == "ok" and sent == [], sent)
c._on_stump_beacon(LX2, "Neuf", "1.0.0-beta.71", [], boot="n0002")
check("...it restarts: still in, nothing sent", gate(K2)["state"] == "ok" and sent == [], sent)
s2 = restart_firefly(K2)
check("...FireFly restarts: still in, nothing sent", gate(K2)["state"] == "ok" and sent == [], sent)
s2.post("/auth")
check("...typing /auth by hand still sends it", "/auth" in sent, sent)

# a real beacon: the version item is "Beta A", the full release is in caps "r"
import RNS.vendor.umsgpack as msgpack
data = msgpack.packb(["stump", "Beta A", "Bois", bytes(16), [], {"r": "1.0.0-beta.72", "b": "z1"}])
check("beacon_release reads caps r", stump.beacon_release(data) == "1.0.0-beta.72", stump.beacon_release(data))
sent = []
LX4 = "d4" * 16; K4 = "mesh:" + LX4
c._on_stump_beacon(LX4, "Bois", "Beta A", [], boot="z1", release="1.0.0-beta.72")
c.store.upsert_stump_node(K4, "mesh", LX4, name="Bois", last_seen=time.time())
session(K4)
check("'Beta A' with caps r 1.0.0-beta.72: no /auth", gate(K4)["state"] == "ok" and sent == [], sent)
c.stumps[LX4] = ("Bois", "Beta A"); c.boots.pop(LX4, None)
restart_firefly(K4)
check("...FireFly restarts, beacon not heard yet: the kept release still counts", gate(K4)["state"] == "ok" and sent == [], sent)
LX5 = "e5" * 16; K5 = "mesh:" + LX5
c._on_stump_beacon(LX5, "Vieille", "Beta A", [], boot="v1", release="1.0.0-beta.70")
c.store.upsert_stump_node(K5, "mesh", LX5, name="Vieille", last_seen=time.time())
session(K5)
check("'Beta A' with caps r 1.0.0-beta.70: /auth", gate(K5)["state"] == "verifying" and sent == ["/auth"], sent)

# Stump 1.0.0-beta.79: its QR code carries its release (&r=), a hint until its beacon is heard
import base64, RNS
from urllib.parse import quote
ident = RNS.Identity()
LX6 = RNS.Destination.hash(ident, "lxmf", "delivery").hex(); K6 = "mesh:" + LX6
key = base64.urlsafe_b64encode(ident.get_public_key()).decode().rstrip("=")
link = f"lxmf://{LX6}?stump={quote('Bûche 3')}&key={key}&r={quote('1.0.0-beta.79')}"
a = stump.parse_address_link(link)
check("the beta.79 link: name, key and release read", a["stump"] == "Bûche 3" and a["key"] == ident.get_public_key()
      and a["release"] == "1.0.0-beta.79", a)
check("an unreadable r= is ignored", stump.parse_address_link(link.replace("r=1.0.0-beta.79", "r=whenever"))["release"] is None)
check("no r= (a Stump before beta.79): no release", stump.parse_address_link(link.split("&r=")[0])["release"] is None)
c.address_add(link, "stump")
check("added from the code: its release kept", c.store.stump_node(K6).get("release") == "1.0.0-beta.79", c.store.stump_node(K6))
sent = []
session(K6)
check("entering before any beacon: no /auth (the code's release)", gate(K6)["state"] == "ok" and sent == [], sent)
c._on_stump_beacon(LX6, "Bûche 3", "Beta A", [], boot="q1", release="1.0.0-beta.70")
check("its beacon has the last word (says beta.70)", c.store.stump_node(K6).get("release") == "1.0.0-beta.70")
restart_firefly(K6)
check("...then /auth as for any beta.70", gate(K6)["state"] == "verifying" and sent == ["/auth"], sent)
c.address_add(link, "stump")
check("scanning the code again doesn't overwrite what the beacon said", c.store.stump_node(K6).get("release") == "1.0.0-beta.70")

# a beta.71 Stump not entered restarts: it stays verified (nothing to redo)
LX3 = "c3" * 16; K3 = "mesh:" + LX3
c.store.upsert_stump_node(K3, "mesh", LX3, name="Souche", last_seen=time.time(), auth="ok")
c._on_stump_beacon(LX3, "Souche", "1.0.0-beta.71", [], boot="s1")
c._on_stump_beacon(LX3, "Souche", "1.0.0-beta.71", [], boot="s2")
check("a beta.71 Stump not entered restarts: still verified", c.store.stump_node(K3)["auth"] == "ok")

# announces stay rare: many Stumps heard at once, one announce
n = len(announces)
c.last_announce = 0
for i in range(5):
    c._on_stump_beacon(f"{i:02x}" * 16, f"S{i}", "1.0.0-beta.71", [], boot="x")
time.sleep(0.3)
check("five new Stumps at once: one announce", len(announces) == n + 1, announces[n:])

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
