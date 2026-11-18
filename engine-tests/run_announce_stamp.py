"""Announce at once, stamped once the stamp is made (Nov 18; Reticulum 1.6.1).

    python3 run_announce_stamp.py

Reticulum 1.6.1 puts a proof-of-work stamp on every announce and holds an announce (and the
answer to a path request) until its stamp is made: once per address, then kept, but minutes
on a phone the first time. FireFly announces at once without one meanwhile (no node asks for
stamps unless set to), makes it in the background, then announces stamped; on its next start
the kept stamp is loaded. A cheap stamp here (FIREFLY_STAMP_VALUE), as the Stump team's
reference tests do. Before Reticulum 1.6 there is nothing to do (checked too).
"""
import json, os, subprocess, sys, tempfile, time
os.environ.setdefault("FIREFLY_STAMP_VALUE", "12")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))

if len(sys.argv) > 2 and sys.argv[1] == "--again":       # the second start, in its own process
    from firefly import api
    class L:
        def onEvent(self, s): pass
    api.start(sys.argv[2], None, None, L())
    import RNS
    c = api._core
    print(json.dumps({"state": c.stamp_state, "stamping": RNS.Transport.STAMP_DESTINATIONS,
                      "stamp": c.local.stamp is not None}), flush=True)
    os._exit(0)

from firefly import api
import RNS

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

if not hasattr(RNS.Transport, "STAMP_DESTINATIONS"):
    home = tempfile.mkdtemp(prefix="ff-annstamp-")
    class L:
        def onEvent(self, s): pass
    api.start(home, None, None, L())
    check("Reticulum before 1.6: no announce stamps, nothing changes", api._core.stamp_state is None)
    print("ALL PASS" if not fails else f"{fails} FAILED")
    os._exit(1 if fails else 0)

# Each announce, and whether it carried a stamp.
announces = []
real = RNS.Destination._announce
def spy(self, *a, **k):
    if self.name.startswith("lxmf.delivery"):
        announces.append({"at": time.time(), "stamped": RNS.Transport.STAMP_DESTINATIONS and self.stamp is not None})
    return real(self, *a, **k)
RNS.Destination._announce = spy

home = tempfile.mkdtemp(prefix="ff-annstamp-")
class L:
    def onEvent(self, s): pass
t0 = time.time()
api.start(home, None, None, L())
c = api._core
check("no stamp kept yet: made in the background", c.stamp_state in ("working", "ready"), c.stamp_state)
c.announce()
check("the announce went at once (not held for the stamp)", announces and announces[0]["at"] - t0 < 10, announces[:1])
first_unstamped = announces and not announces[0]["stamped"]
deadline = time.time() + 120
while c.stamp_state == "working" and time.time() < deadline:
    time.sleep(0.2)
check("the stamp is made", c.stamp_state == "ready" and c.local.stamp is not None, c.stamp_state)
check("...then announces are stamped (Reticulum's own setting back on)", RNS.Transport.STAMP_DESTINATIONS)
time.sleep(1)
check("...and FireFly announced again, stamped", any(a["stamped"] for a in announces), announces)
if first_unstamped:
    check("(the first announce went without one, as before 1.6.1)", True)
c.announce()
time.sleep(0.5)
check("a later announce goes at once, stamped", announces[-1]["stamped"] and announces[-1]["at"] - time.time() < 2, announces[-1])

# Its next start: the kept stamp, stamped from the first announce.
r = subprocess.run([sys.executable, __file__, "--again", home], capture_output=True, text=True, timeout=120)
line = next((l for l in r.stdout.splitlines() if l.startswith("{")), "{}")
again = json.loads(line)
check("next start: the stamp kept is loaded, nothing to make", again.get("state") == "ready" and again.get("stamp")
      and again.get("stamping"), (again, r.stderr[-400:]))

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
