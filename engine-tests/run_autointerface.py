"""AutoInterface on a Python without socket.if_nametoindex (like Chaquopy's).

    python3 engine-tests/run_autointerface.py
"""
import os, socket, sys, tempfile, time, shutil
os.environ["ANDROID_ROOT"] = "/system"
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python"))
real_index = socket.if_nametoindex
all_ifs = [n for _, n in socket.if_nameindex()]
del socket.if_nametoindex                     # what the phone's Python looks like
import RNS
from firefly.core import Core, _interface_index

tmp = tempfile.mkdtemp(prefix="ff-auto-")
core = Core(tmp, log=lambda *a: None)
core.settings.update({"auto_interface": True, "log_level": 2, "radio": {"enabled": False}})
core.start()
time.sleep(3)
ok = True
names = [i["name"] for i in core.interface_stats()]
auto = [i for i in core.interface_stats() if i["name"] == "Local network"]
print("  interfaces:", [(i["name"], i["type"], i.get("error")) for i in core.interface_stats()])
for cond, what in ((not hasattr(socket, "if_nametoindex"), "socket.if_nametoindex really missing"),
                   (len(auto) == 1, "Local network listed exactly once"),
                   (auto and auto[0]["type"] != "error", "Local network started (no if_nametoindex error)"),
                   (all_ifs and all(_interface_index(n) == real_index(n) for n in all_ifs),
                    f"interface indexes match the real if_nametoindex ({', '.join(all_ifs)})")):
    print(("  PASS " if cond else "  FAIL ") + what); ok = ok and bool(cond)
core.stop(); shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if ok else "SOME FAILED"); sys.stdout.flush(); os._exit(0 if ok else 1)
