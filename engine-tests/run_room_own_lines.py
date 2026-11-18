"""Our own lines stay in the room we said them in (Nov 17).

    python3 run_room_own_lines.py

Each room change used to carry our lines of the last 60 seconds into the new room (meant for
a move the Stump makes right after we speak). Hopping rooms with /join, our lines piled up in
whichever room we landed in: "all my messages like one thread". Now our own /join moves
nothing; a move the Stump makes by itself still re-files the lines of the last moments.
The Stump's replies here are the real ones (a beta.82 Stump over LoRa).
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
api.start(tempfile.mkdtemp(prefix="ff-ownlines-"), None, None, L())
c = api._core
LX = "e8" * 16; K = "mesh:" + LX
c._on_stump_beacon(LX, "Bois", "Beta A", [], boot="o1", release="1.0.0-beta.82")
c.store.upsert_stump_node(K, "mesh", LX, name="Bois", last_seen=time.time())
sess = c.session(K)
sess.pipe.send = lambda line, control=False: None
sess.nick = "mesh-b362"
def stump_says(*lines):
    for ln in lines:
        sess._handle(stump.parse_line(ln))
mine = lambda r: [l["body"] for l in json.loads(api.stump_view(K, r))["lines"] if l.get("mine")]

sess.room = "main"
sess.post("hello from main")
sess.post("/join cafe")
stump_says("→ #cafe", "#cafe: mesh-b362", "✓ ~mesh-b362")
check("joined #cafe", sess.room == "cafe", sess.room)
sess.post("hello from cafe")
sess.post("/join main")
stump_says("→ #main", "#main: mesh-b362", "✓ ~mesh-b362")
sess.post("back in main")
check("#main: only what was said there (and the /join typed there)",
      mine("main") == ["hello from main", "/join cafe", "back in main"], mine("main"))
check("#cafe keeps what was said in #cafe", mine("cafe") == ["hello from cafe", "/join main"], mine("cafe"))

# the room button / switch (join()) the same
sess.join("cafe")
stump_says("→ #cafe", "✓ ~mesh-b362")
check("a room button: nothing carried along", "back in main" in mine("main") and "back in main" not in mine("cafe"),
      (mine("main"), mine("cafe")))

# A move the Stump makes by itself, right after we spoke: that line went to the new room.
sess.join_target = None
sess.room = "lora-mesh"
sess.post("hi everyone")
stump_says("→ #main")                       # no /join of ours: e.g. #lora-mesh with no radio
check("a move the Stump makes by itself still re-files the line just sent",
      "hi everyone" in mine("main") and "hi everyone" not in mine("lora-mesh"), (mine("main"), mine("lora-mesh")))

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
