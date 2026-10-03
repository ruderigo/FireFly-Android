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
                        env=dict(os.environ, FAKE_PEER_TIMEOUT="8"))
line = node.stdout.readline()
while "NODE ready" not in line:
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
api.stump_command(key, "/rooms")
wait(lambda: any(r["name"] == "lounge" and r["tier"] == "hybrid" for r in v()["rooms"]), 30,
     "/rooms parsed with tiers")
api.stump_join(key, "lounge")
wait(lambda: v()["room"] == "lounge", 30, "/join moved us (room from the node's → reply)")
check(api.stump_auth(key), "/auth started")
wait(lambda: v()["auth"] == "ok", 45, "/auth over the mesh -> AUTH-OK (one packet, real signature)")
check(v()["auth_detail"] == core.identity.hash.hex(), "node's identity hash equals ours")

print("-- going quiet on the mesh (the node's MESH_PEER_TIMEOUT)")
api.stump_join(key, "main")
wait(lambda: v()["room"] == "main", 30, "joined #main")
time.sleep(10)                                   # longer than the node's timeout (8 s here)
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
api.stump_join(wkey, "lounge")
wait(lambda: w()["room"] == "lounge", 15, "/join: `room` field in the reply moves us")
api.stump_auth(wkey)
wait(lambda: w()["auth"] == "ok", 15, "/auth over HTTP -> AUTH-OK, same code path")
check(w()["auth_detail"] == core.identity.hash.hex(), "same identity on both transports")
srv.shutdown()
wait(lambda: not w()["online"], 30, "node unreachable: session goes offline, backs off")

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
