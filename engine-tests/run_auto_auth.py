"""FireFly on a Stump's Wi-Fi, as Stump 1.0.0-beta.24/25 asks (Oct 19).

    python3 run_auto_auth.py

  * /auth as soon as it's connected over Wi-Fi, without a tap: that's what
    makes the phone one person on the Stump across Wi-Fi and LoRa. Quietly:
    no AUTH-OK line in the room; a Stump that doesn't answer it leaves no
    "failed" behind.
  * "renamed": {"old": "new"} in the poll: a private conversation follows
    the person's new nick, and so does a block.
  * 503 "busy" (Retry-After: 2): wait and ask again; neither "offline" nor a
    lost line.
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
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1
def wait(cond, secs=20):
    t = time.time()
    while time.time() - t < secs:
        if cond(): return True
        time.sleep(0.3)
    return False

rrc = FakeRRC(rns_verify); rrc.node_lxmf = "ee" * 16
serve_http(47680, rrc)
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-autoauth-"), None, None, L())
c = api._core
key = api.stump_connect_wifi("http://127.0.0.1:47680")
v = lambda: json.loads(api.stump_view(key))
check("connected over Wi-Fi: /auth sent without a tap", wait(lambda: "web:127.0.0.1" in rrc.auth_seen))
check("...and verified", wait(lambda: v()["auth"] == "ok") and "web:127.0.0.1" in rrc.verified, v()["auth"])
check("...quietly: no AUTH-OK line in the room", not any(l["body"].startswith("AUTH-") for l in v()["lines"]),
      [l["body"] for l in v()["lines"]])
time.sleep(3)
check("...once per connection", rrc.auth_seen.count("web:127.0.0.1") == 2, rrc.auth_seen)   # /auth, then /auth <key> <sig>

# renamed: rods-cell is now RodRod
c.session(key)._incoming_dm("rods-cell", "from the old nick")
api.stump_block_nick(key, "spammy")
rrc.renamed = {"rods-cell": "RodRod", "spammy": "spammy2"}
threads = lambda: {t["nick"] for t in v()["threads"]}
check("'renamed' in the poll: the conversation follows the new nick",
      wait(lambda: "RodRod" in threads() and "rods-cell" not in threads()), threads())
check("...with its messages", [m["body"] for m in json.loads(api.stump_dm_thread(key, "RodRod"))] == ["from the old nick"])
check("...and a block follows too", "spammy2" in v()["blocked"], v()["blocked"])
c.session(key)._incoming_dm("RodRod", "and from the new one")
check("...new messages land in the same conversation",
      [m["body"] for m in json.loads(api.stump_dm_thread(key, "RodRod"))] == ["from the old nick", "and from the new one"])

# 503 busy
rrc.busy = 3
api.stump_post(key, "through a busy moment")
check("503 busy: the line still arrives", wait(lambda: any("through a busy moment" in m["body"] for m in rrc.rooms["main"]), 20))
check("...and the Stump never showed as offline", v()["online"] == 1, v().get("offline_reason"))
check("...no 'not sent'", not any(l.get("mine") and l.get("state") in ("error", "failed") for l in v()["lines"]))
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
