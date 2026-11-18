"""Entering a Stump over LoRa says "I'm here" (Nov 17).

    python3 run_lora_here.py

A Stump keeps a LoRa client in its room 5 minutes after its last message (rrc_mesh
PEER_TIMEOUT), then stops sending it the room. The /auth sent on entering used to renew that;
with no /auth (Stump beta.71+) entering sent nothing, so after a quiet spell the room stayed
empty and commands seemed dead (build 77). Now entering sends /names when FireFly's been quiet
4 minutes or more: one message, and the full list. If the Stump had let it go (back in
#lora-mesh), FireFly shows the room the Stump has it in.
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

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-here-"), None, None, L())
c = api._core
LX = "f7" * 16; K = "mesh:" + LX
c._on_stump_beacon(LX, "Bois", "Beta A", [], boot="h1", release="1.0.0-beta.77")
c.store.upsert_stump_node(K, "mesh", LX, name="Bois", last_seen=time.time())
sess = c.session(K)
sent = []
real = sess.pipe.send
def spy(line, control=False):
    sent.append(line); sess.pipe.sent_at = time.time()
sess.pipe.send = spy
enter = lambda: json.loads(api.stump_verify(K, True))
sess.room = "main"

g = enter()
check("entering a beta.77 Stump over LoRa: in at once, no /auth", g["state"] == "ok" and "/auth" not in sent, (g, sent))
check("...and /names: the Stump has us in the room again (one message)", sent == ["/names"], sent)
sess._handle(stump.parse_line("#main: @alice, bob@parc"))
check("...its answer finds us in #main: nothing else sent", sent == ["/names"], sent)

sent.clear()
enter()
check("entering again within 4 minutes: nothing sent (still present)", sent == [], sent)

sess.pipe.sent_at = time.time() - 5 * 60
enter()
check("entering after 5 quiet minutes: /names again", sent == ["/names"], sent)
sess._handle(stump.parse_line("#lora-mesh: carol, dave@gare"))
check("...the Stump had let us go (back in #lora-mesh): FireFly follows it there, nothing sent",
      sess.room == "lora-mesh" and sent == ["/names"], (sess.room, sent))
check("...and says so", any("#lora-mesh" in l["body"] for l in json.loads(api.stump_view(K, "lora-mesh"))["lines"] if l["kind"] == "system"))
sess._handle(stump.parse_line("#main: @alice"))
check("...a /names answer not asked on entering moves nothing", sess.room == "lora-mesh", sess.room)

row = next(r for r in json.loads(api.stump_nodes()) if r.get("group") == LX)
check("a Stump reached over LoRa only: no /admin address (the menu shows it greyed, Wi-Fi only)",
      row.get("admin") is None, row.get("admin"))

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
