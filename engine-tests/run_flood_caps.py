"""Flooding caps (Nov 17, from the potential-carrot review).

    python3 run_flood_caps.py

A stream of new LXMF identities (cheap on a Wi-Fi or TCP interface) used to add a contact
each, for good, make the phone send a Stump-beacon path request for each on every interface
(LoRa included), and grow FireFly's in-memory lists for as long as it ran. Now:
  * contacts only ever heard (never written to, not saved, not a Stump) keep the 500 most
    recently heard;
  * at most one beacon path request per 5 seconds for announces heard;
  * each in-memory list keeps its 4000 most recent addresses.
"""
import os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
from firefly import api, stump, core as core_mod
import RNS

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

# ---- the bounded containers
r = stump.Recent(3)
for k in "abcd": r[k] = 1
check("Recent keeps its limit, the oldest goes", list(r) == ["b", "c", "d"], list(r))
r["b"] = 2; r["e"] = 1
check("...writing a key makes it the newest", list(r) == ["d", "b", "e"], list(r))
r.setdefault("d", 9); r.setdefault("f", {})
check("...setdefault counts as writing (fresh_announce's memory)", list(r) == ["e", "d", "f"] and r["d"] == 1, dict(r))
s = stump.RecentSet(2)
s.add("x"); s.add("y"); s.add("z")
check("RecentSet keeps its limit", "x" not in s and "y" in s and "z" in s and len(s) == 2)

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-flood-"), None, None, L())
c = api._core
asked = []
RNS.Transport.request_path = lambda h, *a, **k: asked.append(h)

# ---- contacts only ever heard: the 500 most recent
keepers = {"saved": "a1" * 16, "talked": "b2" * 16, "stump": "c3" * 16}
c.store.upsert_peer(keepers["saved"], "lxmf", name="Saved", saved=1)
c.store.upsert_peer(keepers["talked"], "lxmf", name="Talked")
c.store.add_message(keepers["talked"], False, "hi", "received", ts=time.time())
c.store.upsert_peer(keepers["stump"], "lxmf", name="Bois", stump="Bois")
for i in range(1200):
    c._on_delivery_announce(bytes.fromhex(f"{i:032x}"), None, None, fresh=True)
heard_only = [p for p in c.store.peers("lxmf") if not p.get("saved") and not p.get("stump") and p["hash"] not in keepers.values()]
check("1200 new identities: contacts only ever heard stay near 500", 500 <= len(heard_only) <= 525, len(heard_only))
check("...the most recent kept", any(p["hash"] == f"{1199:032x}" for p in heard_only)
      and not any(p["hash"] == f"{0:032x}" for p in heard_only))
check("...a saved contact stays", c.store.peer(keepers["saved"]) is not None)
check("...a Stump stays", c.store.peer(keepers["stump"]) is not None)
if hasattr(c.store, "add_message"):
    check("...someone written to stays", c.store.peer(keepers["talked"]) is not None)

# ---- beacon path requests: at most one per 5 s
idents = [RNS.Identity() for _ in range(20)]
asked.clear()
c._beacon_ask_at = 0
for ident in idents:
    c._on_delivery_announce(RNS.Destination.hash(ident, "lxmf", "delivery"), ident, None, fresh=True)
check("20 new identities at once: one beacon path request", len(asked) == 1, len(asked))
c._beacon_ask_at -= core_mod.BEACON_ASK_GAP_S + 1
ident = idents[5]
c._on_delivery_announce(RNS.Destination.hash(ident, "lxmf", "delivery"), ident, None, fresh=True)
check("...one skipped is asked at its next announce, 5 s later", len(asked) == 2, len(asked))

# ---- in-memory lists: 4000 most recent
for i in range(5000):
    c._on_delivery_announce(bytes.fromhex(f"{i + 100000:032x}"), None, None, fresh=True)
check("5000 more: what's heard in memory stays at 4000", len(c.announce_heard) == stump.HEARD_MAX, len(c.announce_heard))
handler = next(h for h in RNS.Transport.announce_handlers if type(h).__name__ == "_DeliveryAnnounces")
for i in range(5000):               # what an announce with a route leaves in its memory
    stump.fresh_announce(handler.seen, bytes.fromhex(f"{i + 200000:032x}"), os.urandom(10), False, 600)
check("...and the announce handler's memory too", len(handler.seen) <= stump.HEARD_MAX, len(handler.seen))
check("...the newest kept", f"{204999:032x}" in handler.seen)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
