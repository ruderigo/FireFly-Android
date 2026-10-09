"""A message's final state can't be overwritten by the outbound monitor.

LXMF leaves a propagated message in SENT when it calls the delivery callback,
so a monitor pass that had already listed the message could write "sent" over
the "stored" the callback just wrote, and nothing corrected it: a message left
at a propagation node showed as "sent" forever (seen about once in 16 runs of
run_propagation.py), and a direct message could stay at "sending" the same way.
This forces that interleaving instead of waiting for it.
    python3 engine-tests/run_outbound_race.py
"""
import os, sys, tempfile, shutil, threading
os.environ["ANDROID_ROOT"] = "/system"
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python"))
from LXMF import LXMessage
from firefly.core import Core

ok = True
def check(cond, what):
    global ok
    print(("  PASS " if cond else "  FAIL ") + what); ok = ok and bool(cond)


class FakeLXM:
    """Just what Core's callbacks and monitor read from an LXMessage."""
    def __init__(self, method, state):
        self.method, self.state = method, state


tmp = tempfile.mkdtemp(prefix="ff-race-")
core = Core(tmp, log=lambda *a: None)
peer = "ab" * 16

for method, state, final in ((LXMessage.PROPAGATED, LXMessage.SENT, "stored"),
                             (LXMessage.DIRECT, LXMessage.SENDING, "delivered"),
                             (LXMessage.OPPORTUNISTIC, LXMessage.SENT, "delivered")):
    mid = core.store.add_message(peer, True, "hello", "sending")
    lxm = FakeLXM(method, state)
    core.outbound[mid] = lxm

    # The monitor has listed the message; the delivery callback fires on
    # another thread before the monitor looks at the row.
    real_message = core.store.message
    fired = []
    def message_with_delivery_in_between(msg_id, _mid=mid, _lxm=lxm):
        if msg_id == _mid and not fired:
            t = threading.Thread(target=core._on_delivered, args=(_mid, _lxm))
            fired.append(t)
            t.start()
            t.join(1)          # completes now if nothing holds it back (the old, racy code)
        return real_message(msg_id)
    core.store.message = message_with_delivery_in_between
    try:
        core._monitor_outbound()
    finally:
        core.store.message = real_message
    for t in fired:
        t.join(5)                       # with the fix it waits for the monitor's lock
    core._monitor_outbound()            # a later pass must not touch it either

    check(fired, f"{final}: the delivery callback ran during the monitor pass")
    check(core.store.message(mid)["state"] == final,
          f"{final}: state stays '{final}' (got '{core.store.message(mid)['state']}')")
    check(mid not in core.outbound, f"{final}: no longer outbound")

shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED"); sys.stdout.flush(); os._exit(0 if ok else 1)
