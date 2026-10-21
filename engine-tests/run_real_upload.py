"""An upload to a real computer Stump (1.0.0-beta.22+ accepts them), as FireFly sends it.

    python3 run_real_upload.py /path/to/SandBox_Stump_On

Before beta.22 a computer Stump refused every upload with 507. This sends the
request FireFly's Kotlin upload sends (raw POST /upload, X-Filename as UTF-8,
Accept: application/json), checks the JSON answer fields FireFly reads (ok,
name, message), then lists the file through FireFly's engine (/files.json).
Skipped (exit 0) without the Stump folder.
"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
STUMP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STUMP_REPO", "")
if not STUMP or not os.path.exists(os.path.join(STUMP, "pc", "stump_pc.py")):
    print("SKIP: give the SandBox_Stump_On folder"); sys.exit(0)
PORT, IP = 8095, "127.0.0.1"
fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:400]})" if detail and not ok else ""))
    fails += 0 if ok else 1

sys.path.insert(0, STUMP)
import builtins; builtins.input = lambda *a: ""
import provisioner as P
work = tempfile.mkdtemp(prefix="ff-up-stump-"); folder = os.path.join(work, "Stump"); os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(STUMP, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += "\nNODE_NAME = 'macOS'\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nPC_STORAGE_MB = 64\n" % (PORT, IP)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
t0 = time.time(); up = False
while time.time() - t0 < 60 and not up:
    try: up = bool(op.open(f"http://{IP}:{PORT}/node.json", timeout=3).read())
    except Exception: time.sleep(1)
check("the real Stump starts", up)

def upload(name, data):
    """FireFly's rawUpload, byte for byte (WifiHttp.kt)."""
    s = socket.create_connection((IP, PORT), 8); s.settimeout(60)
    head = (f"POST /upload HTTP/1.1\r\nHost: {IP}:{PORT}\r\nContent-Type: application/octet-stream\r\n"
            f"Accept: application/json\r\nX-Filename: {name}\r\nContent-Length: {len(data)}\r\n"
            "Connection: close\r\n\r\n").encode("utf-8")
    sent_err = None
    try:
        s.sendall(head + data)
    except OSError as e:
        sent_err = e                    # the Stump may have answered early and closed: read it (as FireFly does)
    raw = b""
    try:
        while True:
            b = s.recv(65536)
            if not b: break
            raw += b
    except OSError:
        pass
    s.close()
    if not raw:
        return -1, f"no answer ({sent_err})"
    headers, _, body = raw.partition(b"\r\n\r\n")
    return int(headers.split()[1]), body.decode("utf-8", "replace")

if up:
    status, body = upload("carte été.txt", b"hello from FireFly\n" * 500)
    ans = json.loads(body) if body.startswith("{") else {}
    check("a computer Stump takes the upload (no 507)", status in (200, 201) and ans.get("ok") is True, (status, body))
    check("...its JSON answer has what FireFly reads", "name" in ans and "message" in ans, ans)
    from firefly import stump_web
    from firefly.stump_session import UrllibHttp
    files = stump_web.files(UrllibHttp(), f"http://{IP}:{PORT}")
    names = [f["name"] for f in files.get("files", [])]
    check("...and FireFly's Files list shows it, accents intact", any("carte été" in n for n in names), names)
    status, body = upload("big.bin", os.urandom(60 * 1024 * 1024))
    ans = json.loads(body) if body.startswith("{") else {}
    check("past 75% of PC_STORAGE_MB: 507, with a message FireFly shows",
          status == 507 and ans.get("error") == "full" and ans.get("message"), (status, body[:200]))
node.terminate()
try: log = node.communicate(timeout=20)[0]
except subprocess.TimeoutExpired: node.kill(); log = node.communicate()[0]
check("no tracebacks on the Stump", "Traceback" not in log, log[log.find("Traceback"):][:1200])
if "Traceback" in log: print("STUMPLOG", log[max(0, log.find("Traceback") - 300):][:2500])
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
