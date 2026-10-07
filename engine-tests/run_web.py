"""Billboard and file shelf over Wi-Fi, against the fake node's HTTP front.

    python3 run_web.py

Checks the engine side (firefly/stump_web.py): reading the board, posting and
proving the post landed by reading the board back (/post always answers 303),
a silently dropped empty post, the shelf, the download URL's UTF-8 encoding,
and every feature-off 404 reading as "off". Upload and download bodies are
streamed by the app in Kotlin; the same requests are made here with urllib so
the fake node's answers (200 with balance, 400, 503, 507, 404) are checked.
"""
import json, os, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from fake_stump import FakeRRC, FakeWeb, rns_verify, serve_http
from firefly import stump_web
from firefly.stump_session import UrllibHttp

PORT = 47590
BASE = f"http://127.0.0.1:{PORT}"
web = FakeWeb()
serve_http(PORT, FakeRRC(rns_verify), web)
time.sleep(0.3)
http = UrllibHttp()
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    fails += 0 if ok else 1


def raw(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


# ---------------------------------------------------------------- billboard
b = stump_web.board(http, BASE)
check("board reads", b["state"] == "ok" and len(b["posts"]) == 2, b)
first = b["posts"][0]
check("post with an id has a time and expiry", first["posted"] and 0 < first["expires_in"] <= 72 * 3600, first)
check("post without an id is kept, no time", b["posts"][1]["id"] is None and b["posts"][1]["posted"] is None)

r = stump_web.board_post(http, BASE, "Vélo à vendre\nbon état", "Rouge, 3 vitesses.\nPrix: 40 $ + casque")
check("post lands, proven by refetch", r["ok"], r)
top = r["board"]["posts"][0]
check("title line break became a space", top["title"] == "Vélo à vendre bon état", top["title"])
check("body keeps line breaks, + and $ survive", top["body"] == "Rouge, 3 vitesses.\nPrix: 40 $ + casque", repr(top["body"]))

r = stump_web.board_post(http, BASE, "x" * 120, "y" * 900)
check("long title/body are cut to 80/600", r["ok"] and len(r["board"]["posts"][0]["title"]) == 80
      and len(r["board"]["posts"][0]["body"]) == 600)

r = stump_web.board_post(http, BASE, "   \n ", "")
check("empty title refused before sending", not r["ok"] and r["reason"] == "empty")

# A post the node drops silently (its own rules) is reported as dropped, not as success.
orig = web.post
web.post = lambda form: None
r = stump_web.board_post(http, BASE, "this one vanishes", "")
web.post = orig
check("silently dropped post is detected", not r["ok"] and r["reason"] == "dropped", r)

# ---------------------------------------------------------------- files
f = stump_web.files(http, BASE)
names = {x["name"]: x for x in f.get("files", [])}
check("shelf reads", f["state"] == "ok" and f["credits"] and "photo.jpg" in names, f)
check("class and cost", names["photo.jpg"]["class"] == "other" and names["photo.jpg"]["cost"] == 1)

url = stump_web.download_url(BASE, "été à Montréal.txt")
check("download URL percent-encodes UTF-8", "%C3%A9t%C3%A9%20%C3%A0%20Montr%C3%A9al.txt" in url, url)
st, data, hd = raw("GET", url[len(BASE):])
check("download streams the file", st == 200 and data == "bonjour l'été\n".encode()
      and hd.get("Content-Length") == str(len(data)), (st, hd))
st, _, _ = raw("GET", stump_web.download_url(BASE, "nope.bin")[len(BASE):])
check("missing file is 404", st == 404)

song = os.urandom(70_000)
st, body, _ = raw("POST", "/upload", song, {"X-Filename": "chanson: ete?.mp3", "Content-Length": str(len(song))})
check("upload answers with the balance", st == 200 and body.decode().startswith("Uploaded. Your balance:"), (st, body))
f = stump_web.files(http, BASE)
names = {x["name"]: x for x in f["files"]}
check("uploaded name sanitized, music weight 2", "chanson_ ete_.mp3" in names and names["chanson_ ete_.mp3"]["cost"] == 2
      and names["chanson_ ete_.mp3"]["class"] == "music", list(names))
check("cost estimate matches the node", stump_web.upload_cost("x.MP4") == 3 and stump_web.upload_cost("x.pdf") == 1
      and stump_web.upload_cost("x") == 1 and stump_web.upload_cost("x.mp3", credits=False) == 0)
st, _, _ = raw("POST", "/upload", b"", {"X-Filename": "empty.txt", "Content-Length": "0"})
check("empty upload is 400", st == 400)
os.environ["FAKE_STUMP_NO_SD"] = "1"
st, _, _ = raw("POST", "/upload", b"abc", {"X-Filename": "a.txt", "Content-Length": "3"})
check("no SD card is 503", st == 503)
del os.environ["FAKE_STUMP_NO_SD"]
os.environ["FAKE_STUMP_FULL"] = "1"
st, _, _ = raw("POST", "/upload", b"abc", {"X-Filename": "a.txt", "Content-Length": "3"})
check("full card is 507", st == 507)
del os.environ["FAKE_STUMP_FULL"]

# The app writes /upload by hand on a socket so the filename goes as UTF-8 bytes
# (Android won't put non-ASCII in a header): the same bytes, here.
import socket
def raw_socket_upload(name, data):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    head = (f"POST /upload HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nContent-Type: application/octet-stream\r\n"
            f"X-Filename: {name}\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode("utf-8")
    s.sendall(head + data)
    resp = b""
    while True:
        chunk = s.recv(4096)
        if not chunk: break
        resp += chunk
    s.close()
    status_line, _, rest = resp.partition(b"\r\n")
    return int(status_line.split()[1]), rest.split(b"\r\n\r\n", 1)[1]

st, body = raw_socket_upload("Fête de l'été: 2026.pdf", b"%PDF-1.4 hello")
f = stump_web.files(http, BASE)
check("UTF-8 filename stored exactly (barkeep rules: quote dropped, colon _)", st == 200
      and "Fête de lété_ 2026.pdf" in {x["name"] for x in f["files"]}, (st, body, [x["name"] for x in f["files"]]))
st, data, _ = raw("GET", stump_web.download_url(BASE, "Fête de lété_ 2026.pdf")[len(BASE):])
check("and downloads back by its UTF-8 name", st == 200 and data == b"%PDF-1.4 hello")

# What a browser or urllib does with é in a header: one Latin-1 byte. barkeep's
# line.decode() fails on it and the request ends with no answer at all. The app
# never sends that; this documents why (and is in the notes for the Stump team).
try:
    raw("POST", "/upload", b"abc", {"X-Filename": "été.txt", "Content-Length": "3"})
    latin1_answered = True
except Exception:
    latin1_answered = False
check("a Latin-1 filename header gets no answer (barkeep behaviour, mirrored)", not latin1_answered)

# ---------------------------------------------------------------- features off
os.environ["FAKE_STUMP_NO_BOARD"] = "1"
os.environ["FAKE_STUMP_NO_FILES"] = "1"
check("board off reads as off", stump_web.board(http, BASE)["state"] == "off")
check("posting to an off board says off", stump_web.board_post(http, BASE, "hi")["reason"] == "off")
check("files off reads as off", stump_web.files(http, BASE)["state"] == "off")
del os.environ["FAKE_STUMP_NO_BOARD"], os.environ["FAKE_STUMP_NO_FILES"]

check("unreachable node reads as unreachable",
      stump_web.board(http, "http://127.0.0.1:1")["state"] == "unreachable")

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
