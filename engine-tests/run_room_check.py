"""Entering after a while: the room is checked first (Nov 21).

    python3 run_room_check.py

Opening a Stump not heard from for a while showed the room FireFly last saw until the Stump's
answer moved it (seconds over LoRa): the wrong room, for a moment. Now, entering after a while,
the room view says "checking" until the Stump answers which room it has us in, then shows that
room; never longer than 30 s over LoRa or 6 s over Wi-Fi.
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
api.start(tempfile.mkdtemp(prefix="ff-roomcheck-"), None, None, L())
c = api._core
LX = "d9" * 16; K = "mesh:" + LX
c._on_stump_beacon(LX, "Bois", "Beta A", [], boot="r1", release="1.0.0-beta.86")
c.store.upsert_stump_node(K, "mesh", LX, name="Bois", last_seen=time.time())
sess = c.session(K)
sent = []
def spy(line, control=False):
    sent.append(line); sess.pipe.sent_at = time.time()
sess.pipe.send = spy
enter = lambda: json.loads(api.stump_verify(K, True))
view = lambda: json.loads(api.stump_view(K))
sess.room = "main"

# ---- LoRa
enter()
check("LoRa, entering after a while: the room is being checked (the /names on entering)",
      view().get("checking_room") is True and sent == ["/names"], (view().get("checking_room"), sent))
sess._handle(stump.parse_line("#lora-mesh: carol"))
v = view()
check("...the Stump's answer: checked, and the room it has us in is shown", v.get("checking_room") is False
      and v.get("room") == "lora-mesh", (v.get("checking_room"), v.get("room")))
enter()
check("entering again soon after: nothing to check, the room straight away", view().get("checking_room") is False)
sess.pipe.sent_at = time.time() - 5 * 60
enter()
sess.here_at -= sess.CHECK_LORA_S + 1                    # no answer at all
check("...no answer within 30 s: the room is shown anyway", view().get("checking_room") is False)

# ---- Wi-Fi (the same session as if over Wi-Fi: its kind, its poll)
sess.here_room = None
sess.pipe.kind = "wifi"
sess.pipe.wake = __import__("threading").Event()
sess.hello = "done"
sess.polled_at = time.time() - 60                        # no poll answer for a minute (backed off)
enter()
check("Wi-Fi, entering after a while: checked, and the poll asked now (not at its next turn)",
      view().get("checking_room") is True and sess.pipe.wake.is_set())
sess.on_http_poll({"room": "main", "users": ["frank"], "messages": []})
v = view()
check("...the poll's answer: checked, its room shown", v.get("checking_room") is False and v.get("room") == "main",
      (v.get("checking_room"), v.get("room")))
sess.pipe.wake.clear()
enter()
check("entering with a poll answer seconds old: the room straight away", view().get("checking_room") is False
      and not sess.pipe.wake.is_set())
sess.polled_at = time.time() - 60
enter()
sess.entered_at -= sess.CHECK_WIFI_S + 1                 # the poll never answers
check("...no poll answer within 6 s: the room is shown anyway", view().get("checking_room") is False)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
