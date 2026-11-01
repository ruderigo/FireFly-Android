"""How a Stump can be reached, every combination (Oct 24 field report).

    python3 run_reach_states.py

Stale billboard/files buttons and a wrong "N/A over LoRa" for a Stump that
was simply down: the screen guessed from pieces. Now the engine answers once
(Core.stump_reach) for all of a Stump's sides together:
  "wifi"     its Wi-Fi side answers (and the phone is on a Wi-Fi): billboard, files
  "lora"     only LoRa reaches it (heard recently, and the radio or a path can carry)
  "no_wifi"  a Wi-Fi-only Stump, and the phone is off Wi-Fi
  "offline"  nothing reaches it
and the Stumps tab's "alive" and its pull-to-refresh use the same answer.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
os.environ["FIREFLY_BROADCAST"] = "127.0.0.1"
from fake_stump import FakeRRC, rns_verify, serve_http
from firefly import api

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1
def wait(cond, secs=15):
    t = time.time()
    while time.time() - t < secs:
        if cond(): return True
        time.sleep(0.3)
    return False

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-reachst-"), None, None, L())
c = api._core
reach = lambda k, w=None, p=False: json.loads(api.stump_reach(k, w, p))

LX = "cd" * 16
rrc = FakeRRC(rns_verify); rrc.node_lxmf = LX
srv = serve_http(47730, rrc)
W = api.stump_connect_wifi("http://127.0.0.1:47730")
wait(lambda: json.loads(api.stump_view(W)).get("online") == 1)

r = reach(W, True)
check("Wi-Fi Stump answering, phone on Wi-Fi: wifi, from that address", r["state"] == "wifi" and r["web"] == W, r)
check("...phone off Wi-Fi, no LoRa side: no_wifi (not 'N/A over LoRa')", reach(W, False)["state"] == "no_wifi")

# The same Stump on LoRa too.
c.store.upsert_stump_node("mesh:" + LX, "mesh", LX, name="LaBuche", last_seen=time.time())
c.can_reach = lambda h: True                     # a radio online
check("...with a LoRa side heard just now, phone off Wi-Fi: lora", reach(W, False)["state"] == "lora")
check("...asked from the LoRa side, phone on Wi-Fi: wifi, from its Wi-Fi side",
      reach("mesh:" + LX, True)["web"] == W)

# The Stump goes down: its Wi-Fi stops answering, and LoRa hasn't been heard for long.
srv.shutdown(); srv.server_close()
c.store.upsert_stump_node("mesh:" + LX, "mesh", LX, last_seen=time.time() - 3600)
c.store.upsert_peer(LX, "lxmf", heard=False)
check("Stump down (probed): offline, not 'N/A over LoRa'", reach("mesh:" + LX, True, True)["state"] == "offline",
      reach("mesh:" + LX, True, True))
check("...and its Wi-Fi side is marked offline by the probe",
      wait(lambda: json.loads(api.stump_view(W)).get("online") == 0, 5))
check("...the Stumps tab says not alive",
      not next(r for r in json.loads(api.stump_nodes(True)) if r["group"] == LX)["alive"])

# LoRa heard again (an announce), Wi-Fi still down: lora.
c.store.upsert_peer(LX, "lxmf")
check("announce heard again, Wi-Fi still down: lora", reach("mesh:" + LX, True, True)["state"] == "lora")
check("...alive on the Stumps tab", next(r for r in json.loads(api.stump_nodes(True)) if r["group"] == LX)["alive"])
c.can_reach = lambda h: False                    # the radio unplugged
check("...radio unplugged: offline (nothing can carry it)", reach("mesh:" + LX, True)["state"] == "offline")

# Pull to refresh: a Stump that's back on its Wi-Fi is found answering again.
rrc2 = FakeRRC(rns_verify); rrc2.node_lxmf = LX
serve_http(47730, rrc2)
check("pull to refresh: the Wi-Fi Stump that came back answers", json.loads(api.stump_refresh_all(True)) >= 1)
check("...and it's alive again", wait(lambda: next(r for r in json.loads(api.stump_nodes(True)) if r["group"] == LX)["alive"], 10))
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
