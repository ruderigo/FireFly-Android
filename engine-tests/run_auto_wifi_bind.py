"""The Local network link's sockets, bound to the Wi-Fi (Nov 17, with the Stump team).

    python3 run_auto_wifi_bind.py

On Android, AutoInterface's sockets weren't bound to the Wi-Fi network: on a Wi-Fi with no
internet their packets went over mobile data ("Network is unreachable"), so the phone was
heard there but couldn't answer, and computer Stumps routed to it that way. Now each socket
AutoInterface makes goes through the Android bridge's bindSocketToWifi(fd). Only that module's
sockets; nothing without a bridge that binds.
"""
import os, socket, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
from firefly import core
import RNS.Interfaces.AutoInterface as auto

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

real = auto.socket
check("no bridge that binds (desktop, tests): nothing changes",
      core._bind_auto_sockets(None) is False and core._bind_auto_sockets(object()) is False and auto.socket is real)

class Bridge:
    def __init__(self, ok=True): self.fds, self.ok = [], ok
    def bindSocketToWifi(self, fd):
        self.fds.append(fd); return self.ok
b = Bridge()
check("a bridge that binds: AutoInterface's socket module stood in for", core._bind_auto_sockets(b) and auto.socket is not real)
s = auto.socket.socket(auto.socket.AF_INET, auto.socket.SOCK_DGRAM)
check("each socket AutoInterface makes is handed to bindSocketToWifi", b.fds == [s.fileno()], (b.fds, s.fileno()))
check("...and is a real socket", isinstance(s, socket.socket))
s.close()
check("the rest of the module is the real one", auto.socket.AF_INET6 == socket.AF_INET6
      and auto.socket.getaddrinfo is socket.getaddrinfo)
check("other modules' sockets untouched", socket.socket is not auto.socket.socket and len(b.fds) == 1)
x = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); x.close()
check("...a socket made elsewhere isn't bound", len(b.fds) == 1, b.fds)
b2 = Bridge(ok=False)
core._bind_auto_sockets(b2)
s2 = auto.socket.socket(auto.socket.AF_INET, auto.socket.SOCK_DGRAM)
check("attached again: the new bridge, still one stand-in", b2.fds == [s2.fileno()] and len(b.fds) == 1)
check("Android refusing (no Wi-Fi up): the socket still works, counted", auto.socket.refused >= 1)
s2.close()
class Broken:
    def bindSocketToWifi(self, fd): raise RuntimeError("no")
core._bind_auto_sockets(Broken())
s3 = auto.socket.socket(auto.socket.AF_INET, auto.socket.SOCK_DGRAM)
check("a bridge that throws doesn't break the link", isinstance(s3, socket.socket))
s3.close()

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
