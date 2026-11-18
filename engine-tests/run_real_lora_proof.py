"""No /auth over LoRa with a real Stump 1.0.0-beta.71+ (Nov 14).

    python3 run_real_lora_proof.py /path/to/SandBox_Stump_On

The Stump's own stump_pc.py and FireFly's engine, joined through a Reticulum hub over TCP
(no radio, same LXMF). FireFly hears the Stump's beacon, announces itself, enters it without
sending /auth, and the Stump's /whoami says it's verified as FireFly's identity: its signed
messages are the proof. On an older Stump FireFly still sends /auth (checked too).
Skipped (exit 0) without the Stump folder.
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
STUMP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STUMP_REPO", "")
if not STUMP or not os.path.exists(os.path.join(STUMP, "pc", "stump_pc.py")):
    print("SKIP: give the SandBox_Stump_On folder"); sys.exit(0)
PORT, IP, HUB = 8097, "127.0.0.1", 4301
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

work = tempfile.mkdtemp(prefix="ff-lora-proof-")
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
cfg += "\nMESH_PEER_TIMEOUT = 45   # (5 min on a real Stump) to see it let a quiet LoRa client go\n"
cfg += "\nNODE_NAME = 'LaBuche'\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\n" % (PORT, IP)
cfg += ("CONFIG['interfaces'] = list(CONFIG['interfaces']) + [{'type': 'TCPClientInterface', 'name': 'hub', "
        "'enabled': True, 'target_host': '127.0.0.1', 'target_port': %d}]\n" % HUB)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
check("the real Stump starts", wait(lambda: op.open(f"http://{IP}:{PORT}/node.json", timeout=3).read(), 60))
try:
    STUMP_REL = int(str(json.loads(op.open(f"http://{IP}:{PORT}/node.json", timeout=5).read()).get("release", "")).rsplit(".", 1)[-1])
except Exception:
    STUMP_REL = 0
LX = json.loads(op.open(f"http://{IP}:{PORT}/node.json", timeout=5).read())["lxmf"]

from firefly import api, stump, ztext
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-lp-"), None, None, L(), "0.6.1-beta.72", 72)
c = api._core
c.settings.update({"auto_interface": False, "tcp_peers": [f"127.0.0.1:{HUB}"]})
c.reattach_optional_interfaces()
import RNS
RNS.Transport.request_path(bytes.fromhex(LX))
print("     path to the Stump known before sending:", wait(lambda: RNS.Transport.has_path(bytes.fromhex(LX)), 20),
      "(LXMF asks for one itself if not)", flush=True)

K = "mesh:" + LX
# Its QR code's link (the /address page), as a phone would scan it: from beta.79 with &r=.
import re as _re, urllib.request as _ur
try:
    page = _ur.urlopen(f"http://{IP}:{PORT}/address", timeout=10).read().decode("utf-8", "replace")
except Exception as e:
    page = ""
    print("     /address:", e, flush=True)
m = _re.search(r"(lxmf://[0-9a-f]{32}\?[^'\"<\s]+)", page)
link = m.group(1).replace("&amp;", "&") if m else None
print("     its QR link:", (link or "none")[:60] + "…" + ((link or "")[-24:]), flush=True)
a = stump.parse_address_link(link) if link else None
check("FireFly reads the Stump's QR link: its address and key",
      a is not None and a["address"] == LX and a["key"] is not None and not a["key_bad"], a)
if STUMP_REL >= 79:
    check("...and from beta.79 its release (&r=)", a is not None and stump.release_beta(a["release"]) == STUMP_REL, a and a["release"])
check("FireFly hears the real Stump's beacon", wait(lambda: LX in c.stumps, 60), list(c.stumps)[:3])
print("     the Stump's release:", c.stumps.get(LX), "| boot:", c.boots.get(LX), flush=True)
check("...and introduces itself (its announce, so the Stump has its key)", c.last_announce > 0)
c.store.upsert_stump_node(K, "mesh", LX, name="LaBuche", last_seen=time.time())
sess = c.session(K)
sent = []
real_send = sess.pipe.send
def spy(line, control=False):
    sent.append(line)
    return real_send(line, control) if control else real_send(line)
sess.pipe.send = spy
_real_in = sess.on_mesh_content
def _trace(content, *a, **k):
    if os.environ.get("TRACE"):
        for ln in str(content).splitlines():
            print("     <<", repr(ln[:120]), "| room now:", sess.room, flush=True)
    return _real_in(content, *a, **k)
sess.on_mesh_content = _trace
lines = lambda: [l["body"] for r in ("main", "lora-mesh") for l in json.loads(api.stump_view(K, r))["lines"]]
t = time.time()
g = json.loads(api.stump_verify(K, True))      # entering, as the app does
if STUMP_REL >= 82:
    # Packed text (Stump beta.82, FireFly 0.6.2): its LXMF announce says it reads it, so what
    # FireFly sends it goes packed, and what it sends back comes packed, read here.
    check("beta.82+: its announce says it reads packed text", wait(lambda: LX in c.packs, 60), sorted(c.packs)[:3])
    _packed = []
    _real_pack = ztext.pack
    def _spy_pack(b):
        out = _real_pack(b); _packed.append((b, out)); return out
    ztext.pack = _spy_pack
    _real_unpack = ztext.unpack
    _unpacked = []
    def _spy_unpack(raw):
        out = _real_unpack(raw); _unpacked.append(out); return out
    ztext.unpack = _spy_unpack
if STUMP_REL >= 71:
    check("beta.71+: in at once, no /auth sent", g["state"] == "ok" and "/auth" not in sent, (g, sent))
    time.sleep(3)
    sess.post("/whoami")
    got = wait(lambda: any(b.startswith(("verified as", "not verified")) for b in lines()), 180)
    who = [b for b in lines() if b.startswith(("verified as", "not verified"))]
    print(f"     /whoami: {who[-1:] if who else 'no answer'} in {round(time.time() - t, 1)}s", flush=True)
    check("the Stump's /whoami: verified as FireFly's identity, no /auth ever sent",
          got and who[-1].startswith("verified as " + c.identity.hash.hex()) and "/auth" not in sent, (who, sent))
    if STUMP_REL >= 82:
        print(f"     packed: {len(_packed)} sent ({sum(len(x) for x, _ in _packed)} -> {sum(len(y) for _, y in _packed)} bytes),"
              f" {len(_unpacked)} received", flush=True)
        check("...what FireFly sent it went packed (and the Stump understood it)", any(x == b"/whoami" for x, _ in _packed), _packed[:3])
        check("...what it sent back came packed, and FireFly read it", any(o.startswith(b"verified as") for o in _unpacked), _unpacked[:3])
    # Entering said "I'm here": /names went out, and its answer came back.
    check("entering sent /names (the Stump keeps a LoRa client 5 min after its last message)",
          "/names" in sent, sent)
    check("...its answer arrived", wait(lambda: any(b.startswith("#") and ":" in b for b in lines()), 120), lines()[-4:])
    # Away in #main, then quiet past the Stump's timeout (45 s here): it lets FireFly go.
    sess.post("/join main")
    check("joined #main", wait(lambda: stump.same_room(sess.room or "", "main"), 120), sess.room)
    sess.pipe.sent_at = 0                      # (not the real clock's 4 minutes: the test's 45 s)
    time.sleep(70)
    n = len(sent)
    json.loads(api.stump_verify(K, True))
    check("entering after the timeout: /names again", sent[n:n + 1] == ["/names"], sent[n:])
    check("...and its answer arrived: FireFly in the room the Stump has it in, nothing else sent",
          wait(lambda: sess.here_room is None, 120) and "/join lora-mesh" not in sent[n:], (sess.room, sent[n:]))
else:
    check("an older Stump: FireFly still sends /auth", "/auth" in sent, sent)

node.terminate(); hub.terminate()
try: log = node.communicate(timeout=20)[0]
except subprocess.TimeoutExpired: node.kill(); log = node.communicate()[0]
open(os.path.join(work, "stump.log"), "w").write(log)
print("     Stump log:", os.path.join(work, "stump.log"))
check("no tracebacks on the Stump", "Traceback" not in log, log[log.find("Traceback"):][:1500])
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
