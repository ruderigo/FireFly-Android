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
check("post lands: the node's 201 answer, with the post's id", r["ok"] and r.get("id")
      and r["board"]["posts"][0]["id"] == r["id"], r)
top = r["board"]["posts"][0]
check("title line break became a space", top["title"] == "Vélo à vendre bon état", top["title"])
check("body keeps line breaks, + and $ survive", top["body"] == "Rouge, 3 vitesses.\nPrix: 40 $ + casque", repr(top["body"]))

r = stump_web.board_post(http, BASE, "x" * 120, "y" * 900)
check("long title/body are cut to 80/600", r["ok"] and len(r["board"]["posts"][0]["title"]) == 80
      and len(r["board"]["posts"][0]["body"]) == 600)

r = stump_web.board_post(http, BASE, "   \n ", "")
check("empty title refused before sending", not r["ok"] and r["reason"] == "empty")

# The node's own refusals, as data: 400 title_required, 507 storage.
orig = web.post
web.post = lambda form: (False, "title_required")
r = stump_web.board_post(http, BASE, "the node says no title", "")
check("400 title_required from the node reads as 'empty'", not r["ok"] and r["reason"] == "empty", r)
web.post = orig
os.environ["FAKE_STUMP_POST_FULL"] = "1"
r = stump_web.board_post(http, BASE, "card full", "")
check("507 storage: not posted, reason 'storage'", not r["ok"] and r["reason"] == "storage", r)
del os.environ["FAKE_STUMP_POST_FULL"]

# An older node (Oct 7 stable) answers 303 whatever happened: proven by reading the board back.
os.environ["FAKE_STUMP_OLD"] = "1"
r = stump_web.board_post(http, BASE, "old node post", "still works")
check("old node: post proven by refetch", r["ok"] and r["board"]["posts"][0]["title"] == "old node post", r)
web.post = lambda form: (False, "title_required")
r = stump_web.board_post(http, BASE, "this one vanishes", "")
web.post = orig
check("old node: a silently dropped post is detected", not r["ok"] and r["reason"] == "dropped", r)
del os.environ["FAKE_STUMP_OLD"]

os.environ["FAKE_STUMP_POST_LIMIT"] = "1"
r = stump_web.board_post(http, BASE, "one more", "")
check("429 rate_limited: not posted, reason 'rate_limited'", not r["ok"] and r["reason"] == "rate_limited", r)
del os.environ["FAKE_STUMP_POST_LIMIT"]
b = stump_web.board(http, BASE, q="velo")
check("board search: 'velo' finds 'Vélo à vendre…' (accents ignored)", [p["title"] for p in b["posts"]] ==
      ["Vélo à vendre bon état"], [p["title"] for p in b["posts"]])
os.environ["FAKE_STUMP_OLD"] = "1"
b = stump_web.board(http, BASE, q="velo")
check("...an older node ignores ?q=: FireFly filters itself", [p["title"] for p in b["posts"]] == ["Vélo à vendre bon état"])
del os.environ["FAKE_STUMP_OLD"]

# ---------------------------------------------------------------- files
f = stump_web.files(http, BASE)
names = {x["name"]: x for x in f.get("files", [])}
check("shelf reads", f["state"] == "ok" and f["credits"] and "photo.jpg" in names, f)
check("class and cost", names["photo.jpg"]["class"] == "other" and names["photo.jpg"]["cost"] == 1)

f = stump_web.files(http, BASE, q="montreal")
check("files search: 'montreal' finds 'été à Montréal.txt'", [x["name"] for x in f["files"]] == ["été à Montréal.txt"], f)
f = stump_web.files(http, BASE, cat="image")
check("files ?cat=image: the photo, with cat and mtime", [x["name"] for x in f["files"]] == ["photo.jpg"]
      and f["files"][0]["cat"] == "image" and f["files"][0]["mtime"], f)
os.environ["FAKE_STUMP_OLD"] = "1"
f = stump_web.files(http, BASE, q="montreal")
check("...an older node ignores ?q=: FireFly filters itself", [x["name"] for x in f["files"]] == ["été à Montréal.txt"])
del os.environ["FAKE_STUMP_OLD"]

# Names from another Stump of a chain (#wifi-mesh): nick@thatstump, in rooms and DMs.
from firefly import stump as stump_proto
pm = stump_proto.parse_message("<rod@LaBuche> hi from the chain\n[DM] <rod@LaBuche>: psst")
check("nick@stump in a room line and a DM", [(x["kind"], x.get("nick") or x.get("author")) for x in pm] ==
      [("msg", "rod@LaBuche"), ("dm", "rod@LaBuche")], pm)
