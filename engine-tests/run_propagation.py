"""Leaving messages at a Stump propagation node, as on LaBuche.

The fake node is a real upstream LXMF propagation node (stamp cost 16) that
announces itself once, before FireFly starts, so FireFly can only find it by
asking. FireFly runs as on Android: ANDROID_ROOT set, and multiprocessing
unusable, as under Chaquopy.

    python3 engine-tests/run_propagation.py
"""
import json, multiprocessing, os, shutil, subprocess, sys, tempfile, time
os.environ["ANDROID_ROOT"] = "/system"
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python")); sys.path.insert(0, here)

def no_mp(*a, **k):
    raise OSError("This platform lacks a functioning sem_open implementation")   # Chaquopy's answer
multiprocessing.Manager = no_mp

from firefly.core import Core
from firefly import api
import RNS

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
        time.sleep(0.3)
    check(False, what); return False
def until(proc, prefix, secs=60):
    t = time.time()
    while time.time() - t < secs:
        line = proc.stdout.readline()
        if line.startswith(prefix): return line.split()
    return None

import atexit, random
PORT = str(random.randint(47700, 48900))          # a fresh port: no clash with a crashed run
children = []
atexit.register(lambda: [c.kill() for c in children])
tmp = tempfile.mkdtemp(prefix="ff-pn-")
node = subprocess.Popen([sys.executable, os.path.join(here, "fake_stump.py"), os.path.join(tmp, "node"), PORT],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=dict(os.environ, FAKE_PN="1", FAKE_PN_LIMIT_KB="2"))
children.append(node)
pn_hex = until(node, "NODE pn")[2]
node_hex = until(node, "NODE ready")[2]
print("node propagation address", pn_hex)
time.sleep(12)                      # its one propagation announce is over before anyone listens

core = Core(os.path.join(tmp, "me"), log=lambda *a: None)
core.settings.update({"auto_interface": False, "tcp_peers": [f"127.0.0.1:{PORT}"], "display_name": "Rod Phone",
                      "log_level": 2, "radio": {"enabled": False}})
core.start(); api._core = core

print("-- finding the propagation node")
wait(lambda: core.is_stump(node_hex), 30, "LaBuche recognised from its beacon")
wait(lambda: core.propagation_node() == pn_hex, 40, "its propagation node found without hearing its announce")
nodes = json.loads(api.propagation_nodes())
check(any(n["hash"] == pn_hex and n.get("stump") for n in nodes), "listed as LaBuche's propagation node")
print("     ", [(n["name"], n["hash"][:8], n.get("stump"), n.get("stamp_cost"), n.get("transfer_limit_kb")) for n in nodes])

