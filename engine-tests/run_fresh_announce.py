"""Offline Stumps showing online (Nov 9): a relay's remembered announce isn't hearing the Stump.

    python3 run_fresh_announce.py

Any Reticulum relay (every Stump) answers a path request with the last announce it passed on,
days later. FireFly counted that as hearing the Stump: online for 10 minutes (its announce),
35 (its beacon). Now:
  * an announce heard live counts, and so does the Stump's own answer to a path request;
  * a copy already seen doesn't; an older one than the last doesn't; one made long ago doesn't
    (when the Stump's clock is real);
  * a Stump restarted with its clock back near 2000 (a Heltec with no internet) still counts;
  * a replayed beacon doesn't make it online, and its old boot id isn't a restart.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
import RNS
from firefly import api, stump

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

def blob(t):
    return os.urandom(5) + int(t).to_bytes(5, "big")

# --- the rule alone
mem, d = {}, os.urandom(16)
now = time.time()
b1 = blob(now)
check("heard live: fresh", stump.fresh_announce(mem, d, b1, False, 600, now))
check("the same announce again (a relay's copy): not fresh", not stump.fresh_announce(mem, d, b1, True, 600, now))
check("an older one than the last, by path request: not fresh", not stump.fresh_announce(mem, d, blob(now - 120), True, 600, now))
check("a newer one, by path request (the Stump answering itself): fresh", stump.fresh_announce(mem, d, blob(now + 5), True, 600, now))
mem2, d2 = {}, os.urandom(16)
check("first time, by path request, made 3 days ago: not fresh", not stump.fresh_announce(mem2, d2, blob(now - 3 * 86400), True, 600, now))
check("first time, by path request, made a minute ago: fresh", stump.fresh_announce(mem2, d2, blob(now - 60), True, 600, now))
mem3, d3 = {}, os.urandom(16)
heltec = 946684800 + 3600                    # an ESP32 with no internet: 2000-01-01 + an hour of uptime
check("a Heltec's clock (near 2000), first time by path request: its age can't be told, not counted",
      not stump.fresh_announce(mem3, d3, blob(heltec), True, 600, now))
check("...heard live: fresh", stump.fresh_announce(mem3, d3, blob(heltec + 120), False, 600, now))
check("...a newer one by path request (the Stump answering itself): fresh", stump.fresh_announce(mem3, d3, blob(heltec + 200), True, 600, now))
check("...its same announce again: not fresh", not stump.fresh_announce(mem3, d3, mem3[d3.hex()]["blobs"][-1], True, 600, now))
check("...restarted, clock back to 2000 + 10 s, heard live: fresh", stump.fresh_announce(mem3, d3, blob(946684810), False, 600, now))
check("...an older copy by path request after that: not fresh", not stump.fresh_announce(mem3, d3, blob(heltec - 60), True, 600, now))
check("can't tell which announce: only a live one counts", stump.fresh_announce({}, d, None, False, 600)
      and not stump.fresh_announce({}, d, None, True, 600))

# --- in FireFly, through Reticulum's path table as it fills it
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-fresh-"), None, None, L())
c = api._core
c.can_reach = lambda h: True                 # the radio can carry it (no radio in this test)
ident = RNS.Identity()
dest = RNS.Destination.hash(ident, "lxmf", "delivery")
LX = dest.hex()
def _announce_handlers():
    """Reticulum's registered announce handlers: a list up to 1.5.x, a table by aspect from 1.6."""
    ah = RNS.Transport.announce_handlers
    return [h for hs in ah.values() for h in hs] if isinstance(ah, dict) else list(ah)
handler = next(h for h in _announce_handlers() if type(h).__name__ == "_DeliveryAnnounces")
beacons = next(h for h in _announce_handlers() if isinstance(h, stump.BeaconHandler))
def arrive(h, dhash, b, path_response, app_data=None, idn=None):
    ph = os.urandom(32)
    RNS.Transport.path_table[dhash] = [time.time(), os.urandom(16), 2, time.time() + 86400, [b], None, ph]
    h.received_announce(dhash, idn or ident, app_data, announce_packet_hash=ph, is_path_response=path_response)
reach = lambda: c.stump_reach("mesh:" + LX)["state"]

# A Stump, heard by its beacon an hour ago, its last announce made an hour ago.
c.store.upsert_stump_node("mesh:" + LX, "mesh", LX, name="LaBuche", last_seen=time.time() - 3600)
c.stumps[LX] = ("LaBuche", "1.0.0-beta.57")
old = blob(time.time() - 3600)
c.store.upsert_peer(LX, "lxmf", heard=False)
check("(an hour since it was heard: offline)", reach() == "offline", reach())
arrive(handler, dest, old, True)
check("a relay answers the pull-to-refresh with its hour-old announce: still offline", reach() == "offline",
      (reach(), (c.store.peer(LX) or {}).get("last_heard")))
arrive(handler, dest, old, True)
check("...and again: still offline", reach() == "offline", reach())
arrive(handler, dest, blob(time.time()), False)
check("the Stump announces itself (live): online", reach() == "lora", reach())

# Opening a Stump, or "seen" times saved before (older versions), aren't hearing it.
ident9 = RNS.Identity(); LX9 = RNS.Destination.hash(ident9, "lxmf", "delivery").hex()
c.stumps[LX9] = ("Racine", "1.0.0-beta.57")
c.store.upsert_stump_node("mesh:" + LX9, "mesh", LX9, name="Racine", last_seen=time.time())
c.store.upsert_peer(LX9, "lxmf")                 # last_heard: now, as an older FireFly saved it
r9 = lambda: c.stump_reach("mesh:" + LX9)["state"]
check("'seen' times saved by an older FireFly: not online (this run never heard it)", r9() == "offline", r9())
api.stump_view("mesh:" + LX9)                    # opening it
check("opening a Stump: still offline", r9() == "offline", r9())
json.loads(api.stump_verify("mesh:" + LX9, True))
check("...verifying (entering it): still offline", r9() == "offline", r9())

# Beacons: a replayed one doesn't make it online, and its old boot id isn't a restart.
ident2 = RNS.Identity()
dest2 = RNS.Destination.hash(ident2, "lxmf", "delivery"); LX2 = dest2.hex()
bdest = stump.beacon_hash_for(ident2)
import RNS.vendor.umsgpack as mp
def beacon_data(boot):
    return mp.packb(["stump", "1.0.0-beta.57", "Souche", dest2, ["http://10.0.0.9"], {"b": boot}])
arrive(beacons, bdest, blob(time.time()), False, beacon_data("B2"), ident2)
c.store.upsert_stump_node("mesh:" + LX2, "mesh", LX2, last_seen=time.time() - 3600)   # an hour later, it's gone
restarts = dict(c.restarted_at)
arrive(beacons, bdest, blob(time.time() - 7200), True, beacon_data("B1"), ident2)       # a relay's old copy
check("a replayed beacon: not seen now (still an hour ago)", time.time() - c.store.stump_node("mesh:" + LX2)["last_seen"] > 3000,
      c.store.stump_node("mesh:" + LX2))
check("...and its old boot id isn't taken for a restart", c.restarted_at == restarts and c.boots.get(LX2) == "B2",
      (c.boots.get(LX2), c.restarted_at))
arrive(beacons, bdest, blob(time.time()), False, beacon_data("B3"), ident2)
check("its real next beacon (a new boot id): seen now, a restart", time.time() - c.store.stump_node("mesh:" + LX2)["last_seen"] < 10
      and c.boots.get(LX2) == "B3" and LX2 in c.restarted_at)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
