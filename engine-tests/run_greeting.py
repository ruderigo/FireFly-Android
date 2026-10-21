"""The greeting (Stump 1.0.0-beta.31): an app says who it is before it chats over Wi-Fi.

    python3 run_greeting.py

  * On connecting: /auth, then /nick <Your name>, without a tap and before any
    other line; the phone is then listed under its name, and reads and writes.
  * Before it's done the Stump shows nothing ("hello": "required"): FireFly
    keeps what it shows, and a line typed meanwhile waits and then goes out.
  * A Stump that already heard the name on LoRa: /auth alone, no /nick.
  * A new session (the Stump asks again): greeted again, nothing to do.
  * A new name in Settings reaches a Wi-Fi Stump (/nick).
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
rrc = FakeRRC(rns_verify); rrc.node_lxmf = "f1" * 16; rrc.hello_gate = True
serve_http(47690, rrc)
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-hello-"), None, None, L())
c = api._core
api.settings_set(json.dumps({"display_name": "RodRod"}))
key = api.stump_connect_wifi("http://127.0.0.1:47690")
api.stump_post(key, "typed before the greeting")       # right away, while it's still greeting
v = lambda: json.loads(api.stump_view(key))
check("greeted on connecting: verified and named, without a tap",
      wait(lambda: rrc.hello(K) == "done") and rrc.users[K]["nick"] == "RodRod", (rrc.hello(K), rrc.users[K]["nick"]))
lines = [l for k, l in rrc.sent_log if k == K]
first = [l.split(" ")[0] for l in lines[:3]]
check("...the greeting came first: /auth, /auth <key> <sig>, /nick RodRod",
      first == ["/auth", "/auth", "/nick"] and lines[2] == "/nick RodRod", lines[:4])
check("...the line typed meanwhile waited, then went out",
      wait(lambda: any("typed before the greeting" in m["body"] for m in rrc.rooms["main"])), rrc.rooms["main"][-3:])
check("...and no 'hello_required' refusal reached the room",
      not any("Say who you are" in l["body"] for l in v()["lines"]), [l["body"] for l in v()["lines"]])
check("...quietly: no AUTH-OK line", not any(l["body"].startswith("AUTH-") for l in v()["lines"]))
check("...and FireFly shows its name and the room's people", wait(lambda: v().get("nick") == "RodRod" and v().get("users")),
      (v().get("nick"), v().get("users")))

# A new session on the Stump (it forgot us): it asks again, FireFly greets again.
rrc.verified.discard(K); rrc.nick_chosen.discard(K)
check("a new session: greeted again by itself", wait(lambda: rrc.hello(K) == "done", 30), rrc.hello(K))
check("...what FireFly showed stayed in place meanwhile", bool(v().get("users")) and v().get("room") == "main")

# A new name in Settings.
api.settings_set(json.dumps({"display_name": "Rodrigo"}))
check("a new name in Settings reaches the Wi-Fi Stump", wait(lambda: rrc.users[K]["nick"] == "Rodrigo"), rrc.users[K]["nick"])

# A Stump that knows the name from LoRa: /auth alone.
rrc2 = FakeRRC(rns_verify); rrc2.node_lxmf = "f2" * 16; rrc2.hello_gate = True
rrc2.lora_names[K] = "RodCell"
serve_http(47691, rrc2)
k2 = api.stump_connect_wifi("http://127.0.0.1:47691")
check("a Stump that heard the name on LoRa: /auth alone completes it",
      wait(lambda: rrc2.hello(K) == "done") and not any(l.startswith("/nick") for _, l in rrc2.sent_log),
      [l for _, l in rrc2.sent_log])
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
