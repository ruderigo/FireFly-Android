"""Entering a Stump: FireFly proves who it is first, over Wi-Fi and over LoRa (Oct 27).

    python3 run_verify_gate.py

The room waits behind a "verifying…" popup until the Stump has FireFly's
signature. stump_verify(key) starts it if need be and says where it is:
  * over Wi-Fi it's already under way when the Stump appears (/hello): ok;
  * over LoRa, entering sends /auth (it never did by itself before), answers
    the challenge, and is ok on AUTH-OK; entering again doesn't ask again;
  * a refusal, no answer, no radio or an unreachable Wi-Fi Stump: failed, with
    the reason, and Try again starts over;
  * a Stump without /auth: nothing to prove, the room opens;
  * a Stump that restarts (a new boot id in its beacon, Stump 1.0.0-beta.43)
    is verified again by itself; unknown caps keys are ignored.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
from fake_stump import FakeRRC, rns_verify, serve_http
from firefly import api, stump

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

def wait(cond, secs=15):
    t = time.time()
    while time.time() - t < secs:
        if cond():
            return True
        time.sleep(0.2)
    return False

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-verify-"), None, None, L())
c = api._core
gate = lambda k, again=False: json.loads(api.stump_verify(k, again))

# --- Wi-Fi: under way as soon as the Stump appears
rrc = FakeRRC(rns_verify); rrc.node_lxmf = "ab" * 16
rrc.enable_hello()
serve_http(47690, rrc)
W = api.stump_connect_wifi("http://127.0.0.1:47690")
check("Wi-Fi: verified by itself (/hello), the room opens", wait(lambda: gate(W)["state"] == "ok"), gate(W))
check("...and the view carries it", json.loads(api.stump_view(W))["verify"]["state"] == "ok")
n = len(rrc.hello_log)
gate(W); time.sleep(1)
check("...entering again doesn't greet again", len(rrc.hello_log) == n, rrc.hello_log[n:])

DEAD = api.stump_connect_wifi("http://127.0.0.1:47691")       # nothing listens there
gate(DEAD, True)                                              # entering it
check("Wi-Fi, nothing answers: failed, with the reason", wait(lambda: gate(DEAD)["state"] == "failed")
      and gate(DEAD)["detail"], gate(DEAD))

# --- LoRa
LX = "cd" * 16
K = "mesh:" + LX
c.store.upsert_stump_node(K, "mesh", LX, name="LaBuche", last_seen=time.time())
reach = {"ok": True}
c.can_reach = lambda addr: reach["ok"]
sess = c.session(K)
sent = []
sess.pipe.send = lambda line, control=False: sent.append(line)
time.sleep(0.5)
check("LoRa: nothing sent until the Stump is entered (no airtime for Stumps only heard)", sent == [], sent)
g = gate(K)
check("entering: /auth goes out, verifying", sent == ["/auth"] and g["state"] == "verifying" and g["transport"] == "mesh", (sent, g))
gate(K)
check("...asking where it is doesn't send it twice", sent == ["/auth"], sent)
sess.on_mesh_content("AUTH-CHALLENGE " + "5f" * 16)
check("...the challenge is answered", len(sent) == 2 and sent[1].startswith("/auth ") and len(sent[1].split()) == 3, sent)
sess.on_mesh_content("AUTH-OK " + c.identity.hash.hex())
check("...AUTH-OK: verified, the room opens", gate(K)["state"] == "ok", gate(K))
check("...quietly: no AUTH-OK line in the room",
      not any(l["body"].startswith("AUTH-") for l in json.loads(api.stump_view(K))["lines"]))
gate(K)
check("...entering again doesn't ask again", len(sent) == 2, sent)

# a refusal, then Try again
sess.verify = {"state": "idle", "detail": "", "at": 0}
sent.clear()
gate(K)
sess.on_mesh_content("AUTH-CHALLENGE " + "6a" * 16)
sess.on_mesh_content("AUTH-FAIL signature does not match")
g = gate(K)
check("refused: failed, with the Stump's reason", g["state"] == "failed" and "signature" in g["detail"], g)
gate(K); time.sleep(5.5); gate(K)
check("...not asked again by itself, however long the popup asks", sent.count("/auth") == 1, sent)
check("...Try again starts over", gate(K, True)["state"] == "verifying" and sent.count("/auth") == 2, sent)

# no answer
sess.auth.started -= stump.AUTH_WINDOW_S + 31
g = gate(K)
check("no answer over LoRa: failed, says so", g["state"] == "failed" and "no answer" in g["detail"], g)

# a Stump without /auth
sent.clear()
gate(K, True)
sess.on_mesh_content("? /auth")
check("a Stump without /auth: nothing to prove, the room opens", gate(K)["state"] == "not_needed", gate(K))

# A Stump restarts (Stump 1.0.0-beta.43: a new boot id, caps["b"], in its beacon). It forgot
# who proved who they are: FireFly drops "verified" and, being in use, verifies again quietly.
import RNS.vendor.umsgpack as mp
def beacon(boot=None, extra=None):
    caps = {"m": "hybrid", "rad": True, "zz_future": [1, 2]}       # an unknown key: ignored
    if boot is not None:
        caps["b"] = boot
    caps.update(extra or {})
    return mp.packb(["stump", "1.0.0-beta.43", "LaBuche", bytes.fromhex(LX), ["http://10.0.0.5"], caps])
check("the beacon's boot id is read", stump.beacon_boot(beacon("a1b2c3")) == "a1b2c3")
check("...absent on an older Stump: None, nothing else changes",
      stump.beacon_boot(beacon()) is None and stump.parse_beacon(beacon())[0] == LX
      and stump.beacon_http(beacon()) == ["http://10.0.0.5"])
check("...a five-item beacon (beta.21-42): None",
      stump.beacon_boot(mp.packb(["stump", "1.0.0-beta.42", "LaBuche", bytes.fromhex(LX), []])) is None)
check("...caps that aren't a dict, or a b that isn't a string: None",
      stump.beacon_boot(mp.packb(["stump", "x", "n", bytes.fromhex(LX), [], [1]])) is None
      and stump.beacon_boot(beacon(12345)) is None)

sent.clear()
sess.verify = {"state": "idle", "detail": "", "at": 0}
gate(K)
sess.on_mesh_content("AUTH-CHALLENGE " + "7b" * 16)
sess.on_mesh_content("AUTH-OK " + c.identity.hash.hex())
check("(verified again)", gate(K)["state"] == "ok")
sent.clear()
c._on_stump_beacon(LX, "LaBuche", "1.0.0-beta.43", ["http://10.0.0.5"], boot="a1b2c3")
c._on_stump_beacon(LX, "LaBuche", "1.0.0-beta.43", ["http://10.0.0.5"], boot="a1b2c3")
check("the same boot id again: nothing happens", sent == [] and gate(K)["state"] == "ok", (sent, gate(K)))
c._on_stump_beacon(LX, "LaBuche", "1.0.0-beta.43", ["http://10.0.0.5"], boot="d4e5f6")
check("a new boot id: it restarted, FireFly verifies again by itself",
      sent == ["/auth"] and sess.verify["state"] == "verifying", (sent, sess.verify))
check("...not shown 'verified' meanwhile", json.loads(api.stump_view(K))["auth"] != "ok")
check("...and the view says when it restarted", json.loads(api.stump_view(K))["restarted_at"])
sess.on_mesh_content("AUTH-CHALLENGE " + "8c" * 16)
sess.on_mesh_content("AUTH-OK " + c.identity.hash.hex())
check("...verified again, quietly", gate(K)["state"] == "ok"
      and not any(l["body"].startswith("AUTH-") for l in json.loads(api.stump_view(K))["lines"]))
c._on_stump_beacon(LX, "LaBuche", "1.0.0-beta.42", ["http://10.0.0.5"], boot=None)
check("a beacon without a boot id (downgrade, older relay) changes nothing", gate(K)["state"] == "ok" and len(sent) == 2, sent)

# A Stump restarting that FireFly hasn't entered this run: only "verified" goes, no airtime.
LX3 = "cf" * 16
K3 = "mesh:" + LX3
c.store.upsert_stump_node(K3, "mesh", LX3, name="Racine", last_seen=time.time(), auth="ok")
c._on_stump_beacon(LX3, "Racine", "1.0.0-beta.43", [], boot="111111")
c._on_stump_beacon(LX3, "Racine", "1.0.0-beta.43", [], boot="222222")
check("a Stump not entered restarts: no longer 'verified', and nothing sent",
      c.store.stump_node(K3)["auth"] == "none" and K3 not in c.sessions, c.store.stump_node(K3).get("auth"))

# no radio
LX2 = "ce" * 16
K2 = "mesh:" + LX2
c.store.upsert_stump_node(K2, "mesh", LX2, name="Souche", last_seen=time.time())
reach["ok"] = False
g = gate(K2)
check("no radio: failed at once, with why", g["state"] == "failed" and "no radio" in g["detail"], g)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
