"""Home Wi-Fi, no radio: the gateway is the router, not a Stump (Stump team memo, Oct 18).

    python3 run_gateway.py

The "Stump over Wi-Fi" box offered the Wi-Fi's gateway (192.168.0.1, the
router) as "the Stump you're next to", sends went there and came back as a
bare "send failed (-1)", and Verify stayed at "verifying…". Now:
  * an address is opened only once /node.json says a Stump is there; anything
    else says what's there instead (a router's page, nothing listening);
  * the same Stump found at a new address retires its old entry at once;
  * a line that never reached the node is drawn "not sent" with the network's
    reason, never "too fast" (only a ⧗ from the node means that);
  * a /auth that can't be sent ends Verify at once, with the reason;
  * a Wi-Fi Stump that stops answering says why ("connection refused").
"""
import json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
from fake_stump import FakeRRC, rns_verify, serve_http
from firefly import api

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    fails += 0 if ok else 1

def wait(cond, secs=15):
    t = time.time()
    while time.time() - t < secs:
        if cond():
            return True
        time.sleep(0.2)
    return False

# A home router: a web page on every path.
class Router(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _page(self):
        body = b"<html><body>Router login</body></html>"
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    do_GET = do_POST = _page
srv = ThreadingHTTPServer(("127.0.0.1", 47640), Router)
threading.Thread(target=srv.serve_forever, daemon=True).start()

LXMF = "c0ffee41a6b23b7a6108eb76cce3e8dd"
rrc = FakeRRC(rns_verify); rrc.node_lxmf = LXMF
serve_http(47641, rrc, node={"name": "macOS", "lxmf": LXMF, "version": "1.0.0", "release": "1.0.0-beta.20",
                             "ap_ip": None, "chain": None, "features": ["chat"]})
ROUTER, STUMP, NOTHING = "http://127.0.0.1:47640", "http://127.0.0.1:47641", "http://127.0.0.1:47642"

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-gw-"), None, None, L())
c = api._core

def keys():
    return [k for r in json.loads(api.stump_nodes()) for k in [r["key"]] + [o["key"] for o in r["others"]]]

# 1. The gateway guess is checked.
p = json.loads(api.discover_probe(ROUTER))
check("the router isn't taken for a Stump", p.get("stump") is False, p)
r = json.loads(api.stump_connect_checked(ROUTER))
check("connecting to the router says what's there, and opens nothing",
      "key" not in r and "isn't a Stump" in r.get("error", "") and not keys(), r)
r = json.loads(api.stump_connect_checked(NOTHING))
check("nothing listening: says so", "key" not in r and "refused" in r.get("error", ""), r)

# 2. A real Stump opens, and replaces its entry at an older address.
c.store.upsert_stump_node("wifi:http://127.0.0.1:47643", "wifi", "http://127.0.0.1:47643", name="macOS",
                          lxmf=LXMF, online=0, last_seen=time.time() - 60)
r = json.loads(api.stump_connect_checked(STUMP))
check("a Stump opens, with its name", r.get("key") == "wifi:" + STUMP and r.get("name") == "macOS", r)
check("...and its entry at the old address is gone at once", "wifi:http://127.0.0.1:47643" not in keys(), keys())

# 3. A Stump that doesn't answer (opened without the check, like an old saved entry).
dead = api.stump_connect_wifi(NOTHING)
check("its view says why it's unreachable",
      wait(lambda: "refused" in (json.loads(api.stump_view(dead)).get("offline_reason") or "")),
      json.loads(api.stump_view(dead)).get("offline_reason"))
api.stump_post(dead, "hello there")
def line():
    return next((l for l in json.loads(api.stump_view(dead))["lines"] if l.get("mine") and l["body"] == "hello there"), None)
check("a line that never reached it is drawn 'not sent' (error), not 'too fast'",
      wait(lambda: line() is not None) and line()["state"] == "error" and line()["local_state"] == "error", line())
replies = [l["body"] for l in json.loads(api.stump_view(dead))["lines"] if l["kind"] == "reply"]
check("...with the reason beside it, not a bare -1",
      any(b.startswith("not sent:") and "refused" in b for b in replies) and not any("(-1)" in b for b in replies), replies)
api.stump_auth(dead)
v = lambda: json.loads(api.stump_view(dead))
check("Verify ends at once when /auth can't be sent", wait(lambda: v()["auth"] == "failed", 10) and "refused" in v()["auth_detail"],
      (v()["auth"], v()["auth_detail"]))

# 4. The real Stump still works the same.
api.stump_post("wifi:" + STUMP, "hi from the phone")
check("a line to the real Stump arrives", wait(lambda: any("hi from the phone" in str(m) for m in rrc.log)) if hasattr(rrc, "log")
      else wait(lambda: any(l.get("mine") and l["body"] == "hi from the phone" and l.get("state") not in ("failed", "error")
                            for l in json.loads(api.stump_view("wifi:" + STUMP))["lines"])))

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