print("-- the friend comes online, announces, then goes offline")
peer_home = os.path.join(tmp, "friend")
p = subprocess.Popen([sys.executable, os.path.join(here, "pn_peer.py"), peer_home, PORT, "20"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
children.append(p)
friend = until(p, "PEER ready")[2]; p.wait()
print("friend", friend, "is now offline")
wait(lambda: RNS.Identity.recall(bytes.fromhex(friend)) is not None, 5,
     "we heard the friend's announce before they left (their first announce wasn't lost)")
print("-- leaving a message for the offline friend")
t0 = time.time()
mid = core.send(friend, "left at the node for you", method="propagated")

wait(lambda: core.store.message(mid)["state"] == "stored", 240, "stamped (cost 16, no multiprocessing) and stored at the node")
print("      stamp + upload took %.0f s" % (time.time() - t0))

print("-- the friend comes back and syncs")
time.sleep(5)
p = subprocess.Popen([sys.executable, os.path.join(here, "pn_peer.py"), peer_home, PORT, "90", "sync"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
children.append(p)
got = None
t_f = time.time()
while time.time() - t_f < 150:
    line = p.stdout.readline()
    if line.startswith("PEER state"): print("      friend sync:", line.split(" ", 2)[2].strip(), "at %.0f s" % (time.time() - t_f))
    if line.startswith("PEER got"): got = line.split(); break
check(got is not None and "left at the node for you" in " ".join(got), "friend collected it with a sync")
p.terminate()

st = json.loads(api.status())["propagation"]
check(st["transfer_limit_kb"] == 2 and st["stamp_cost"] == 16 and st["stump"] == "LaBuche Test",
      f"status shows the node's limits and that it's LaBuche's: {st['transfer_limit_kb']} KB, cost {st['stamp_cost']}")

print("-- someone whose identity we know but who has never been online (no path)")
carol_id = RNS.Identity()
carol_home = os.path.join(tmp, "carol"); os.makedirs(carol_home, exist_ok=True)
for f in ("identity", "identity.bak"):
    open(os.path.join(carol_home, f), "wb").write(carol_id.get_private_key())
carol = RNS.Destination.hash(carol_id, "lxmf", "delivery").hex()
RNS.Identity.remember(RNS.Identity.full_hash(os.urandom(16)), bytes.fromhex(carol), carol_id.get_public_key())
m1 = api.send(carol, "auto-routed to the node because Carol has no path")
long_ids = [api.send(carol, f"long message {i}: " + "x" * 700) for i in range(3)]
big = api.send(carol, "B" * 2600)
wait(lambda: core.store.message(m1)["state"] == "stored", 240,
     "no path -> left at the node at once (no direct attempts first)")
check(core.store.message(m1)["method"] == "propagated", "sent as propagated")
wait(lambda: core.store.message(big)["state"] == "failed", 120, "2.6 KB message refused: over the node's 2 KB limit")
print("      reason:", core.store.message(big)["reason"])
wait(lambda: all(core.store.message(i)["state"] == "stored" for i in long_ids), 300, "three 700-char messages stored")

print("-- things that must never go through the node")
ghost_id = RNS.Identity()
ghost = RNS.Destination.hash(ghost_id, "lxmf", "delivery").hex()
RNS.Identity.remember(RNS.Identity.full_hash(os.urandom(16)), bytes.fromhex(ghost), ghost_id.get_public_key())
core.stumps[ghost] = ("Ghost Stump", "test")
from firefly.stump_session import MeshPipe
stump_msg = MeshPipe(core, ghost).send("/join lounge")
api.use_propagation_node("off")
check(core.propagation_node() is None, "propagation off: no node in use")
off_msg = api.send(carol, "with propagation off")
time.sleep(30)
for mid_, what in ((stump_msg, "Stump chat line to an unreachable Stump"), (off_msg, "message with propagation off")):
    row = core.store.message(mid_)
    check(row["method"] != "propagated" and row["state"] != "stored", f"{what}: never left at a node ({row['state']})")
api.use_propagation_node(pn_hex)
check(json.loads(api.status())["propagation"]["mode"] == "manual" and core.propagation_node() == pn_hex,
      "pinned by address")
api.use_propagation_node("auto")
wait(lambda: core.propagation_node() == pn_hex, 30, "back to automatic: LaBuche's node chosen again")

print("-- overlapping sync requests")
first, second = core.sync(), core.sync()
check(first and not second, "a second sync while one is running joins it instead of starting a parallel chain")
wait(lambda: not core._sync_chain, 60, "the chain finishes")

print("-- Carol comes online for the first time and syncs")
cp = subprocess.Popen([sys.executable, os.path.join(here, "pn_peer.py"), carol_home, PORT, "200", "sync"],
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                      env=dict(os.environ, FF_DOWNLOAD_LIMIT_KB="2"))
children.append(cp)
got, rounds, t, last = [], 0, time.time(), time.time()
want = ["auto-routed to the node because Carol has no path"] + [f"long message {i}: " + "x" * 700 for i in range(3)]
import select
while time.time() - t < 200 and not (all(w in [g[9:] for g in got] for w in want) and time.time() - last > 5):
    if not select.select([cp.stdout], [], [], 1)[0]:
        continue
    line = cp.stdout.readline()
    open("/tmp/carol.log", "a").write(f"{time.time()-t:6.1f} {line}")
    last = time.time()
    if line.startswith("PEER got"): got.append(line.strip())
    if line.startswith("PEER rounds"): rounds = max(rounds, int(line.split()[2]))
print("      Carol got:", [g[9:60] for g in got])
texts = [g[9:] for g in got]
missing = [w[:30] for w in want if w not in texts]
if missing: print("      MISSING:", missing, "| states at sender:",
                   [(core.store.message(i)["state"], core.store.message(i)["method"]) for i in [m1] + long_ids])
check(all(w in texts for w in want), "Carol collected all 4 messages left at the node")
check(len(texts) == len(set(texts)), "no duplicates across sync rounds")
check(rounds >= 2, f"it took {rounds} sync rounds (node returns at most 2 KB per sync): continued until empty")
cp.terminate()
core.stop(); node.terminate()

print("-- settings from an older install")
from firefly.settings import Settings
sp = os.path.join(tmp, "old-settings.json")
json.dump({"sync_interval_min": 60, "display_name": "x"}, open(sp, "w"))
check(Settings(sp)["sync_interval_min"] == 30, "the old 60-minute default becomes 30")
json.dump({"sync_interval_min": 45}, open(sp, "w"))
check(Settings(sp)["sync_interval_min"] == 45, "a chosen interval is kept")
shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED"); sys.stdout.flush()
[c.kill() for c in children]
os._exit(0 if ok else 1)
