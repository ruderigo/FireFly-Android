"""The billboard over LoRa (Stump 1.0.0-beta.38), and other beta.34-38 answers.

    python3 run_board_lora.py

/board, /board <id> and /post go to the Stump's LoRa side; its answers (an LXMF
batch, as the Stump's rrc.board_command writes them) fill FireFly's LoRa Board
tab and don't land in the room. Also: the radio-down ⧗ keeps a line (it isn't
"not sent"), a forged stump.node beacon is ignored, and a bot's lines ("bot":
true) aren't offered as a DM contact.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
import RNS
from firefly import api, stump

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-boardlora-"), None, None, L())
c = api._core
LX = "ef" * 16
K = "mesh:" + LX
c.store.upsert_stump_node(K, "mesh", LX, name="LaBuche", last_seen=time.time())
sess = c.session(K)
sent = []
sess.pipe.send = lambda line, control=False: sent.append(line)
view = lambda: json.loads(api.stump_board_lora_view(K))
room = lambda: [l["body"] for l in json.loads(api.stump_view(K))["lines"]]

api.stump_board_lora(K)
check("/board asked over LoRa", sent[-1] == "/board" and view()["state"] == "waiting", (sent, view()["state"]))
sess.on_mesh_content("#482193 Marché samedi\n#612803 Vélo à vendre")       # the Stump's answer
v = view()
check("its answer is the list: ids and titles", v["state"] == "ok"
      and v["posts"] == [{"id": "482193", "title": "Marché samedi"}, {"id": "612803", "title": "Vélo à vendre"}], v)
check("...and isn't drawn in the room", not any("Marché" in b for b in room()), room())

api.stump_board_lora(K, "  velo  ")
check("/board <words>", sent[-1] == "/board velo")
sess.on_mesh_content('nothing on the billboard about "velo"')
check("...nothing about them: empty", view()["state"] == "empty")

api.stump_board_lora_item(K, "612803")
check("/board <id>", sent[-1] == "/board 612803")
sess.on_mesh_content("#612803 Vélo à vendre\nBon état.\nAppeler le soir.")
it = view()["items"].get("612803")
check("...its title and body (line breaks kept)", it and it["title"] == "Vélo à vendre" and it["body"] == "Bon état.\nAppeler le soir.", it)
api.stump_board_lora_item(K, "111111")
sess.on_mesh_content("no notice #111111 (expired?)")
check("...an expired one: gone", view()["items"].get("111111", {}).get("gone") is True, view()["items"])

check("/post with a body", api.stump_board_lora_post(K, "Soupe  ce soir", "18 h\nchez Rod") == "true"
      and sent[-1] == "/post Soupe ce soir | 18 h\nchez Rod", sent[-1])
sess.on_mesh_content("✓ #700512")
check("...posted: ✓ #id", view()["post"] == {"state": "ok", "id": "700512", "title": "Soupe ce soir"}, view()["post"])
api.stump_board_lora_post(K, "Encore")
sess.on_mesh_content("⧗ — posting too fast, wait a minute")
check("...refused: the Stump's reason", view()["post"]["state"] == "refused" and "too fast" in view()["post"]["message"], view()["post"])
check("an empty title isn't sent", api.stump_board_lora_post(K, "   ") == "false")

# A batch that isn't the answer (a room line arriving meanwhile) isn't taken for it.
api.stump_board_lora(K)
sess.on_mesh_content("<alice> hello there", title="#main")
check("a room line meanwhile stays a room line", view()["state"] == "waiting" and any("hello there" in l["body"] for l in json.loads(api.stump_view(K, "main"))["lines"]))
sess.on_mesh_content("no billboard on this Stump")
check("billboard switched off: off", view()["state"] == "off")

# The field case (Oct 24): a long post went out slowly; meanwhile the list was asked again and
# a notice opened. The answers come back late and out of order: each goes where it belongs,
# none into the room.
sess.board_asked = {"list": None, "items": {}, "post": None}
api.stump_board_lora_post(K, "8011001", "6000" + "10" * 250)
api.stump_board_lora(K)
api.stump_board_lora_item(K, "568944")
for k in ("list", "post"):
    sess.board_asked[k] = (sess.board_asked[k][0], time.time() - 400)        # 6+ min ago: late
sess.board_asked["items"]["568944"] = time.time() - 400
check("after 2 min the tab says 'no answer yet'", view()["state"] == "no_answer" and view()["post"]["state"] == "no_answer")
before = len(room())
sess.on_mesh_content("#568944 8011001\n6000" + "10" * 250)                 # the notice's answer first
sess.on_mesh_content("\u2713 #568944")                                       # then the post's
sess.on_mesh_content("#568944 8011001\n#482193 Marché samedi")               # then the list's
v = view()
check("late, out of order: the notice's text is the notice's", v["items"].get("568944", {}).get("body", "").startswith("6000"), v["items"].get("568944"))
check("...the ✓ is the post's", v["post"].get("state") == "ok" and v["post"].get("id") == "568944", v["post"])
check("...the titles are the list's", v["state"] == "ok" and [p["id"] for p in v["posts"]] == ["568944", "482193"], v)
check("...and nothing of it lands in the room", len(room()) == before, room()[before:])
check("...nothing left waiting", sess.board_pending is None and view()["items_waiting"] == [])

# The list arriving while a notice is awaited, and starting with that notice: still the list.
api.stump_board_lora(K)
api.stump_board_lora_item(K, "900001")
sess.on_mesh_content("#900001 Pain frais\n#482193 Marché samedi")
check("the list first, though it starts with an awaited notice: the list",
      [p["id"] for p in view()["posts"]] == ["900001", "482193"] and "900001" in view()["items_waiting"], view())
sess.on_mesh_content("#900001 Pain frais\nDès 7 h.")
check("...then the notice's own answer: the notice", view()["items"].get("900001", {}).get("body") == "Dès 7 h.")

# The room's buttons wait while a /join or /rooms is on its way (Oct 24).
st = lambda: json.loads(api.stump_view(K))
api.stump_command(K, "/rooms")
check("/rooms sent: refreshing", st()["refreshing"] is True)
sess.on_mesh_content("#main ·3 General\n#lora-mesh ·1 LoRa")
check("...its answer in: not any more", st()["refreshing"] is False)
api.stump_join(K, "main")
check("/join sent: joining #main", st()["joining"] == "main", st().get("joining"))
sess.on_mesh_content("→ #main")
check("...moved there: not joining any more", st()["joining"] is None and st()["room"] == "main", (st().get("joining"), st()["room"]))
api.stump_join(K, "vip")
sess.on_mesh_content("⊘ #vip minted — verified only")
check("...refused: not joining either", st()["joining"] is None)

# Radio down: the line is kept, not 'not sent'.
sess.post("for the radio room")
sess.on_mesh_content("⧗ — the radio has been down for 4 min: your message will go out when it's back (kept up to an hour)")
lines = json.loads(api.stump_view(K))["lines"]
mine = [l for l in lines if l.get("mine") and l["body"] == "for the radio room"]
check("radio down: the line isn't marked not sent", mine and mine[-1].get("local_state") != "failed", mine)
check("...and the Stump's note is shown", any("radio has been down" in l["body"] for l in lines))

# A forged beacon: another identity naming this Stump's LXMF address.
me, other = RNS.Identity(), RNS.Identity()
own = RNS.Destination.hash(me, "lxmf", "delivery").hex()
check("a beacon naming its own LXMF address is taken", stump.beacon_is_own(own, me))
check("...one naming someone else's is not (forged)", not stump.beacon_is_own(own, other))

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
