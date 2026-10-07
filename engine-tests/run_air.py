"""Two FireFly engines, two processes, one simulated LoRa channel.

Phone A: USB RNode.  Phone B: Bluetooth LE RNode.  Every byte goes through
Reticulum's RNodeInterface and FireFly's RadioLink shim on both sides.

    python3 engine-tests/run_air.py
"""
import os, sys, subprocess, tempfile, time, shutil
os.environ["ANDROID_ROOT"] = "/system"
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python")); sys.path.insert(0, here)
import RNS
from fake_rnode import FakeLinks, FakeRNodeLink
from firefly.core import Core

ok = True
def check(c, what):
    global ok
    print(("  PASS " if c else "  FAIL ") + what); ok = ok and bool(c)

def wait(cond, secs, what):
    global ok
    t = time.time()
    while time.time() - t < secs:
        if cond():
            print(f"  PASS {what} ({time.time()-t:.1f}s)"); return True
        time.sleep(0.2)
    print(f"  FAIL {what}"); ok = False; return False

tmp = tempfile.mkdtemp(prefix="ff-air-")
peer = subprocess.Popen([sys.executable, os.path.join(here, "radio_peer.py"), os.path.join(tmp, "b")],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
line = peer.stdout.readline()
while "PEER ready" not in line:
    line = peer.stdout.readline()
    if not line: print("peer died"); sys.exit(1)
b_addr = line.split()[2]
print("phone B (BLE RNode) is", b_addr)

links = FakeLinks()
usb = FakeRNodeLink(style="usb", air=(47610, 47611))
links.usb["usb:/dev/bus/usb/002/003"] = usb
a = Core(os.path.join(tmp, "a"), links=links, log=lambda *x: None)
a.settings.update({"auto_interface": False, "display_name": "Phone A", "log_level": 2})
a.start()
wait(lambda: a.radio.status.state == "online", 15, "phone A's USB RNode online")
wait(lambda: (a.store.peer(b_addr) or {}).get("name") == "Phone B", 40, "A heard B's announce over LoRa")
mid = a.send(b_addr, "Salut B, ça passe par la radio ✓")
wait(lambda: a.store.message(mid)["state"] == "delivered", 40, "A -> B delivered, proof came back over the air")
wait(lambda: any(m["content"].startswith("echo: Salut") for m in a.store.messages(b_addr) if not m["outgoing"]),
     40, "B's echo received by A")
got = [m for m in a.store.messages(b_addr) if not m["outgoing"]][-1:]
if got:
    print("      rssi", got[0]["rssi"], "snr", got[0]["snr"], "method", got[0]["method"])
snap = a.radio.snapshot()
print("      radio: rssi", snap.get("rssi"), "snr", snap.get("snr"), "rx", snap.get("rx"), "tx", snap.get("tx"))
big = "L" * 700
mid2 = a.send(b_addr, big)
wait(lambda: a.store.message(mid2)["state"] == "delivered", 90, "700-char message over a Reticulum link, via both radios")
print("      frames A put on air:", usb.frames_on_air)
print("-- voice notes (Codec 2 1200, LXMF audio field)")
import hashlib, json as _json
from firefly import api
api._core = a
speech = open(os.path.join(here, "kristoff_1200.c2"), "rb").read()        # 5 s of real speech
short = speech[:6 * 45]                                                    # 1.8 s: fits one packet
hq = open(os.path.join(here, "kristoff_3200.c2"), "rb").read()             # the same 5 s at 3200
opus = open(os.path.join(here, "kristoff_opus.ogg"), "rb").read()          # the same 5 s, Ogg Opus 6 kbps
for label, mode, clip in (("1.8 s note (one packet)", 4, short), ("5 s note (over a link)", 4, speech),
                          ("5 s note at 3200 (best quality)", 9, hq), ("5 s Opus note (Ogg)", 16, opus)):
    vid = api.send_voice(b_addr, mode, clip)
    want = f"voice {mode} {len(clip)} {hashlib.sha256(clip).hexdigest()[:16]}"
    wait(lambda: a.store.message(vid)["state"] == "delivered", 90, f"{label}: delivered")
    wait(lambda: any(m["content"] == want for m in a.store.messages(b_addr) if not m["outgoing"]), 60,
         f"{label}: B got it byte for byte ({len(clip)} B)")
    print("      method:", a.store.message(vid)["method"])
rows = _json.loads(api.messages(b_addr))
mine = [r for r in rows if r.get("audio_mode")]
check(len(mine) == 4 and all(r["audio_ms"] == 5000 and r["audio_playable"] for r in mine[1:]),
      "message list: the 5 s notes (1200, 3200, Opus) show 5000 ms, playable, no raw bytes in the JSON")
check(api.voice_mode_for(b_addr) == 16, "default: Opus to B, even though our link to B is LoRa")
a.settings.update({"voice_mode": 0})
check(api.voice_mode_for(b_addr) == 4, "Automatic picks Codec 2 1200 for B: our link to B is LoRa")
a.settings.update({"voice_mode": 16})
check(api.voice_mode_for(b_addr) == 16, "Opus forced: Opus even over LoRa")
a.settings.update({"voice_mode": 0})
check("audio" not in mine[-1], "audio bytes are not in the message list")
check(api.message_audio(mine[1]["id"]) == speech and api.message_audio(mine[2]["id"]) == hq,
      "message_audio returns the exact note for playback")
try:
    api.send_voice(b_addr, 2, b"x" * 30); check(False, "unsupported mode refused")
except ValueError:
    check(True, "unsupported mode refused (450 isn't in Codec 2 1.2)")

print("-- deleting a conversation, completely")
ghost_id = RNS.Identity()
ghost = RNS.Destination.hash(ghost_id, "lxmf", "delivery").hex()
RNS.Identity.remember(RNS.Identity.full_hash(os.urandom(16)), bytes.fromhex(ghost), ghost_id.get_public_key())
waiting = api.send(ghost, "still looking for a path")        # its worker is waiting for a path
time.sleep(2)
api.delete_conversation(ghost)
voice_id = mine[1]["id"]
check(a.store.peer(b_addr) is not None, "before: B is a contact")
api.delete_conversation(b_addr)
convs = [c["peer"] for c in _json.loads(api.conversations())]
check(b_addr not in convs and ghost not in convs, "both conversations gone from the list")
check(_json.loads(api.messages(b_addr)) == [] and api.message_audio(voice_id) == b"", "messages and voice notes deleted")
check(a.store.peer(b_addr) is None and not any(p_["hash"] == b_addr for p_ in _json.loads(api.peers())),
      "the contact is deleted too: not under 'heard' any more")
time.sleep(25)                                                # past the path wait
check(a.store.message(waiting) is None and not any(m["peer"] == ghost for m in _json.loads(api.conversations())),
      "a message deleted while waiting for a path was never sent and didn't come back")
wait(lambda: any(p_["hash"] == b_addr for p_ in _json.loads(api.peers())), 60,
     "B announces again: back under 'heard' as a new entry")
check(not a.store.messages(b_addr), "...with no old messages")
mid3 = a.send(b_addr, "after deleting")
wait(lambda: a.store.message(mid3)["state"] == "delivered", 60, "a new conversation with B works")
api.delete_conversation(b_addr)
wait(lambda: not any(p_["hash"] == b_addr for p_ in _json.loads(api.peers())), 5,
     "removing someone from 'heard' works the same way")

print("-- blocking")
heard = lambda: any(p["hash"] == b_addr for p in _json.loads(api.peers()))
wait(heard, 60, "before: B is heard")
api.block(b_addr)
check(not heard() and not a.store.messages(b_addr), "blocked: B and everything with B deleted")
check(bytes.fromhex(b_addr) in a.router.ignored_list, "LXMF ignores B too (also for messages from a propagation node)")
check(any(x["hash"] == b_addr and x["name"] == "Phone B" for x in _json.loads(api.blocked())), "listed under Blocked, with B's last name")
time.sleep(45)
check(not heard(), "B keeps announcing (45 s): stays gone")
class _Fake: pass
fake = _Fake(); fake.source_hash = bytes.fromhex(b_addr); fake.hash = os.urandom(32); fake.content = b"hi"
fake.fields = {}; fake.title = b""; fake.timestamp = time.time(); fake.method = None
a._on_message(fake)
check(not a.store.messages(b_addr), "a message from B that gets through anyway is dropped, not stored")
check(a.send(b_addr, "hello?") is None, "sending to a blocked person is refused")
stranger = os.urandom(16).hex()
api.block(stranger)
check(any(x["hash"] == stranger and x["name"] is None for x in _json.loads(api.blocked())),
      "an address never heard can be blocked (by address alone)")
try:
    api.add_contact(stranger); check(False, "a blocked address can't be added as a contact")
except ValueError:
    check(True, "a blocked address can't be added as a contact")
api.unblock(stranger)
api.unblock(b_addr)
check(not any(x["hash"] == b_addr for x in _json.loads(api.blocked())) and bytes.fromhex(b_addr) not in a.router.ignored_list,
      "unblocked: off the list, LXMF listens again")
wait(heard, 60, "B comes back only when it announces")
check(not a.store.messages(b_addr), "...with nothing from before")

a.stop(); peer.terminate()
shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED"); sys.stdout.flush(); os._exit(0 if ok else 1)