check("room aliases: lxmf and lora are lora-mesh, mesh is wifi-mesh", stump_proto.same_room("lxmf", "#lora-mesh")
      and stump_proto.same_room("lora", "lora-mesh") and stump_proto.same_room("mesh", "wifi-mesh")
      and not stump_proto.same_room("main", "lora-mesh"))

# A Stump on a computer with its own Heltec may mark #lora-mesh [LoRa] in /rooms.
r = stump_proto.parse_message("#lora-mesh ·2 [LoRa] LoRa (radio)")[0]
check("/rooms: a [LoRa] tier is a tier, not part of the topic", r["kind"] == "room" and r["tier"] == "LoRa"
      and r["topic"] == "LoRa (radio)", r)
r = stump_proto.parse_message("#lora-mesh ·2 LoRa (radio)  [LoRa]")[0]
check("/rooms: ...also where rrc.py puts it, after the topic", r["kind"] == "room" and r["tier"] == "LoRa"
      and r["topic"] == "LoRa (radio)", r)

# /node.json's radio (Stump 1.0.0-beta.5+) against ours: a mismatched radio never hears anything.
RADIO_NODE = {"name": "Far", "lxmf": "cd" * 16, "radio": {"frequency": 868_100_000, "bandwidth": 125_000, "sf": 8, "cr": 5}}
serve_http(PORT + 1, FakeRRC(rns_verify), FakeWeb(), node=RADIO_NODE)
time.sleep(0.2)
nr = stump_web.node_radio(http, f"http://127.0.0.1:{PORT + 1}")
mine = {"frequency": 915_000_000, "bandwidth": 125_000, "spreading_factor": 8, "coding_rate": 5, "tx_power": 7}
check("node radio read from /node.json", nr == RADIO_NODE["radio"], nr)
check("...only the frequency differs from ours", stump_web.radio_differences(nr, mine) == ["frequency"])
check("...'use these' sets frequency, bandwidth, SF, CR (TX power stays ours)", stump_web.radio_patch(nr) ==
      {"frequency": 868_100_000, "bandwidth": 125_000, "spreading_factor": 8, "coding_rate": 5})
check("a node without /node.json (or without a radio): nothing to compare", stump_web.node_radio(http, BASE) is None)

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

# Answers as data (Accept: application/json), what the app asks for.
J = {"Accept": "application/json"}
st, body, _ = raw("POST", "/upload", b"abc", {**J, "X-Filename": "notes.txt", "Content-Length": "3"})
d = json.loads(body)
check("upload as data: ok, stored name, balance", st == 200 and d["ok"] and d["name"] == "notes.txt"
      and isinstance(d["balance"], int) and d["slot"] is False, d)
os.environ["FAKE_STUMP_FULL"] = "1"
st, body, _ = raw("POST", "/upload", b"abc", {**J, "X-Filename": "a.txt", "Content-Length": "3"})
del os.environ["FAKE_STUMP_FULL"]
d = json.loads(body)
check("full card as data: 507, error 'full'", st == 507 and not d["ok"] and d["error"] == "full", d)
st, body, _ = raw("POST", "/upload", b"", {**J, "X-Filename": "e.txt", "Content-Length": "0"})
check("empty as data: 400, error 'empty'", st == 400 and json.loads(body)["error"] == "empty")

# Over HTTPS the app sends the name percent-encoded with X-Filename-Encoding: url.
st, body, _ = raw("POST", "/upload", b"x", {**J, "X-Filename": "th%C3%A9%20%E6%97%A5%E6%9C%AC.jpg",
                                             "X-Filename-Encoding": "url", "Content-Length": "1"})
check("percent-encoded name decoded (thé 日本.jpg)", st == 200 and json.loads(body)["name"] == "thé 日本.jpg", body)
st, body, _ = raw("POST", "/upload", b"x", {**J, "X-Filename": "100%25.txt", "Content-Length": "1"})
check("without the encoding header a % stays literal", json.loads(body)["name"] == "100%25.txt", body)

