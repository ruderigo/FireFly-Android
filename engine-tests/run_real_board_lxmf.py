"""/post and /board to a real Stump over Reticulum, as FireFly's LoRa side sends them (Oct 24).

    python3 run_real_board_lxmf.py /path/to/SandBox_Stump_On

Field report: a full-size notice (title 80, details 600) posted over LoRa hung.
At ~700 bytes it doesn't fit one packet, so LXMF opens a link and sends it as a
Resource. This runs the Stump's own stump_pc.py and FireFly's engine, joined
through a Reticulum hub over TCP (no radio, same LXMF), and posts a short notice
and a full-size one, then reads the full one back with /board <id>. It reports
how each went, and how long it took.
Skipped (exit 0) without the Stump folder.
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
STUMP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STUMP_REPO", "")
if not STUMP or not os.path.exists(os.path.join(STUMP, "pc", "stump_pc.py")):
    print("SKIP: give the SandBox_Stump_On folder"); sys.exit(0)
PORT, IP, HUB = 8096, "127.0.0.1", 4299
fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:500]})" if detail and not ok else ""), flush=True)
    fails += 0 if ok else 1
def wait(cond, secs):
    t = time.time()
    while time.time() - t < secs:
        try:
            if cond(): return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

work = tempfile.mkdtemp(prefix="ff-board-lxmf-")
hub_src = os.path.join(work, "hub.py")
open(hub_src, "w").write(open(os.path.join(HERE, "rns_hub.py")).read())
hub = subprocess.Popen([sys.executable, "-u", hub_src, os.path.join(work, "hub"), str(HUB)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
check("the Reticulum hub starts", wait(lambda: "hub up" in (hub.stdout.readline() or ""), 20))

sys.path.insert(0, STUMP)
import builtins; builtins.input = lambda *a: ""
import provisioner as P
folder = os.path.join(work, "Stump"); os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(STUMP, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += "\nNODE_NAME = 'LaBuche'\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\n" % (PORT, IP)
cfg += ("CONFIG['interfaces'] = list(CONFIG['interfaces']) + [{'type': 'TCPClientInterface', 'name': 'hub', "
        "'enabled': True, 'target_host': '127.0.0.1', 'target_port': %d}]\n" % HUB)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
check("the real Stump starts", wait(lambda: op.open(f"http://{IP}:{PORT}/node.json", timeout=3).read(), 60))
LX = json.loads(op.open(f"http://{IP}:{PORT}/node.json", timeout=5).read())["lxmf"]

from firefly import api
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-bl-"), None, None, L())
c = api._core
c.settings.update({"auto_interface": False, "tcp_peers": [f"127.0.0.1:{HUB}"]})
c.reattach_optional_interfaces()
import RNS
RNS.Transport.request_path(bytes.fromhex(LX))
print("     path to the Stump known before sending:", wait(lambda: RNS.Transport.has_path(bytes.fromhex(LX)), 20),
      "(LXMF asks for one itself if not)", flush=True)
K = "mesh:" + LX
c.store.upsert_stump_node(K, "mesh", LX, name="LaBuche", last_seen=time.time())
view = lambda: json.loads(api.stump_board_lora_view(K))
board_http = lambda: json.loads(op.open(f"http://{IP}:{PORT}/billboard.json", timeout=5).read())["posts"]

def post(title, body, secs=180):
    t = time.time()
    api.stump_board_lora_post(K, title, body)
    ok = wait(lambda: view()["post"].get("state") in ("ok", "refused"), secs)
    return ok, view()["post"], round(time.time() - t, 1)

ok, p, s = post("Soupe ce soir", "18 h chez Rod")
print(f"     short post: {p} in {s}s ({len(('/post Soupe ce soir | 18 h chez Rod').encode())} B)", flush=True)
check("a short /post over Reticulum is answered ✓", ok and p.get("state") == "ok", p)

T = "T" * 80
B = ("0110" * 150)[:600]
ok, p, s = post(T, B)
size = len(f"/post {T} | {B}".encode())
print(f"     full post: {p} in {s}s ({size} B: more than one packet, so a link and a Resource)", flush=True)
check("a full-size /post (title 80, details 600) is answered ✓", ok and p.get("state") == "ok", p)
on_board = [x for x in board_http() if x.get("title") == T]
check("...and is on the billboard, with all 600 characters", on_board and len(on_board[0].get("body", "")) == 600,
      [(x.get("title", "")[:20], len(x.get("body", ""))) for x in board_http()][:5])
if p.get("id"):
    t = time.time()
    api.stump_board_lora_item(K, p["id"])
    got = wait(lambda: p["id"] in view()["items"], 180)
    it = view()["items"].get(p["id"], {})
    print(f"     /board {p['id']}: {'answered' if got else 'no answer'} in {round(time.time() - t, 1)}s", flush=True)
    check("/board <id> brings its 600 characters back (the Stump sends a Resource too)", it.get("body") == B,
          (len(it.get("body", "")), it.get("title", "")[:20]))

node.terminate(); hub.terminate()
try: log = node.communicate(timeout=20)[0]
except subprocess.TimeoutExpired: node.kill(); log = node.communicate()[0]
open(os.path.join(work, "stump.log"), "w").write(log)
print("     Stump log:", os.path.join(work, "stump.log"))
check("no tracebacks on the Stump", "Traceback" not in log, log[log.find("Traceback"):][:1500])
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
