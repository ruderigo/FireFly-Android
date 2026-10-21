"""Hybrid access as Stump 1.0.0-beta.21 means it, over Wi-Fi.

    python3 run_hybrid.py

Everyone reads every room and writes in #main; writing anywhere else (other
rooms, private messages) takes /auth. Before Verify, a line in another room
is answered "auth required for that -- send /auth to verify your identity".
FireFly keeps that line, marked "verify first" (not "too fast", not lost),
fails a private message with the reason, and after Verify the same line goes
through.
"""
import json, os, sys, tempfile, time
os.environ["FAKE_STUMP_MODE"] = "hybrid"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
from fake_stump import FakeRRC, rns_verify, serve_http, MESH_ROOM
from firefly import api

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

def wait(cond, secs=20):
    t = time.time()
    while time.time() - t < secs:
        if cond():
            return True
        time.sleep(0.3)
    return False

rrc = FakeRRC(rns_verify)
rrc.no_auth = True          # FireFly verifies itself on connecting (beta.25): refuse that one, to test before Verify
serve_http(47650, rrc)

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-hybrid-"), None, None, L())
key = api.stump_connect_wifi("http://127.0.0.1:47650")
v = lambda room=None: json.loads(api.stump_view(key, room))
check("connected", wait(lambda: v().get("online") == 1))
check("its own /auth on connecting went unanswered: quietly, not 'failed', no '? /auth' line",
      wait(lambda: "web:127.0.0.1" in rrc.auth_seen) and wait(lambda: v()["auth"] == "none", 10)
      and not any("/auth" in l["body"] for l in v()["lines"]), (v()["auth"], [l["body"] for l in v()["lines"]]))
rrc.no_auth = False

api.stump_post(key, "hello main")
check("#main: anyone writes", wait(lambda: any("hello main" in m["body"] for m in rrc.rooms["main"])))

api.stump_join(key, MESH_ROOM)
check(f"#{MESH_ROOM}: anyone reads (joins)", wait(lambda: v()["room"] == MESH_ROOM))
api.stump_post(key, "hello radio room")
mine = lambda: next((l for l in v(MESH_ROOM)["lines"] if l.get("mine") and l["body"] == "hello radio room"), None)
check("...a line there before Verify is kept, marked 'verify first'",
      wait(lambda: mine() is not None) and mine()["state"] == "auth", mine())
check("...not 'too fast', and the node's sentence isn't added as noise",
      not any(l["kind"] == "reply" and l["body"].startswith("auth required") for l in v(MESH_ROOM)["lines"]))
check("...and it didn't reach the room", not any("hello radio room" in m["body"] for m in rrc.rooms[MESH_ROOM]))

api.stump_send_dm(key, "alice", "psst")
dm = lambda: next((d for d in json.loads(api.stump_dm_thread(key, "alice")) if d["body"] == "psst"), None)
check("a private message before Verify fails with the reason",
      wait(lambda: dm() is not None and dm()["state"] == "failed") and "verified" in (dm().get("reason") or ""), dm())

check("Verify", api.stump_auth(key) and wait(lambda: v()["auth"] == "ok", 30), v().get("auth"))
api.stump_post(key, "hello radio room")      # what a tap on the line does
check("after Verify, the same line goes through", wait(lambda: any("hello radio room" in m["body"] for m in rrc.rooms[MESH_ROOM])))

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