# é as one Latin-1 byte (browsers, urllib): a current node reads it as Latin-1; an
# older one closed the connection with no answer (what FireFly reported).
st, body, _ = raw("POST", "/upload", b"abc", {**J, "X-Filename": "été.txt", "Content-Length": "3"})
check("Latin-1 filename header: now answered and stored as été.txt", st == 200 and json.loads(body)["name"] == "été.txt", body)
os.environ["FAKE_STUMP_OLD"] = "1"
try:
    raw("POST", "/upload", b"abc", {"X-Filename": "été.txt", "Content-Length": "3"})
    latin1_answered = True
except Exception:
    latin1_answered = False
check("...an older node: no answer at all (why FireFly sends raw UTF-8)", not latin1_answered)
st, body = raw_socket_upload("Crème brûlée.txt", b"yum")
check("...but raw UTF-8 works there too, as plain text", st == 200 and body.decode().startswith("Uploaded."), (st, body))
del os.environ["FAKE_STUMP_OLD"]

# ---------------------------------------------------------------- plugins and apps
pl = stump_web.plugins(http, BASE)
check("plugins: !commands offered, slash-only stumpid/fservbot left out",
      pl["state"] == "ok" and [c["word"] for c in pl["commands"]] == ["!8ball", "!roll", "!seen"], pl)
t = stump_web.tools(http, BASE, "0.5.0-beta.1")
check("tools: the node's newer Android app is offered, url as given (it names the firefly/ folder)",
      t["update"] and t["update"]["version"] == "9.9.9" and t["update"]["file"] == "FireFly-Android-9.9.9.apk"
      and t["update"]["url"] == BASE + "/tool?f=firefly%2FFireFly-Android-9.9.9.apk", t)
os.environ["FAKE_STUMP_FLAT_TOOLS"] = "1"
t_flat = stump_web.tools(http, BASE, "0.5.0-beta.1")
check("tools: a beta.2 node (loose files, no sections) still works",
      t_flat["update"]["url"] == BASE + "/tool?f=FireFly-Android-9.9.9.apk", t_flat)
del os.environ["FAKE_STUMP_FLAT_TOOLS"]
os.environ["FAKE_STUMP_APK_VERSION"] = "0.4.0"
check("tools: an older one isn't", stump_web.tools(http, BASE, "0.5.0-beta.1")["update"] is None)
os.environ["FAKE_STUMP_APK_VERSION"] = "0.5.0-beta.1"
check("tools: the same one isn't", stump_web.tools(http, BASE, "0.5.0-beta.1")["update"] is None)
os.environ["FAKE_STUMP_APK_VERSION"] = "0.5.0"
check("tools: the final release beats its beta", stump_web.tools(http, BASE, "0.5.0-beta.1")["update"]["version"] == "0.5.0")
del os.environ["FAKE_STUMP_APK_VERSION"]
st, data, _ = raw("GET", t["update"]["url"][len(BASE):])
check("the offered APK downloads from its url", st == 200 and data == web.APK)
check("chain: a lone Stump says not chained", stump_web.chain(http, BASE) == {"state": "ok", "enabled": False,
      "role": None, "below": 0, "parent": None})
os.environ["FAKE_STUMP_CHAIN"] = "member"
c = stump_web.chain(http, BASE)
check("chain: a member, its parent and how many below", c["enabled"] and c["role"] == "member"
      and c["parent"] == "192.168.4.1" and c["below"] == 1, c)
del os.environ["FAKE_STUMP_CHAIN"]

# stump.node beacons may gain fields (beta.2): read the first four, ignore the rest.
import RNS.vendor.umsgpack as msgpack
from firefly.stump import parse_beacon
h = bytes(range(16))
check("beacon with 4 fields", parse_beacon(msgpack.packb(["stump", "Beta A", "LaBuche", h])) == (h.hex(), "LaBuche", "Beta A"))
check("beacon with a 5th field: same answer", parse_beacon(msgpack.packb(["stump", "Beta A", "LaBuche", h, {"chain": 2}]))
      == (h.hex(), "LaBuche", "Beta A"))
check("not a beacon", parse_beacon(msgpack.packb(["other", "x", "y", h])) is None)

os.environ["FAKE_STUMP_OLD"] = "1"
check("older node: no chain.json -> off", stump_web.chain(http, BASE)["state"] == "off")
check("older node: no plugins.json -> off", stump_web.plugins(http, BASE)["state"] == "off")
check("older node: no tools.json -> off", stump_web.tools(http, BASE, "0.4.0")["state"] == "off")
del os.environ["FAKE_STUMP_OLD"]

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
