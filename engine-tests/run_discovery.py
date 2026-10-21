"""Finding Stumps on a network, and how steady each link is.

    python3 run_discovery.py      (binds port 80 on loopback addresses: run as root)

Three fake Stumps and one ordinary web server, on 127.0.0.5, .9, .12 and .7,
all port 80: the sweep of 127.0.0.0/24 must find exactly the Stumps, read
their name and LXMF address from /node.json where they have it, fall back to
/chain.json or /files.json where they don't, never touch /rrc/poll (that
would make the phone a chat user), and rank the slow one last.
"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from fake_stump import FakeRRC, FakeWeb, rns_verify, serve_http
from firefly import discovery
from firefly.stump_session import UrllibHttp

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    fails += 0 if ok else 1

# /node.json exactly as barkeep.py builds it (Stump main after 1.0.0-beta.2): chain is null unless
# chained, then the /chain.json object.
A = {"name": "LaBuche", "lxmf": "53d91d2f" + "0" * 20 + "48e4", "version": "Beta A", "release": "1.0.0-beta.2",
     "ap_ip": "192.168.4.1",
     "chain": {"enabled": True, "role": "root", "ap_ip": "192.168.4.1", "channel": 11, "secret": True,
               "uplink": None, "below": ["192.168.4.23"]},
     "features": ["chat", "billboard", "files", "about"]}
UNNAMED = {"name": None, "lxmf": None, "version": "Beta A", "release": "1.0.0-beta.2", "ap_ip": "192.168.6.1",
           "chain": None, "features": ["billboard"]}
HOME_PAGE = "<!DOCTYPE html><html><body>Stump</body></html>"
polled = []
class Spy(FakeRRC):
    def poll(self, key, since):
        polled.append(key)
        return super().poll(key, since)

serve_http(80, Spy(rns_verify), FakeWeb(), host="127.0.0.5", node=A)              # beta.3-era node
serve_http(80, Spy(rns_verify), FakeWeb(), host="127.0.0.9")                      # beta.2: /chain.json only
os.environ.pop("FAKE_STUMP_OLD", None)
serve_http(80, Spy(rns_verify), FakeWeb(), host="127.0.0.12", delay=0.25)         # a far one: slow answers
serve_http(80, Spy(rns_verify), FakeWeb(), host="127.0.0.14", node=UNNAMED)        # no name, LoRa off
serve_http(80, Spy(rns_verify), FakeWeb(), host="127.0.0.16", node=HOME_PAGE)      # beta.2: /node.json is the home page
serve_http(8080, Spy(rns_verify), FakeWeb(), host="127.0.0.20",                      # a Stump on a computer: port 8080
           node=dict(A, name="Laptop", lxmf="ab" * 16, ap_ip="192.168.137.1", chain=None))

class Plain(BaseHTTPRequestHandler):                                              # not a Stump
    def log_message(self, *a): pass
    def do_GET(self):
        body = b"<html>router admin</html>"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = ThreadingHTTPServer(("127.0.0.7", 80), Plain)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)
http = UrllibHttp()

print("-- one address")
p = discovery.probe(http, "http://127.0.0.5")
check("node.json: name, LXMF address, chain role", p["stump"] and p["name"] == "LaBuche" and p["lxmf"] == A["lxmf"]
      and p["chain"]["role"] == "root" and p["chain"]["below"] == 1 and p["via"] == "/node.json", p)
check("node.json: release and features", p["release"] == "1.0.0-beta.2" and p["features"] == ["chat", "billboard", "files", "about"], p)
p2 = discovery.probe(http, "http://127.0.0.14")
check("node.json with null name and lxmf is still node.json; chain null = not chained",
      p2["stump"] and p2["via"] == "/node.json" and p2["name"] is None and p2["lxmf"] is None
      and p2["chain"]["enabled"] is False and p2["features"] == ["billboard"], p2)
p3 = discovery.probe(http, "http://127.0.0.16")
check("a node that answers /node.json with its home page (HTML): falls back to /chain.json",
      p3["stump"] and p3["via"] == "/chain.json", p3)
check("steady: 5 answers, none lost, 3 bars", p["samples"] == 5 and p["lost"] == 0 and p["steady_bars"] == 3, p)
p = discovery.probe(http, "http://127.0.0.9")
check("no node.json yet: recognised by /chain.json, no name", p["stump"] and p["via"] == "/chain.json" and p["name"] is None, p)
os.environ["FAKE_STUMP_OLD"] = "1"
p = discovery.probe(http, "http://127.0.0.9")
check("an older node: recognised by /files.json", p["stump"] and p["via"] == "/files.json", p)
del os.environ["FAKE_STUMP_OLD"]
p4 = discovery.probe(http, "http://127.0.0.20")
check("a Stump on a computer (port 8080) found from its bare address", p4["stump"] and p4["name"] == "Laptop"
      and p4["base"] == "http://127.0.0.20:8080", p4)
check("a web server that isn't a Stump: not one", discovery.probe(http, "http://127.0.0.7") == {"base": "http://127.0.0.7", "stump": False})
check("nobody there: not one, quickly", discovery.probe(http, "http://127.0.0.3")["stump"] is False)

print("-- the whole /24")
t = time.monotonic()
found = discovery.sweep(http, "127.0.0.1", 24)
took = time.monotonic() - t
check("sweep finds exactly the six Stumps, the one on port 8080 too", sorted(f["base"] for f in found) ==
      ["http://127.0.0.12", "http://127.0.0.14", "http://127.0.0.16", "http://127.0.0.20:8080", "http://127.0.0.5",
       "http://127.0.0.9"],
      [f["base"] for f in found])
check(f"...in a few seconds ({took:.1f} s)", took < 10)
check("the slow one is ranked last", found[-1]["base"] == "http://127.0.0.12" and found[-1]["median_ms"] >= 240, found[-1])
check("skip: the gateway isn't listed twice", "http://127.0.0.5" not in
      [f["base"] for f in discovery.sweep(http, "127.0.0.1", 24, skip=["127.0.0.5"])])
check("discovery never polls the chat (no '✓ guest' for anyone)", polled == [], polled)

print("-- addresses, bars, names")
check("/24 hosts: 253, not the phone", len(discovery.lan_hosts("192.168.1.37", 24)) == 253
      and "192.168.1.37" not in discovery.lan_hosts("192.168.1.37", 24))
check("a bigger LAN (/16) is still swept as its /24", len(discovery.lan_hosts("10.0.7.9", 16)) == 253)
check("a smaller subnet (/28): its 13 others", len(discovery.lan_hosts("192.168.5.20", 28)) == 13)
check("signal bars", [discovery.signal_bars(r) for r in (-50, -65, -80, -90, None)] == [3, 2, 1, 0, None])
check("steadiness bars", [discovery.steady_bars(*a) for a in ((40, 0, 5), (300, 1, 5), (900, 2, 5), (None, 5, 5))] == [3, 2, 1, 0])
check("LoRa bars from SNR", [discovery.lora_bars(s) for s in (8, 0, -8, -14, None)] == [3, 2, 1, 0, None])
check("overall: the weakest known part", discovery.overall(3, 1, None) == 1 and discovery.overall(None) is None)
check("hotspot names", [discovery.looks_like_stump(s, ["Bob's Stump Café"]) for s in
      ("LaBuche-Stump.web.app", "Bob's Stump Café", "stump-b", "Starbucks", "")] == ["yes", "yes", "likely", "maybe", "no"])

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
