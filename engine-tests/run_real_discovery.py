"""FireFly's discovery against a real computer Stump (Stump 1.0.0-beta.21+).

    python3 run_real_discovery.py /path/to/SandBox_Stump_On

Starts the Stump's own stump_pc.py (as its tests/pc/discovery_test.py does)
and checks, with FireFly's engine and its desktop HTTP/UDP transport:
  * STUMP? on UDP 4244 gets a card FireFly reads (name, lxmf, a base with the
    port the Stump really serves on);
  * locate() finds it by identity;
  * a saved Wi-Fi entry at an address where nothing answers is followed to the
    Stump's real address, with its history, and chat works there;
  * the Stump's beacon data (five items) is read by FireFly's beacon parser.
Skipped (exit 0) when no Stump folder is given or found.
"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))

STUMP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STUMP_REPO", "")
if not STUMP or not os.path.exists(os.path.join(STUMP, "pc", "stump_pc.py")):
    print("SKIP: give the SandBox_Stump_On folder (argument or STUMP_REPO)")
    sys.exit(0)

PORT, IP = 8094, "127.0.0.1"
os.environ["FIREFLY_BROADCAST"] = IP          # "the whole Wi-Fi" is this one host here
fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:400]})" if detail and not ok else ""))
    fails += 0 if ok else 1

def wait(cond, secs=20):
    t = time.time()
    while time.time() - t < secs:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False

# --- a real Stump-PC, set up the way the Stump's own test does it
sys.path.insert(0, STUMP)
import builtins
builtins.input = lambda *a: ""
import provisioner as P
work = tempfile.mkdtemp(prefix="ff-real-stump-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(STUMP, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += "\nNODE_NAME = 'macOS'\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\n" % (PORT, IP)
cfg += "\nAPP_HELLO = True\n"            # the Stump's own tests switch the greeting off; a real one has it on
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
up = wait(lambda: opener.open(f"http://{IP}:{PORT}/node.json", timeout=3).read(), 60)
check("the real Stump starts", up)
if not up:
    node.kill(); sys.exit(1)
node_json = json.loads(opener.open(f"http://{IP}:{PORT}/node.json", timeout=5).read())
LXMF = node_json.get("lxmf")
time.sleep(2)

from firefly import discovery, api
from firefly.stump_session import UrllibHttp
http = UrllibHttp()

cards = []
wait(lambda: bool(cards.extend(discovery.ask(http)) or cards), 10)
c = next((x for x in cards if x["lxmf"] == LXMF), None)
check("STUMP? (broadcast): FireFly reads the real Stump's card", c is not None and c["name"] == "macOS", cards)
check("...its base is where it really serves, port included", c and c["base"] == f"http://{IP}:{PORT}", c)
check("locate() finds it by identity", (discovery.locate(http, LXMF) or {}).get("base") == f"http://{IP}:{PORT}")
found = discovery.find_all(http, own_ip=None, sweep_too=False, samples=2)
check("Find Stumps lists it, by answer", any(f.get("lxmf") == LXMF and f.get("found_by") == "answer" for f in found), found)

# --- the field case: a saved Wi-Fi entry at the laptop's old address
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-real-"), None, None, L())
core = api._core
OLD = "http://127.0.0.1:47699"                    # nothing answers here
old_key = "wifi:" + OLD
core.store.upsert_stump_node(old_key, "wifi", OLD, name="macOS", lxmf=LXMF, online=0, last_seen=time.time() - 60)
core.store.add_line(old_key, "main", "reply", "an old line from before the move")
api.stump_post(old_key, "hello after the move")
new_key = f"wifi:http://{IP}:{PORT}"
check("a send that fails finds the Stump at its new address", wait(lambda: core.resolve(old_key) == new_key, 30),
      core.resolve(old_key))
rows = json.loads(api.stump_nodes())
keys = [k for r in rows for k in [r["key"]] + [o["key"] for o in r["others"]]]
check("...one entry, at the new address", new_key in keys and old_key not in keys, keys)
lines = lambda: json.loads(api.stump_view(new_key))["lines"]
check("...the history came along", any("an old line from before the move" in l["body"] for l in lines()))
check("...it says it found it", any(l["body"].startswith("found macOS at its new address") for l in lines()),
      [l["body"] for l in lines()])
check("...and the line that failed arrived there",
      wait(lambda: "hello after the move" in opener.open(f"http://{IP}:{PORT}/rrc/poll?room=main&since=0", timeout=5).read().decode()),
      "")

check("FireFly verified itself there on its own (/auth on connecting, Stump 1.0.0-beta.25)",
      wait(lambda: (core.store.stump_node(new_key) or {}).get("auth") == "ok", 30),
      {k: (core.store.stump_node(new_key) or {}).get(k) for k in ("auth", "auth_detail")})
check("...quietly", not any(l["body"].startswith("AUTH-") for l in lines()), [l["body"] for l in lines()])

# The greeting (Stump 1.0.0-beta.31): this test shares the phone's address, so its own poll
# reads what the Stump thinks of FireFly.
from firefly import stump as _stump
want = _stump.clean_nick(core.settings["display_name"])
def as_firefly():
    return json.loads(opener.open(f"http://{IP}:{PORT}/rrc/poll?room=main&since=0", timeout=5).read())
check("the real Stump has FireFly's greeting: 'hello': 'done', under its own name",
      wait(lambda: as_firefly().get("hello") in ("done", None) and as_firefly().get("nick") == want, 30),
      {k: as_firefly().get(k) for k in ("hello", "nick", "topic")})

check("...through /hello (Stump 1.0.0-beta.32), not the /auth + /nick fallback",
      getattr(core.sessions.get(new_key), "hello_v2", False))

# --- its beacon, as the Stump builds it
sys.path[:0] = [os.path.join(STUMP, "pc", "pc_compat"), os.path.join(STUMP, "final_firmware")]
try:
    import stump_host
    stump_host.install(os.path.join(STUMP, "final_firmware"))
    import discovery as stump_discovery
    from firefly import stump
    data = stump_discovery.beacon_data("1.0.0-beta.21", "macOS", bytes.fromhex(LXMF))
    info = stump.parse_beacon(data)
    check("FireFly reads the five-item beacon's first four as before", info is not None and info[0] == LXMF, info)
    check("...and its addresses", stump.beacon_http(data) and all(u.startswith("http://") for u in stump.beacon_http(data)),
          stump.beacon_http(data))
except Exception as e:
    check("the Stump's beacon builder", False, repr(e))

node.terminate()
try:
    log = node.communicate(timeout=20)[0]
except subprocess.TimeoutExpired:
    node.kill(); log = node.communicate()[0]
check("no tracebacks on the Stump", "Traceback" not in log, log[log.find("Traceback"):][:1200])
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
