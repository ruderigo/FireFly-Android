"""FireFly's stamp search against LXMF's own code, as LXMF runs it on Android.

    python3 engine-tests/run_stamps.py
"""
import os, sys, threading, time
os.environ["ANDROID_ROOT"] = "/system"            # LXMF takes its Android stamp path
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python"))
import RNS
from LXMF import LXStamper
from firefly.core import _install_fast_stamper, fast_stamp_job

ok = True
def check(c, what):
    global ok
    print(("  PASS " if c else "  FAIL ") + what); ok = ok and bool(c)

check(_install_fast_stamper() and LXStamper.job_android is fast_stamp_job, "installed as LXMF's Android stamp job")

for label, rounds_wb in (("propagation stamp (250 KB work block)", LXStamper.WORKBLOCK_EXPAND_ROUNDS_PN),
                         ("delivery stamp (750 KB work block)", LXStamper.WORKBLOCK_EXPAND_ROUNDS)):
    times = []
    for cost in (8, 12, 16):
        mid = os.urandom(32)
        t = time.time()
        stamp, value = LXStamper.generate_stamp(mid, cost, expand_rounds=rounds_wb)   # LXMF's own entry point
        times.append(time.time() - t)
        wb = LXStamper.stamp_workblock(mid, expand_rounds=rounds_wb)
        check(stamp is not None and LXStamper.stamp_valid(stamp, cost, wb) and value >= cost,
              f"{label}, cost {cost}: valid by LXMF's check (value {value}) in {times[-1]:.2f} s")

# Many cost-16 stamps: the time a phone user waits, minus the phone's slower CPU
mid = os.urandom(32); wb = LXStamper.stamp_workblock(mid, LXStamper.WORKBLOCK_EXPAND_ROUNDS_PN)
ts = []
for i in range(20):
    t = time.time(); s, r = fast_stamp_job(16, wb, os.urandom(8)); ts.append(time.time() - t)
    assert LXStamper.stamp_valid(s, 16, wb)
ts.sort()
print(f"      20 cost-16 searches: median {ts[10]:.3f} s, slowest {ts[-1]:.3f} s (this machine)")

# Cancellation, as LXMF does it when a message is cancelled mid-stamp
mid = os.urandom(16); res = {}
th = threading.Thread(target=lambda: res.update(r=fast_stamp_job(60, wb, mid)))   # cost 60: never finishes
th.start(); time.sleep(0.5); LXStamper.cancel_work(mid); th.join(5)
check(not th.is_alive() and res["r"][0] is None and mid not in LXStamper.active_jobs,
      "cancel_work() stops it within a moment and returns no stamp")
print("ALL PASSED" if ok else "SOME FAILED")
