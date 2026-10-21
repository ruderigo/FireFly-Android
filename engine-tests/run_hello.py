"""/hello (Stump 1.0.0-beta.32/33): the greeting as one signed exchange, and the route toggle.

    python3 run_hello.py

  * On connecting: GET /hello, the Stump's signature checked (and that its key
    is the one behind its LXMF address), POST /hello with ours, our name and
    the route; nothing as chat lines, nothing for the person to press.
  * 429 "wait": waited out. A name someone else has: greeted, and it says so.
  * The route toggle (Settings): /hello again with the new "prefer".
  * An impostor (a signature that doesn't check out, or another identity than
    the Stump we know at that address): FireFly doesn't introduce itself.
  * A Stump in a rescue start: its node screen says so (/node.json "mode").
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
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:400]})" if detail and not ok else ""))
    fails += 0 if ok else 1
def wait(cond, secs=20):
    t = time.time()
    while time.time() - t < secs:
        if cond(): return True
        time.sleep(0.2)
    return False

K = "web:127.0.0.1"
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-hello2-"), None, None, L())
c = api._core
api.settings_set(json.dumps({"display_name": "RodRod"}))

rrc = FakeRRC(rns_verify); rrc.hello_gate = True; rrc.enable_hello(); rrc.hello_429 = 1
serve_http(47700, rrc, node={"name": "LaBuche Test", "lxmf": rrc.node_lxmf, "mode": "rescue"})
key = api.stump_connect_wifi("http://127.0.0.1:47700")
api.stump_post(key, "typed right away")
v = lambda: json.loads(api.stump_view(key))
check("greeted with /hello on connecting, through a 429", wait(lambda: rrc.hello(K) == "done"), rrc.hello_log)
posts = [d for k, m, d in rrc.hello_log if m == "POST"]
check("...POST /hello carried our key, signature, name and the route",
      posts and posts[-1]["name"] == "RodRod" and posts[-1]["prefer"] == "wifi"
      and posts[-1]["pub"] == c.identity.get_public_key().hex(), posts[-1:] )
check("...no /auth or /nick chat lines", not any(l.startswith(("/auth", "/nick")) for _, l in rrc.sent_log), rrc.sent_log)
check("...FireFly is verified under its name", wait(lambda: v()["auth"] == "ok" and v().get("nick") == "RodRod"),
      (v()["auth"], v().get("nick")))
check("...the line typed meanwhile went out after it",
      wait(lambda: any("typed right away" in m["body"] for m in rrc.rooms["main"])))

before = len([1 for _, m, _ in rrc.hello_log if m == "POST"])
api.settings_set(json.dumps({"stump_prefer": "lora"}))
check("route toggle: /hello again with prefer lora",
      wait(lambda: rrc.prefer.get(K) == "lora") and len([1 for _, m, _ in rrc.hello_log if m == "POST"]) > before,
      rrc.prefer)
api.settings_set(json.dumps({"display_name": "Rodrigo"}))
check("a new name: /hello again with it", wait(lambda: rrc.users[K]["nick"] == "Rodrigo"), rrc.users[K]["nick"])
check("rescue start: the node screen is told", json.loads(api.stump_radio(key)).get("mode") == "rescue")

# A name someone else has.
rrc2 = FakeRRC(rns_verify); rrc2.hello_gate = True; rrc2.enable_hello()
rrc2.users["alice"]["nick"] = "Rodrigo"
serve_http(47701, rrc2)
k2 = api.stump_connect_wifi("http://127.0.0.1:47701")
v2 = lambda: json.loads(api.stump_view(k2))
check("a name someone else has: greeted anyway, and the room says why",
      wait(lambda: rrc2.hello(K) == "done") and wait(lambda: any("taken" in l["body"] for l in v2()["lines"])),
      [l["body"] for l in v2()["lines"]])

# An impostor.
rrc3 = FakeRRC(rns_verify); rrc3.hello_gate = True; rrc3.enable_hello(); rrc3.hello_forge = True
serve_http(47702, rrc3)
k3 = api.stump_connect_wifi("http://127.0.0.1:47702")
v3 = lambda: json.loads(api.stump_view(k3))
check("a signature that doesn't check out: FireFly doesn't introduce itself",
      wait(lambda: v3()["auth"] == "failed") and not any(m == "POST" for _, m, _ in rrc3.hello_log)
      and "isn't the Stump" in v3()["auth_detail"], (v3()["auth"], v3()["auth_detail"], rrc3.hello_log))

rrc4 = FakeRRC(rns_verify); rrc4.hello_gate = True; rrc4.enable_hello()
serve_http(47703, rrc4)
c.store.upsert_stump_node("wifi:http://127.0.0.1:47703", "wifi", "http://127.0.0.1:47703", lxmf="cd" * 16)
k4 = api.stump_connect_wifi("http://127.0.0.1:47703")
v4 = lambda: json.loads(api.stump_view(k4))
check("another real Stump where we knew ours: no greeting, and it says another Stump answers there",
      wait(lambda: "another Stump answers" in (v4().get("offline_reason") or "")) and not any(m == "POST" for _, m, _ in rrc4.hello_log)
      and v4()["auth"] != "failed", (v4().get("offline_reason"), v4()["auth"], rrc4.hello_log))
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
