"""FireFly's stamp search against Reticulum's and LXMF's own code, as they run on Android.

    python3 engine-tests/run_stamps.py

From Reticulum 1.6.1 / LXMF 1.3.1 every stamp (LXMF's message stamps, Reticulum's announce
stamps) is made by RNS.Stamps; its Android path forks worker processes with multiprocessing,
which Chaquopy's Python doesn't have. FireFly's single-process search replaces it (here
multiprocessing is made to fail, as on the phone). Before 1.6, the same for LXMF's stamper.
"""
import multiprocessing, os, sys, threading, time
os.environ["ANDROID_ROOT"] = "/system"            # Reticulum and LXMF take their Android path
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python"))
import RNS
from LXMF import LXStamper
from firefly.core import _install_fast_stamper, fast_stamp_job, fast_rns_stamp_job

ok = True
def check(c, what):
    global ok
    print(("  PASS " if c else "  FAIL ") + what); ok = ok and bool(c)

def no_multiprocessing(*a, **k):
    raise OSError("no multiprocessing here (like Chaquopy)")
multiprocessing.get_context = no_multiprocessing
multiprocessing.Manager = no_multiprocessing
multiprocessing.Pool = no_multiprocessing

try:
    import RNS.Stamps as Stamps
except ImportError:
    Stamps = None

if Stamps is not None:                                  # Reticulum 1.6.1+
    check(_install_fast_stamper() and Stamps.job_android is fast_rns_stamp_job, "installed as Reticulum's Android stamp job")
    # LXMF's message stamps, through LXMF's own entry point
    for label, rounds_wb in (("propagation stamp (250 KB work block)", LXStamper.WORKBLOCK_EXPAND_ROUNDS_PN),
                             ("delivery stamp (750 KB work block)", LXStamper.WORKBLOCK_EXPAND_ROUNDS)):
        for cost in (8, 12, 16):
            mid = os.urandom(32)
            t = time.time()
            stamp, value = LXStamper.generate_stamp(mid, cost, expand_rounds=rounds_wb)
            wb = Stamps.stamp_workblock(mid, expand_rounds=rounds_wb)
            check(stamp is not None and len(stamp) == LXStamper.STAMP_SIZE and Stamps.stamp_valid(stamp, cost, wb),
                  f"{label}, cost {cost}: valid by Reticulum's check (value {value}) in {time.time() - t:.2f} s")
    # Announce stamps: 14 bytes, fractional costs, no work block expansion (as Destination does)
    for cost in (8.0, 15.8, 18.5):
        material = os.urandom(32 + 64 + 10)
        t = time.time()
        stamp, value = Stamps.generate_stamp(material, stamp_cost=cost, expand_rounds=0, size=RNS.Identity.STAMPLENGTH)
        wb = Stamps.stamp_workblock(material, expand_rounds=0)
        check(stamp is not None and len(stamp) == RNS.Identity.STAMPLENGTH and Stamps.stamp_valid(stamp, cost, wb),
              f"announce stamp, cost {cost}: {len(stamp or b'')} bytes, valid (value {value}) in {time.time() - t:.2f} s")
    # Cancellation, as Reticulum does it
    mid = os.urandom(16); res = {}
    th = threading.Thread(target=lambda: res.update(r=fast_rns_stamp_job(Stamps.cost_to_fp(60), b"x" * 64, mid, 14)))
    th.start(); time.sleep(0.5); Stamps.cancel_work(mid); th.join(5)
    check(not th.is_alive() and res["r"][0] is None and mid not in Stamps.active_jobs,
          "cancel_work() stops it within a moment and returns no stamp")
else:                                                    # before Reticulum 1.6: LXMF's stamper
    check(_install_fast_stamper() and LXStamper.job_android is fast_stamp_job, "installed as LXMF's Android stamp job")
    for cost in (8, 12, 16):
        mid = os.urandom(32)
        stamp, value = LXStamper.generate_stamp(mid, cost, expand_rounds=LXStamper.WORKBLOCK_EXPAND_ROUNDS)
        wb = LXStamper.stamp_workblock(mid, expand_rounds=LXStamper.WORKBLOCK_EXPAND_ROUNDS)
        check(stamp is not None and LXStamper.stamp_valid(stamp, cost, wb), f"delivery stamp, cost {cost}: valid")
    mid = os.urandom(16); res = {}
    wb = LXStamper.stamp_workblock(os.urandom(32), LXStamper.WORKBLOCK_EXPAND_ROUNDS_PN)
    th = threading.Thread(target=lambda: res.update(r=fast_stamp_job(60, wb, mid)))
    th.start(); time.sleep(0.5); LXStamper.cancel_work(mid); th.join(5)
    check(not th.is_alive() and res["r"][0] is None, "cancel_work() stops it")

print("ALL PASSED" if ok else "SOME FAILED")
sys.stdout.flush(); os._exit(0 if ok else 1)
