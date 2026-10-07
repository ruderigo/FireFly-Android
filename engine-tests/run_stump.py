"""Stump chat, the same session code over both pipes.

Mesh: a fake Stump node in its own process, reached by LXMF over TCP
(standing in for LoRa). Wi-Fi: the same chat logic behind /rrc/poll and
/rrc/send. Covers the quickstart's checklist: beacon detection, /auth with a
real signature (one packet on the mesh), rooms and landing room, DMs pending
until the node's [DM] echo, ⊖ for nobody, incoming DMs threaded by sender.

    python3 engine-tests/run_stump.py
"""
import json, os, subprocess, sys, tempfile, time, shutil
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python")); sys.path.insert(0, here)
from firefly.core import Core
from firefly import api
from fake_stump import FakeRRC, rns_verify, serve_http

ok = True
def check(c, what):
    global ok
    print(("  PASS " if c else "  FAIL ") + what); ok = ok and bool(c)

def wait(cond, secs, what):
    t = time.time()
    while time.time() - t < secs:
        try:
            if cond():
                print(f"  PASS {what} ({time.time()-t:.1f}s)"); return True
        except Exception:
            pass
        time.sleep(0.2)
    check(False, what); return False

tmp = tempfile.mkdtemp(prefix="ff-stump-")
node = subprocess.Popen([sys.executable, os.path.join(here, "fake_stump.py"), os.path.join(tmp, "node"), "47520"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        env=dict(os.environ, FAKE_PEER_TIMEOUT="20"))
line = node.stdout.readline()
while "NODE ready" not in line:
    if not line and node.poll() is not None:
        sys.exit("the fake node didn't start (is port 47520 still in use?)")
    line = node.stdout.readline()
node_hex = line.split()[2]

events = []
class L:
    def onEvent(self, s): events.append(json.loads(s))

core = Core(os.path.join(tmp, "me"), listener=L(), log=lambda *a: None)
core.settings.update({"auto_interface": False, "tcp_peers": ["127.0.0.1:47520"], "display_name": "Rod Phone",
                      "log_level": 2, "radio": {"enabled": False}})
core.start()
api._core = core

print("-- over the mesh (LXMF)")
wait(lambda: core.is_stump(node_hex), 30, "recognised the Stump from its stump.node beacon")
key = f"mesh:{node_hex}"
wait(lambda: any(n["key"] == key for n in json.loads(api.stump_nodes())), 5, "listed under Stump nodes")
check(not any(c["peer"] == node_hex for c in json.loads(api.conversations())), "not mixed into plain chats")
api.stump_post(key, "bonjour la salle")
v = lambda: json.loads(api.stump_view(key))
wait(lambda: any(l["mine"] and l["state"] == "delivered" for l in v()["lines"]), 30,
     "own room line drawn at once, then marked delivered to the node")
wait(lambda: any(l["nick"] == "alice" and "welcome" in l["body"] for l in v()["lines"]), 30,
     "room traffic pushed by the node lands in #lxmf")
check(v()["room"] == "lxmf", "mesh users land in #lxmf")
wait(lambda: any(t["nick"] == "alice" and t["unread"] for t in v()["threads"]), 30, "alice's DM threaded, unread")
check(any(e["type"] == "stump_dm" and e["nick"] == "alice" for e in events), "DM notification event")
api.stump_send_dm(key, "alice", "merci alice")
dm_state = lambda nick: [d for d in json.loads(api.stump_dm_thread(key, nick)) if d["outgoing"]][-1]
check(dm_state("alice")["state"] == "pending", "sent DM shows pending first")
wait(lambda: dm_state("alice")["state"] == "delivered", 30, "DM confirmed by the node's [DM] <me> echo")
check(v()["nick"] == "Rod-Phone", f"learned exact nick from the echo: {v()['nick']}")
api.stump_send_dm(key, "ghost", "anyone?")
wait(lambda: dm_state("ghost")["state"] == "failed", 30, "DM to nobody: ⊖ marks it failed")
print("      reason:", dm_state("ghost")["reason"])
print("-- labels and limits, as the node computes them")
from firefly.audio import label, valid_voice
check([label(x) for x in (2250, 2249, 1950, 15050)] == ["♪ 2.3 s", "♪ 2.2 s", "♪ 2.0 s", "♪ 15.1 s"],
      "♪ tenths rounded half up: 2.25 -> 2.3, 2.249 -> 2.2, 1.95 -> 2.0")
check(not valid_voice(4, b"x" * 6 * 376) and valid_voice(4, b"x" * 6 * 375), "Codec 2: 15,000 ms allowed, 15,040 refused")
print("-- which codec for a voice note (Automatic)")
check(core.voice_mode_for(node_hex) == 16, "Automatic: Opus for a peer reached over TCP, not LoRa")
check(api.voice_mode_for_stump(key) == 16, "Stump voice DMs: Opus by default, like every chat")
core.settings.update({"voice_mode": 9})
check(api.voice_mode_for_stump(key) == 9, "...a chosen Codec 2 mode is used")
core.settings.update({"voice_mode": 0})
check(api.voice_mode_for_stump("wifi:http://x") == 16, "...Automatic: Opus to a node over Wi-Fi")
check(core.voice_mode_for("ab" * 16) == 4, "a peer with no path and no propagation node: Codec 2")

print("-- voice DMs over the mesh")
speech = open(os.path.join(here, "kristoff_1200.c2"), "rb").read()      # 5.0 s
vid = api.stump_send_voice(key, "alice", 4, speech)
vrow = lambda k, nick, i: next(d for d in json.loads(api.stump_dm_thread(k, nick)) if d["id"] == i)
check(vrow(key, "alice", vid)["state"] == "pending" and vrow(key, "alice", vid)["body"] == "♪ 5.0 s",
      "voice DM shows pending, labelled like the node does (♪ 5.0 s)")
wait(lambda: vrow(key, "alice", vid)["state"] == "delivered", 60, "confirmed by [DM] <me>: ♪ 5.0 s")
# (sent only after the first is confirmed: on LoRa a short one-packet note can overtake a long one)
fast = api.stump_send_voice(key, "alice", 4, speech[:300])               # 2.0 s, within 5 s: too fast
wait(lambda: vrow(key, "alice", fast)["state"] == "failed", 60, "a second note within 5 s: ⧗ fails it")
print("      reason:", vrow(key, "alice", fast)["reason"])
check(api.stump_dm_audio(vid) == speech, "our own note stays playable on our side")
core.settings.update({"voice_mode": 16})
opus_note = open(os.path.join(here, "kristoff_opus.ogg"), "rb").read()
time.sleep(5.5)                                                       # the node's one-per-5-s limit
ov = api.stump_send_voice(key, "alice", 16, opus_note)
api.stump_command(key, "/help")          # a sentence right after: must not be taken for a refusal
check(vrow(key, "alice", ov)["body"] == "♪ 5.0 s", "Opus voice DM labelled from its Ogg pages: ♪ 5.0 s")
wait(lambda: vrow(key, "alice", ov)["state"] == "delivered", 90, "Opus voice DM over the mesh, ~3.4 KB: confirmed")
inc_opus = lambda k: [d for d in json.loads(api.stump_dm_thread(k, "alice")) if not d["outgoing"] and d.get("audio_mode") == 16]
wait(lambda: inc_opus(key), 90, "alice's Opus reply arrives with its audio field")
check(api.stump_dm_audio(inc_opus(key)[0]["id"]) == opus_note and inc_opus(key)[0]["audio_playable"]
      and inc_opus(key)[0]["audio_ms"] == 5000, "...5.0 s, playable, byte for byte")
inc = lambda k: [d for d in json.loads(api.stump_dm_thread(k, "alice")) if not d["outgoing"] and d.get("audio_mode")]
wait(lambda: inc(key), 60, "alice's voice reply arrives with its audio field")
check(inc(key)[0]["audio_ms"] == 5000 and inc(key)[0]["audio_playable"] and len(api.stump_dm_audio(inc(key)[0]["id"])) == 750,
      "...5.0 s, playable, 750 bytes of Codec 2")
try:
    api.stump_send_voice(key, "alice", 4, speech[:30]); check(False, "a 0.2 s note is refused before sending")
except ValueError:
    check(True, "a 0.2 s note is refused before sending")

api.stump_command(key, "/rooms")
wait(lambda: any(r["name"] == "lounge" and r["tier"] == "hybrid" for r in v()["rooms"]), 30,
     "/rooms parsed with tiers")
api.stump_join(key, "lounge")
wait(lambda: v()["room"] == "lounge", 30, "/join moved us (room from the node's → reply)")
lx_lines = lambda: [l["body"] for l in json.loads(api.stump_view(key, "lxmf"))["lines"]]
lo_lines = lambda: [l["body"] for l in json.loads(api.stump_view(key, "lounge"))["lines"]]
wait(lambda: "bye from #lxmf" in lx_lines(), 30, "a line sent in #lxmf just as we left arrives after the move...")
check("bye from #lxmf" not in lo_lines(), "...and is filed in #lxmf by its batch title, not in #lounge")
api.stump_join(key, "vip")
if not wait(lambda: any(l["body"].startswith("⊘ #vip minted") for l in v()["lines"]), 30, "/join vip unverified: ⊘ #vip minted"):
    print("      DEBUG room", v()["room"], [l["body"][:50] for l in v()["lines"]][-6:])
    for m in core.store.messages(node_hex)[-6:]: print("      DEBUG raw", m["outgoing"], repr(m["content"][:70]), repr(m.get("title")))
check(v()["room"] == "lounge", "...and we stay in #lounge (no false move)")
api.stump_command(key, "/frob")
wait(lambda: any(l["body"].startswith("? /frob") for l in v()["lines"]), 30, "? /frob: unknown command shown")
check(api.stump_auth(key), "/auth started")
wait(lambda: v()["auth"] == "ok", 45, "/auth over the mesh -> AUTH-OK (one packet, real signature)")
check(v()["auth_detail"] == core.identity.hash.hex(), "node's identity hash equals ours")
api.stump_join(key, "vip")
wait(lambda: v()["room"] == "vip", 30, "verified: /join vip now moves us")

print("-- going quiet on the mesh (the node's MESH_PEER_TIMEOUT)")
api.stump_join(key, "main")
wait(lambda: v()["room"] == "main", 30, "joined #main")
time.sleep(22)                                   # longer than the node's timeout (20 s here)
api.stump_post(key, "anyone still here?")
wait(lambda: v()["room"] == "lxmf", 30, "node put us back in #lxmf: seen from our own ✓, no → line")
lx = lambda: json.loads(api.stump_view(key, "lxmf"))["lines"]
main_lines = [l["body"] for l in json.loads(api.stump_view(key, "main"))["lines"]]
after_move = main_lines[len(main_lines) - 1 - main_lines[::-1].index("→ #main"):]
check("✓ ~Rod-Phone" not in after_move, "the re-landing ✓ is shown in #lxmf, not in #main")
check(any(l["mine"] and l["body"] == "anyone still here?" for l in lx()),
      "the message sent at that moment is filed in #lxmf, where it landed")
sess = core.sessions[key]
sess.room = "main"; sess._save(room="main")      # simulate a stale belief
api.stump_post(key, "/j lxmf")
wait(lambda: v()["room"] == "lxmf", 30, "'tu es déjà dans #lxmf' corrects a stale room")

print("-- over Wi-Fi (HTTP)")
rrc = FakeRRC(rns_verify)
srv = serve_http(47530, rrc)
wkey = api.stump_connect_wifi("127.0.0.1:47530")
check(wkey == "wifi:http://127.0.0.1:47530", f"session key {wkey}")
w = lambda: json.loads(api.stump_view(wkey))
wait(lambda: w()["online"] and w()["nick"] == "guest-a1b2", 15, "polled: online, nick from the server")
check(w()["room"] == "main", "Wi-Fi visitors are in #main")
check(w()["name"] == "LaBuche Test", "the Wi-Fi entry takes the node's own name from the poll's node field")
api.stump_post(wkey, "hello from wifi")
wait(lambda: any(l["body"] == "hello from wifi" and l["mine"] for l in w()["lines"]), 15,
     "own line arrives via the poll (not drawn twice)")
check(sum(1 for l in w()["lines"] if l["body"] == "hello from wifi") == 1, "exactly once")
wait(lambda: any(t["nick"] == "alice" for t in w()["threads"]), 15, "incoming DM from the poll's dms list")
api.stump_send_dm(wkey, "alice", "hi alice")
wdm = lambda nick: [d for d in json.loads(api.stump_dm_thread(wkey, nick)) if d["outgoing"]][-1]
wait(lambda: wdm("alice")["state"] == "delivered", 15, "DM confirmed by the /rrc/send reply")
api.stump_send_dm(wkey, "nobody", "x")
wait(lambda: wdm("nobody")["state"] == "failed", 15, "⊖ reply fails the DM")
print("-- voice DMs over Wi-Fi")
wv = api.stump_send_voice(wkey, "alice", 4, speech)
wait(lambda: vrow(wkey, "alice", wv)["state"] == "delivered", 30, "voice DM over /rrc/voice: confirmed")
wait(lambda: inc(wkey), 30, "alice's voice reply: in the poll, frames fetched from /rrc/voice?id=")
check(len(api.stump_dm_audio(inc(wkey)[0]["id"])) == 750 and inc(wkey)[0]["audio_playable"], "...playable, 750 bytes")
time.sleep(5.5)
wo = api.stump_send_voice(wkey, "alice", 16, opus_note)
wait(lambda: vrow(wkey, "alice", wo)["state"] == "delivered", 30, "Opus voice DM over /rrc/voice: confirmed")
wait(lambda: inc_opus(wkey), 30, "alice's Opus reply: fetched from /rrc/voice?id=, playable")
time.sleep(5.5)
api.stump_join(wkey, "lounge")
wait(lambda: w()["room"] == "lounge", 15, "/join: `room` field in the reply moves us")
api.stump_auth(wkey)
wait(lambda: w()["auth"] == "ok", 15, "/auth over HTTP -> AUTH-OK, same code path")
check(w()["auth_detail"] == core.identity.hash.hex(), "same identity on both transports")
print("-- hide and show again")
keys = lambda: [n["key"] for n in json.loads(api.stump_nodes())]
mesh_lines = len(core.store.lines(key, v()["room"]))
wifi_lines = len(w()["lines"])
pipe = core.sessions[wkey].pipe
api.stump_hide(wkey); api.stump_hide(key)
check(wkey not in keys() and key not in keys() and api.stump_hidden_count() == 2, "both hidden: off the list")
check(not pipe.running, "hidden Wi-Fi node: polling stopped")
time.sleep(8)
check(key not in keys(), "a hidden LoRa node stays hidden while it keeps beaconing")
api.stump_restore_hidden()
check(key in keys() and wkey in keys(), "'show again' brings both back")
check(len(core.store.lines(key, v()["room"])) == mesh_lines and len(w()["lines"]) == wifi_lines,
      "...with their history kept")

print("-- remove the Wi-Fi Stump")
api.stump_remove(wkey)
check(wkey not in keys() and api.stump_hidden_count() == 0, "removed: not listed, and not hidden either")
check(core.store.stump_node(wkey) is None and not core.store.lines(wkey, "lounge") and not core.store.dm_threads(wkey),
      "removed: node, rooms and DMs deleted from the phone")
api.stump_connect_wifi("127.0.0.1:47530")
check(wkey in keys() and json.loads(api.stump_view(wkey))["auth"] == "none",
      "connecting again starts a fresh entry (our old state, like verification, is gone)")
api.stump_close(wkey)
srv.shutdown()

print("-- remove the mesh Stump")
api.stump_remove(key)
check(key not in keys() and core.store.stump_node(key) is None, "removed from the list and the phone")
check(not core.store.messages(node_hex) and core.store.peer(node_hex) is None and not core.is_stump(node_hex),
      "its raw LXMF traffic and peer entry are gone too")
wait(lambda: key in keys() and core.is_stump(node_hex), 40,
     "still on the air: its next beacon brings it back as a new entry")
check(not core.store.lines(key, "lxmf") and not core.store.dm_threads(key), "...with no old history")

print("-- announce before a first message")
core.last_announce = 0
before = time.time()
core.send("11" * 16, "first message to someone new")
wait(lambda: core.last_announce >= before, 5, "announced just before the first message to a new peer")
t = core.last_announce
core.send("11" * 16, "second message")
time.sleep(2)
check(core.last_announce == t, "no extra announce for the second message")

core.stop(); node.terminate()
out = node.stdout.read()
print("--- node saw:", " | ".join(l[9:60] for l in out.splitlines() if l.startswith("NODE got"))[:500])
shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED"); sys.stdout.flush(); os._exit(0 if ok else 1)
