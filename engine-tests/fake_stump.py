"""A small Stump node for tests, following CLIENT_QUICKSTART.md.

FakeRRC is the chat logic (rooms, nicks, DMs, /auth with real signature
checks). Two fronts use it:
  * serve_http(port)  -> GET /rrc/poll, POST /rrc/send   (in-process thread)
  * `python3 fake_stump.py <workdir> <tcp port>`  -> an LXMF node over TCP,
    with its stump.node beacon, in its own process (Reticulum is one per process)

A resident user, alice, greets every newcomer in the room and sends them a DM.
"""
import json, os, secrets, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
import re

NICK_OK = re.compile(r"[A-Za-z0-9\-_\[\]{}\\^`|]")


# Stump 1.0.0-beta.4 renamed #lxmf to #lora-mesh; FAKE_STUMP_OLD=1 is a node from before.
MESH_ROOM = "lxmf" if os.environ.get("FAKE_STUMP_OLD") == "1" else "lora-mesh"
ROOM_ALIASES = {} if MESH_ROOM == "lxmf" else {"lxmf": "lora-mesh", "lora": "lora-mesh"}


def fold(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


def clean(n):
    return ("".join(c if NICK_OK.match(c) else "-" for c in n or ""))[:16] or "guest"


class FakeRRC:
    def __init__(self, verify):
        self.verify = verify            # (pub_hex, sig_hex, nonce_hex) -> identity hash hex or None
        self.lock = threading.RLock()
        self.next_id = 1
        self.rooms = {"main": [], MESH_ROOM: [], "lounge": []}
        self.topics = {"main": "General. Be decent.", MESH_ROOM: "LoRa (radio)", "lounge": ""}
        self.buckets = {}               # key -> (tokens, time): 3 lines at once, then one every 2 s
        self.tiers = {"lounge": "hybrid", "vip": "minted"}
        self.rooms.setdefault("vip", []); self.topics.setdefault("vip", "verified only")
        self.verified = set()           # keys that passed /auth (minted rooms let them in)
        self.users = {"alice": {"nick": "alice", "room": "main", "mesh": False}}
        self.inbox = {}                 # key -> [dm dicts]
        self.voice_sent = {}            # key -> time of the last voice note (rate limit)
        self.nonces = {}
        self.plugin_calls = {}          # key -> recent !command times (the tavern plugin's flood guard)
        self.node_lxmf = "00" * 16      # what the poll's node field gives as this node's LXMF address
        # Stump 1.0.0-beta.21 access modes (stumpid): "hybrid" lets anyone write only in #main
        # before /auth; a line elsewhere, or a private message, gets AUTH_REQUIRED.
        self.auth_mode = os.environ.get("FAKE_STUMP_MODE", "open")
        self.renamed = {}               # "old" -> "new" nicks of the last 30 minutes (beta.25 poll field)
        self.busy = 0                   # answer this many requests "503 busy" (beta.24 connection limits)
        self.auth_seen = []             # keys that sent /auth (FireFly does it on connecting, beta.25)
        self.no_auth = False            # answer /auth "? /auth" (no identity plugin)
        # The greeting (Stump 1.0.0-beta.31, APP_HELLO): an app without cookies must /auth, then
        # /nick, before it reads or writes. lora_names: keys whose name the Stump already heard
        # on LoRa (/auth alone is enough for them).
        self.hello_gate = False
        self.nick_chosen = set()
        self.lora_names = {}
        self.sent_log = []              # (key, line) as /rrc/send got them
        # /hello (Stump 1.0.0-beta.32): the greeting in one signed exchange. hello_v2 turns it
        # on (the node then has a real identity, and node_lxmf is its LXMF address).
        self.hello_v2 = False
        self.node_ident = None
        self.hello_nonces = {}
        self.hello_log = []             # (key, "GET"|"POST", body)
        self.prefer = {}                # key -> "wifi" | "lora"
        self.hello_429 = 0              # answer this many /hello "429 wait"
        self.hello_forge = False        # sign with the wrong key (an impostor)

    def enable_hello(self):
        import RNS
        self.hello_v2 = True
        self.node_ident = RNS.Identity()
        self.node_lxmf = RNS.Destination.hash(self.node_ident, "lxmf", "delivery").hex()

    def hello_get(self, key):
        n = secrets.token_hex(16)
        self.hello_nonces[key] = n
        signer = self.node_ident
        if self.hello_forge:
            import RNS
            signer = RNS.Identity()
        return {"stump": "LaBuche Test", "lxmf": self.node_lxmf, "nonce": n,
                "pub": self.node_ident.get_public_key().hex(),
                "sig": signer.sign((n + self.node_lxmf).encode()).hex(), "hello": self.hello(key) or "done"}

    def hello_post(self, key, d):
        n = self.hello_nonces.pop(key, None)
        who = self.verify(d.get("pub", ""), d.get("sig", ""), n) if n else None
        if not who:
            return 403, {"hello": "required", "error": "auth", "message": "AUTH-FAIL bad signature"}
        self.verified.add(key)
        out = {}
        name = clean(str(d.get("name") or ""))
        if name and any(u["nick"] == name for k, u in self.users.items() if k != key):
            out = {"error": "name", "message": f"{name} is taken"}
        elif name:
            self.users[key]["nick"] = name
        self.nick_chosen.add(key)
        if d.get("prefer") in ("wifi", "lora"):
            self.prefer[key] = d["prefer"]
        out.update({"hello": self.hello(key) or "done", "nick": self.users[key]["nick"],
                    "lxmf": "ab" * 16, "prefer": self.prefer.get(key, "wifi")})
        return 200, out

    def _take_token(self, key):
        """Lines others see: 3 at once, then one every 2 s (Stump 1.0.0-beta.3's guard)."""
        if os.environ.get("FAKE_STUMP_OLD") == "1":
            return True
        tokens, t = self.buckets.get(key, (3.0, time.time()))
        tokens = min(3.0, tokens + (time.time() - t) / 2.0)
        if tokens < 1:
            self.buckets[key] = (tokens, time.time())
            return False
        self.buckets[key] = (tokens - 1, time.time())
        return True

    def _id(self):
        self.next_id += 1
        return self.next_id

    def _post(self, room, nick, body, kind="msg"):
        self.rooms[room].append({"id": self._id(), "ts": time.time(), "nick": nick, "body": body, "kind": kind})

    def _key_of(self, nick):
        for k, u in self.users.items():
            if u["nick"].lower() == nick.lower():
                return k
        return None

    def arrive(self, key, nick, room, mesh=False):
        with self.lock:
            if key in self.users:
                return False
            n, i = clean(nick), 1
            while self._key_of(n):
                i += 1
                n = f"{clean(nick)[:14]}{i}"
            self.users[key] = {"nick": n, "room": room, "mesh": mesh}
            self._post(room, "*", f"✓ {'~' if mesh else ''}{n}", "system")
            self._post(room, "alice", f"welcome {n}")
            self.inbox.setdefault(key, []).append(
                {"id": self._id(), "ts": time.time(), "nick": "alice", "body": "psst, welcome", "kind": "dm"})
            return True

    def handle(self, key, line):
        """-> (replies, moved_room_or_None)"""
        with self.lock:
            u = self.users[key]
            me, room = u["nick"], u["room"]
            low = line.lower()
            if self.auth_mode == "hybrid" and key not in self.verified and (
                    (not line.startswith("/") and room != "main") or low.startswith(("/msg ", "/m ", "/w "))):
                return ["auth required for that -- send /auth to verify your identity"], None
            if not line.startswith("/") and not self._take_token(key):
                return ["⧗ — slow down: wait a moment before sending again"], None      # the line wasn't sent
            if line.startswith("!") and line[1:].split()[:1] and line[1:].split()[0].lower() in ("roll", "8ball", "seen"):
                # A plugin command: an ordinary room line, then the plugin answers from its own
                # nick. Too many too fast: ⧗, to the sender only.
                now = time.time()
                calls = [t for t in self.plugin_calls.get(key, []) if now - t < 10]
                if len(calls) >= 3:
                    self.plugin_calls[key] = calls
                    return ["⧗ — slow down: too many commands"], None
                self.plugin_calls[key] = calls + [now]
                self._post(room, me, line[:400])
                self._post(room, "tavern", f"{me} rolls 4")
                return [], None
            if not line.startswith("/"):
                self._post(room, me, line[:400])
                return [], None
            cmd, _, rest = line.partition(" ")
            cmd = cmd.lower()
            if cmd == "/auth":
                self.auth_seen.append(key)
                if self.no_auth:                # a Stump without identities: /auth is unknown
                    return ["? /auth"], None
            if cmd == "/auth" and not rest:
                n = secrets.token_hex(16)
                self.nonces[key] = (n, time.time())
                return [f"AUTH-CHALLENGE {n}"], None
            if cmd == "/auth":
                parts = rest.split()
                n, t = self.nonces.pop(key, (None, 0))
                if n is None:
                    return ["AUTH-FAIL no challenge pending -- start with /auth"], None
                if time.time() - t > 60:
                    return ["AUTH-FAIL challenge expired"], None
                h = self.verify(parts[0], parts[1], n) if len(parts) == 2 else None
                if h:
                    self.verified.add(key)
                return ([f"AUTH-OK {h}"] if h else ["AUTH-FAIL signature does not match"]), None
            if cmd in ("/join", "/j"):
                new = re.sub(r"[^a-z0-9]", "-", rest.strip().lstrip("#").lower())[:20]
                new = ROOM_ALIASES.get(new, new)                # /join lxmf still works, answers lora-mesh
                if new == room:
                    return [f"= #{room} — tu es déjà dans #{room}"], None
                if self.tiers.get(new) == "minted" and key not in self.verified:
                    return [f"⊘ #{new} minted — that room requires a verified identity -- send /auth first"], None
                if self.users[key].get("mesh"):
                    # Alice speaks in the room being left, just as the user leaves: this line
                    # is pushed after the move, in a batch titled with the old room.
                    self._post(room, "alice", f"bye from #{room}")
                self.rooms.setdefault(new, []); self.topics.setdefault(new, "")
                self._post(room, "*", f"✗ {me}", "system")
                u["room"] = new
                self._post(new, "*", f"✓ {me}", "system")
                return [f"→ #{new}"], new
            if cmd == "/rooms":
                out = []
                for r in sorted(self.rooms):
                    count = sum(1 for x in self.users.values() if x["room"] == r)
                    tier = f" [{self.tiers[r]}]" if r in self.tiers else (" [LoRa]" if r == MESH_ROOM and os.environ.get("FAKE_STUMP_LORA_TIER") == "1" else "")
                    out.append(f"#{r} ·{count}{tier} {self.topics[r]}".rstrip())
                return out, None
            if cmd == "/names":
                return [f"#{room}: " + ", ".join(x["nick"] for x in self.users.values() if x["room"] == room)], None
            if cmd in ("/msg", "/m", "/w"):
                to, _, text = rest.partition(" ")
                k = self._key_of(to.lstrip("~"))
                if not k:
                    return [f"⊖ {to.lstrip('~')}"], None
                self.inbox.setdefault(k, []).append(
                    {"id": self._id(), "ts": time.time(), "nick": me, "body": text, "kind": "dm"})
                return [f"[DM] <{me}>: {text}"], None
            if cmd == "/nick":
                self.nick_chosen.add(key)
                new = clean(rest.strip())
                self._post(room, "*", f"✎ {me} → {new}", "system")
                u["nick"] = new
                return [], None
            if cmd == "/help":
                return ["Commandes : /join /part /rooms /names /msg /me /nick /auth (voir la clé des symboles)"], None
            return [f"? {cmd} — commande inconnue"], None

    VOICE_FRAMES = {3: (4, 40), 4: (6, 40), 5: (7, 40), 6: (7, 40), 7: (8, 40), 8: (6, 20), 9: (8, 20)}

    def voice_dm(self, key, to, mode, data):
        """Relay a voice note as a DM, as the quickstart describes. -> replies"""
        with self.lock:
            me = self.users[key]["nick"]
            opus_ok = os.environ.get("FAKE_STUMP_CODEC2_ONLY") != "1"     # an updated node relays Opus too
            if not isinstance(data, (bytes, bytearray)) or not data or not (
                    mode in self.VOICE_FRAMES or (mode == 16 and opus_ok)):
                return ["unreadable voice note (Codec 2 expected)" if not opus_ok
                        else "unreadable voice note (Opus or Codec 2 expected)"]
            if mode == 16:
                sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "src", "main", "python"))
                from firefly.audio import duration_ms
                ms_total = duration_ms(16, bytes(data))
                if ms_total is None:
                    return ["unreadable voice note"]
                data, secs, bpf = bytes(data), ms_total / 1000, None
            else:
                bpf, ms = self.VOICE_FRAMES[mode]
                frames = len(data) // bpf
                data = bytes(data[:frames * bpf])                        # trailing partial frame trimmed
                secs = frames * ms / 1000
            if not 0.6 <= secs <= 15.1:
                return ["voice notes must be 0.6 to 15 seconds"]
            k = self._key_of(to.lstrip("~"))
            if not k:
                return [f"⊖ {to.lstrip('~')}"]
            last = self.voice_sent.get(key, 0)
            if time.time() - last < 5:
                return ["⧗ — slow down: one voice note every 5 seconds"]
            self.voice_sent[key] = time.time()
            tenths = (int(round(secs * 1000)) + 50) // 100           # tenths rounded half up
            label = f"♪ {tenths // 10}.{tenths % 10} s"
            self.inbox.setdefault(k, []).append({"id": self._id(), "ts": time.time(), "nick": me, "body": label,
                                                 "kind": "dm", "voice": {"mode": mode, "bytes": len(data), "secs": secs},
                                                 "audio": data})
            if self.users[k]["nick"] == "alice":                 # alice answers a voice note with one
                rev = data if bpf is None else b"".join(data[i:i + bpf] for i in range(len(data) - bpf, -1, -bpf))
                self.inbox.setdefault(key, []).append({"id": self._id(), "ts": time.time(), "nick": "alice",
                                                       "body": label, "kind": "dm",
                                                       "voice": {"mode": mode, "bytes": len(rev), "secs": secs},
                                                       "audio": rev})
            return [f"[DM] <{me}>: {label}"]

    def voice_frames(self, key, dm_id):
        with self.lock:
            return next((d["audio"] for d in self.inbox.get(key, []) if d["id"] == dm_id and "audio" in d), None)

    def leave(self, key):
        with self.lock:
            u = self.users.pop(key, None)
            if u:
                self._post(u["room"], "*", f"✗ {'~' if u['mesh'] else ''}{u['nick']}", "system")

    def hello(self, key):
        """None (not asked), "done" or "required"."""
        if not self.hello_gate:
            return None
        return "done" if key in self.verified and key in self.nick_chosen else "required"

    def poll(self, key, since):
        with self.lock:
            u = self.users[key]
            msgs = [m for m in self.rooms[u["room"]] if m["id"] > since]
            dms = [{k: v for k, v in d.items() if k != "audio"}           # no audio in the JSON
                   for d in self.inbox.get(key, []) if d["id"] > since]
            return {"room": u["room"], "nick": u["nick"], "topic": self.topics[u["room"]],
                    "rooms": sorted(self.rooms), "messages": msgs, "dms": dms,
                    "users": [x["nick"] for x in self.users.values() if x["room"] == u["room"]], "stumps": [],
                    "node": {"name": "LaBuche Test", "lxmf": self.node_lxmf},
                    **({"renamed": dict(self.renamed)} if self.renamed else {}),   # Stump 1.0.0-beta.25
                    "hw": {"state": "online", "title": "USB /dev/ttyUSB0 · Heltec V3 · RNode 1.82"}}


def rns_verify(pub_hex, sig_hex, nonce_hex):
    import RNS
    pk = RNS.Identity(create_keys=False)
    pk.load_public_key(bytes.fromhex(pub_hex))
    if pk.validate(bytes.fromhex(sig_hex), nonce_hex.encode()):
        return RNS.Identity.truncated_hash(bytes.fromhex(pub_hex)).hex()
    return None


class FakeWeb:
    """The billboard and the file shelf (fservbot), as the quickstart's reference describes."""
    WEIGHT = {**{e: 3 for e in ("mp4", "mkv", "avi", "mov")}, **{e: 2 for e in ("mp3", "flac", "wav", "ogg", "m4a")},
              **{e: 1 for e in ("pdf", "txt", "doc", "docx")}}
    CLASS = {3: "video", 2: "music"}

    def __init__(self):
        self.lock = threading.Lock()
        self.posts = [{"id": f"{time.time() - 3600:.9f}", "title": "Marché samedi", "body": "L'été à 10h", "sig": "0078"},
                      {"id": None, "title": "old post, before ids", "body": "", "sig": "0001"}]
        self.files = {"photo.jpg": b"\xff\xd8" + b"x" * 48211, "été à Montréal.txt": "bonjour l'été\n".encode()}
        self.balance = {}
        self.credits = os.environ.get("FAKE_STUMP_NO_CREDITS") != "1"

    def weight(self, name):
        return self.WEIGHT.get(name.rsplit(".", 1)[-1].lower() if "." in name else "", 1) if self.credits else 0

    def klass(self, name):
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        w = self.WEIGHT.get(ext)
        return self.CLASS.get(w, "document" if w == 1 else "other")

    def post(self, form):
        title = (form.get("title") or form.get("entry") or [""])[0]
        body = (form.get("body") or [""])[0].replace("\r\n", "\n")
        if not title and body:
            title = body.split("\n", 1)[0]
        title = " ".join(title.replace("\r", "\n").split("\n")).strip()[:80]
        body = body.strip()[:600]
        if not title:
            return False, "title_required"           # an old node drops it silently (still 303)
        if os.environ.get("FAKE_STUMP_POST_FULL") == "1":
            return False, "storage"
        with self.lock:
            pid = f"{time.time():.9f}"
            self.posts.insert(0, {"id": pid, "title": title, "body": body, "sig": "00ab"})
            return True, pid

    def shelf(self):
        with self.lock:
            return {"sd": True, "credits": self.credits,
                    "files": [{"name": n, "size": len(b), "class": self.klass(n), "cost": self.weight(n)}
                              for n, b in sorted(self.files.items())]}

    def upload(self, who, name, data):
        """-> (status, sentence, extra fields, error token) -- barkeep's _result arguments"""
        if not name:
            return 400, "No filename given.", None, "no_filename"
        if os.environ.get("FAKE_STUMP_NO_SD") == "1":
            return 503, "No card in the slot right now.", None, "no_card"
        if not data:
            return 400, "Empty upload.", None, "empty"
        if os.environ.get("FAKE_STUMP_FULL") == "1":
            return 507, "Card capacity exceeded (75% threshold limit).", None, "full"
        # barkeep._clean_filename, exactly
        name = name.replace("/", "_").replace("..", "_").replace("'", "").replace('"', "")
        for ch in "<>:\\|?*":
            name = name.replace(ch, "_")
        name = "".join(c for c in name if ord(c) >= 32).strip()
        if not name:
            return 400, "No filename given.", None, "no_filename"
        with self.lock:
            self.files[name] = data
            extra = {"name": name, "size": len(data), "slot": False, "balance": None}
            if not self.credits:
                return 200, "Uploaded. Thanks for bringing something.", extra, None
            self.balance[who] = self.balance.get(who, 0) + self.weight(name)
            extra["balance"] = self.balance[who]
            return 200, f"Uploaded. Your balance: {self.balance[who]}", extra, None

    APK = b"PK\x03\x04 not really an apk " * 100

    def tools(self):
        """/tools.json after 1.0.0-beta.2: a folder per tool, each with a README; the apps under
        firefly/, their url naming the folder, their file the bare name (FAKE_STUMP_OLD: flat)."""
        version = os.environ.get("FAKE_STUMP_APK_VERSION", "9.9.9")
        apk = f"FireFly-Android-{version}.apk"
        folder = "" if os.environ.get("FAKE_STUMP_FLAT_TOOLS") == "1" else "firefly/"
        prov = {"name": "provisioner.py", "size": 156584, "section": "" if not folder else "provisioner",
                "url": "/tool?f=" + quote(("provisioner/" if folder else "") + "provisioner.py", safe="")}
        out = {"sd": True,
               "apps": [{"app": "android", "file": apk, "version": version, "size": len(self.APK),
                         "url": "/tool?f=" + quote(folder + apk, safe="")},
                        {"app": "rk3326", "file": "FireFly-RK3326-0.6.2.zip", "version": "0.6.2", "size": 97653,
                         "url": "/tool?f=" + quote(folder + "FireFly-RK3326-0.6.2.zip", safe="")}],
               "files": [prov]}
        if folder:
            out["sections"] = [{"dir": "provisioner", "title": "Provisioner",
                                "summary": "The tool that sets up and updates Stump nodes",
                                "readme": "/tool?f=provisioner%2FREADME.md", "files": [prov]}]
        return out

    PLUGINS = {"api": 1, "trigger": "!",
               "plugins": [{"name": "tavern", "version": "0.1", "commands": ["8ball", "roll", "seen"],
                            "pages": ["/p/tavern/", "/p/tavern/votes"]},
                           {"name": "stumpid", "version": "1.0", "commands": ["auth", "whoami", "admin", "invite"],
                            "pages": []},
                           {"name": "fservbot", "version": "1.0", "commands": ["fs", "fsget"], "pages": []}]}

    def download(self, who, name):
        with self.lock:
            data = self.files.get(name)
            if data is not None and self.credits:
                self.balance[who] = self.balance.get(who, 0) - self.weight(name)
            return data


def serve_udp(host, card, port=4244):
    """A Stump answering STUMP? (1.0.0-beta.21) on host:port. card: a dict, or a
    function returning one (its address can change). Returns a stop() function."""
    import socket as _s
    s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
    s.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.settimeout(0.2)
    alive = {"on": True}
    def loop():
        while alive["on"]:
            try:
                data, addr = s.recvfrom(256)
            except Exception:
                continue
            if data.startswith(b"STUMP?"):
                c = card() if callable(card) else card
                if c:
                    s.sendto(json.dumps(c).encode(), addr)
        s.close()
    threading.Thread(target=loop, daemon=True).start()
    def stop():
        alive["on"] = False
    return stop


def serve_http(port, rrc, web=None, host="127.0.0.1", node=None, delay=0.0, directory=None):
    """node: what /node.json answers (a dict), or None for a node without it (404).
    delay: seconds added to every answer, to look like a far or busy Stump."""
    web = web or FakeWeb()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _key(self):
            key = "web:" + self.client_address[0]
            rrc.arrive(key, "guest-a1b2", "main")
            return key

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _text(self, code, text, extra=()):
            body = text.encode()
            self.send_response(code); self.send_header("Content-Type", "text/plain; charset=utf-8")
            for k, v in extra: self.send_header(k, v)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _off(self, feature):
            return os.environ.get("FAKE_STUMP_NO_" + feature) == "1"

        def _old(self):
            """A node from before the JSON answers (LaBuche-Stump stable, Oct 7)."""
            return os.environ.get("FAKE_STUMP_OLD") == "1"

        def _result(self, as_json, code, message, error=None, extra=None):
            """barkeep._result: data for apps that ask, the sentence for everyone else."""
            if not as_json:
                return self._text(code, message)
            out = {"ok": 200 <= code < 300, "message": message}
            if error:
                out["error"] = error
            out.update(extra or {})
            return self._json(out, code)

        def do_GET(self):
            u = urlparse(self.path)
            if rrc.busy > 0:                    # full right now: 503, Retry-After: 2, body "busy"
                rrc.busy -= 1
                body = b"busy"
                self.send_response(503); self.send_header("Retry-After", "2")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
            if delay:
                time.sleep(delay)
            if u.path == "/stumps.json":
                if directory is None:
                    return self._text(404, "not found")
                return self._json(directory() if callable(directory) else directory)
            if u.path == "/node.json":
                if isinstance(node, str):                       # a beta.2 node: the home page, as HTML
                    body = node.encode()
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                return self._json(node) if node is not None else self._text(404, "not found")
            if u.path in ("/billboard.json", "/billboard"):
                if self._off("BOARD"):
                    return self._text(404, "not offered on this node")
                with web.lock:
                    posts = list(web.posts)
                q = parse_qs(u.query)
                words = fold(q.get("q", [""])[0]).split()
                if words and not self._old():               # ?q=: title or body holds one of the words
                    posts = [p for p in posts if any(w in fold(p["title"] + " " + p["body"]) for w in words)]
                return self._json({"posts": posts})
            if u.path in ("/files.json", "/files"):
                if self._off("FILES"):
                    return self._text(404, "not offered on this node")
                shelf = web.shelf()
                if not self._old():                          # beta.3: cat, mtime, ?q= ?cat= ?sort=
                    q = parse_qs(u.query)
                    for f in shelf["files"]:
                        f["cat"] = "image" if f["name"].lower().endswith((".jpg", ".png")) else f["class"]
                        f["mtime"] = 1791388089
                    words = fold(q.get("q", [""])[0]).split()
                    if words:
                        shelf["files"] = [f for f in shelf["files"] if any(w in fold(f["name"]) for w in words)]
                    if q.get("cat"):
                        shelf["files"] = [f for f in shelf["files"] if f["cat"] == q["cat"][0]]
                return self._json(shelf)
            if u.path == "/tools.json" and not self._old():
                return self._json(web.tools())
            if u.path == "/chain.json" and not self._old():
                role = os.environ.get("FAKE_STUMP_CHAIN", "")       # "", "root" or "member"
                if not role:
                    return self._json({"enabled": False})
                return self._json({"enabled": True, "role": role, "ap_ip": "192.168.5.1", "channel": 6, "secret": True,
                                   "uplink": None if role == "root" else
                                   {"ssid": "Stump-A", "ip": "192.168.4.23", "parent": "192.168.4.1", "parent_heard_s": 3},
                                   "below": ["192.168.6.1"]})
            if u.path == "/plugins.json" and not self._old():
                return self._json(web.PLUGINS)
            if u.path == "/tool":
                name = parse_qs(u.query).get("f", [""])[0].removeprefix("firefly/")      # the apps' folder
                if not name.startswith("FireFly-Android-"):
                    return self._text(404, "no such file")
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(web.APK))); self.end_headers(); self.wfile.write(web.APK)
                return
            if u.path == "/download":
                if self._off("FILES"):
                    return self._text(404, "not offered on this node")
                name = parse_qs(u.query).get("f", [""])[0]          # parse_qs decodes UTF-8 percent-escapes
                data = web.download(self.client_address[0], name)
                if data is None:
                    return self._text(404, "File not found")
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
                return
            if u.path == "/hello" and rrc.hello_v2:
                key = self._key()
                rrc.hello_log.append((key, "GET", None))
                if rrc.hello_429 > 0:
                    rrc.hello_429 -= 1
                    return self._json({"hello": "wait", "retry_after": 1}, 429)
                return self._json(rrc.hello_get(key))
            if u.path == "/rrc/voice":
                frames = rrc.voice_frames(self._key(), int(parse_qs(u.query).get("id", ["0"])[0]))
                if frames is None:
                    return self._json({"error": "not found"}, 404)
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(frames))); self.end_headers(); self.wfile.write(frames)
                return
            if u.path != "/rrc/poll":
                return self._json({"error": "not found"}, 404)
            q = parse_qs(u.query)
            key = self._key()
            if rrc.hello(key) == "required":    # nothing to read before the greeting
                return self._json({"room": "main", "nick": rrc.users[key]["nick"], "rooms": [], "messages": [],
                                   "dms": [], "users": [], "stumps": [], "renamed": {}, "hello": "required",
                                   "topic": "Say who you are first (/auth, then /nick)."})
            out = rrc.poll(key, int(q.get("since", ["0"])[0]))
            if rrc.hello(key):
                out["hello"] = rrc.hello(key)
            self._json(out)

        def do_POST(self):
            u = urlparse(self.path)
            if rrc.busy > 0:                    # full right now: 503, Retry-After: 2, body "busy"
                rrc.busy -= 1
                body = b"busy"
                self.send_response(503); self.send_header("Retry-After", "2")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
            if u.path == "/post":
                if self._off("BOARD"):
                    return self._text(404, "not offered on this node")
                n = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(n).decode("utf-8")
                if "application/json" in self.headers.get("Content-Type", ""):
                    d = json.loads(raw or "{}")
                    form = {"title": [str(d.get("title") or "")], "body": [str(d.get("body") or "")]}
                else:
                    form = parse_qs(raw, keep_blank_values=True)
                if os.environ.get("FAKE_STUMP_POST_LIMIT") == "1" and not self._old():
                    return self._result(True, 429, "too many posts: try again in a minute", "rate_limited")
                ok, info = web.post(form)
                if self._old() or "application/json" not in self.headers.get("Accept", ""):
                    return self._text(303, "", extra=[("Location", "/billboard")])
                if ok:
                    return self._result(True, 201, "posted", extra={"id": info})
                if info == "title_required":
                    return self._result(True, 400, "a title is required", "title_required")
                return self._result(True, 507, "the post couldn't be saved", "storage")
            if u.path == "/upload":
                if self._off("FILES"):
                    return self._text(404, "not offered on this node")
                n = int(self.headers.get("Content-Length", "0"))
                data = self.rfile.read(n)
                if len(data) < n:
                    return self._text(500, "Upload interrupted")
                raw = self.headers.get("X-Filename", "upload.bin").encode("latin-1")    # the header's bytes
                if self._old():
                    try:                            # an old node: UTF-8 only, anything else closed the connection
                        fname = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        self.close_connection = True
                        return
                else:
                    try:                            # now: UTF-8, else Latin-1 (barkeep._header_text)
                        fname = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        fname = raw.decode("latin-1")
                    if self.headers.get("X-Filename-Encoding", "").lower() == "url":
                        fname = unquote(fname)
                code, text, extra, error = web.upload(self.client_address[0], fname, data)
                as_json = not self._old() and "application/json" in self.headers.get("Accept", "")
                return self._result(as_json, code, text, error, extra)
            if u.path == "/hello" and rrc.hello_v2:
                key = self._key()
                n = int(self.headers.get("Content-Length", "0"))
                d = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                rrc.hello_log.append((key, "POST", d))
                if rrc.hello_429 > 0:
                    rrc.hello_429 -= 1
                    return self._json({"hello": "wait", "retry_after": 1}, 429)
                code, out = rrc.hello_post(key, d)
                return self._json(out, code)
            if u.path == "/rrc/voice":
                q = parse_qs(u.query)
                n = int(self.headers.get("Content-Length", "0"))
                if n > 16000:                                   # room for a 15 s Opus note (~13.4 KB)
                    return self._json({"error": "too large"}, 413)
                body = self.rfile.read(n)
                return self._json({"replies": rrc.voice_dm(self._key(), q.get("to", [""])[0],
                                                           int(q.get("mode", ["4"])[0]), body)})
            if self.path != "/rrc/send":
                return self._json({"error": "not found"}, 404)
            n = int(self.headers.get("Content-Length", "0"))
            line = self.rfile.read(n).decode("utf-8")
            key = self._key()
            rrc.sent_log.append((key, line))
            if rrc.hello(key) == "required" and line.strip().lower().split(" ", 1)[0] not in ("/auth", "/nick", "/help", "/whoami"):
                return self._json({"replies": ["Say who you are first (/auth, then /nick)."], "room": None,
                                   "error": "hello_required"})
            replies, room = rrc.handle(key, line)
            if key in rrc.verified and key in rrc.lora_names and key not in rrc.nick_chosen:
                rrc.nick_chosen.add(key)        # its LoRa name: /auth alone completes the greeting
                rrc.users[key]["nick"] = rrc.lora_names[key]
            out = {"replies": replies, "room": room}
            if rrc.hello(key):
                out["hello"] = rrc.hello(key)
            self._json(out)

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def mesh_main(work, port):
    import RNS, LXMF
    import RNS.vendor.umsgpack as msgpack
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "config"), "w") as f:
        f.write(f"""[reticulum]\n  enable_transport = Yes\n  share_instance = No\n[logging]\n  loglevel = 2\n[interfaces]
  [[TCP Server]]\n    type = TCPServerInterface\n    enabled = yes\n    listen_ip = 127.0.0.1\n    listen_port = {port}\n""")
    RNS.Reticulum(configdir=work, loglevel=2)
    ident = RNS.Identity()
    if os.environ.get("FAKE_PN"):
        # A real upstream LXMF propagation node on the node's own identity, stamp cost 16,
        # like LaBuche. Upstream announces it once, a few seconds after enabling.
        limit = int(os.environ.get("FAKE_PN_LIMIT_KB", "15"))      # LaBuche: 15 KB per transfer
        router = LXMF.LXMRouter(identity=ident, storagepath=os.path.join(work, "lxmf"),
                                name="LaBuche Test", propagation_cost=16,
                                propagation_limit=limit, sync_limit=limit)
        router.enable_propagation()
        pn = RNS.Destination.hash(ident, "lxmf", "propagation").hex()
        print("NODE pn", pn, flush=True)
    else:
        router = LXMF.LXMRouter(identity=ident, storagepath=os.path.join(work, "lxmf"))
    local = router.register_delivery_identity(ident, display_name="LaBuche Test")
    beacon = RNS.Destination(ident, RNS.Destination.IN, RNS.Destination.SINGLE, "stump", "node")
    rrc = FakeRRC(rns_verify)
    names, sent_upto, active = {}, {}, {}
    timeout = float(os.environ.get("FAKE_PEER_TIMEOUT", "300"))   # MESH_PEER_TIMEOUT

    class Ann:
        aspect_filter = "lxmf.delivery"
        def received_announce(self, destination_hash, announced_identity, app_data):
            if app_data:
                names[destination_hash] = LXMF.display_name_from_app_data(app_data)
    RNS.Transport.register_announce_handler(Ann())

    def reply(src, text, fields=None, title=""):
        who = RNS.Identity.recall(src)
        t = time.time()
        while who is None and time.time() - t < 15:
            RNS.Transport.request_path(src); time.sleep(0.5); who = RNS.Identity.recall(src)
        d = RNS.Destination(who, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
        router.handle_outbound(LXMF.LXMessage(d, local, text, title=title, fields=fields,
                                              desired_method=LXMF.LXMessage.OPPORTUNISTIC))

    def on_msg(m):
        key, text = "mesh:" + m.source_hash.hex(), m.content_as_string()
        print(f"NODE got [{m.method}] {text[:70]!r}", flush=True)
        if key in active and time.time() - active[key] > timeout:
            rrc.leave(key)            # went quiet: no longer a room participant
        active[key] = time.time()
        landed = rrc.arrive(key, names.get(m.source_hash, "guest"), MESH_ROOM, mesh=True)
        if landed:
            reply(m.source_hash, f"→ #{MESH_ROOM}")     # you're told where you landed, before anything else
            if key not in sent_upto:
                sent_upto[key] = 0      # first contact only: a re-landing doesn't replay history
        if text.startswith("/auth ") and m.method != LXMF.LXMessage.OPPORTUNISTIC:
            return reply(m.source_hash, "AUTH-FAIL test: answer was not a single packet")
        audio = (m.fields or {}).get(LXMF.FIELD_AUDIO)
        if isinstance(audio, (list, tuple)) and len(audio) >= 2 and isinstance(audio[1], (bytes, bytearray)):
            parts = text.split()
            if len(parts) >= 2 and parts[0] in ("/msg", "/m", "/w"):
                return reply(m.source_hash, "\n".join(rrc.voice_dm(key, parts[1], int(audio[0]), bytes(audio[1]))))
            return reply(m.source_hash, "to send a voice note through this node: /msg <who> with the note attached")
        before_room = rrc.users[key]["room"]
        upto = sent_upto.get(key, 0)                # what was pushed before this command
        replies, moved = rrc.handle(key, text)
        if replies:
            reply(m.source_hash, "\n".join(replies))
        if moved and key in sent_upto:
            # Lines of the room just left that weren't pushed yet: they still go out, titled
            # with that room, after the move.
            with rrc.lock:
                late = [x for x in rrc.rooms[before_room] if x["id"] > upto and x["nick"] != rrc.users[key]["nick"]]
            if late:
                reply(m.source_hash, "\n".join(f"<{x['nick']}> {x['body']}" if x["kind"] == "msg" else x["body"] for x in late),
                      title=f"#{before_room}")

    def pusher():
        while True:
            time.sleep(1)
            for key in list(sent_upto):
                data = rrc.poll(key, sent_upto[key])
                me = data["nick"]
                lines = []
                for x in data["messages"]:
                    if x["nick"] == me: continue            # your own lines are never echoed back
                    lines.append(x["body"] if x["kind"] == "system" else f"<{x['nick']}> {x['body']}")
                for d in data["dms"]:
                    if d.get("voice"):                      # its own message, with the audio field
                        frames = rrc.voice_frames(key, d["id"])
                        reply(bytes.fromhex(key[5:]), f"[DM] <{d['nick']}>: {d['body']}",
                              fields={LXMF.FIELD_AUDIO: [d["voice"]["mode"], frames]})
                        continue
                    lines.append(f"[DM] <{d['nick']}>: {d['body']}")
                ids = [x["id"] for x in data["messages"] + data["dms"]]
                if ids:
                    sent_upto[key] = max(ids)
                room_lines = [l for l in lines if not l.startswith("[DM]")]
                dm_lines = [l for l in lines if l.startswith("[DM]")]
                if room_lines:                      # a room batch names its room in the title
                    reply(bytes.fromhex(key[5:]), "\n".join(room_lines[:10]), title=f"#{data['room']}")
                if dm_lines:                        # a batch of DMs has no title
                    reply(bytes.fromhex(key[5:]), "\n".join(dm_lines))

    router.register_delivery_callback(on_msg)
    threading.Thread(target=pusher, daemon=True).start()
    print("NODE ready", local.hash.hex(), flush=True)
    for i in range(900):
        if i % 3 == 0:
            router.announce(local.hash)
            beacon.announce(app_data=msgpack.packb(["stump", "test-1.0", "LaBuche Test", local.hash]))
        time.sleep(1)


if __name__ == "__main__":
    mesh_main(sys.argv[1], int(sys.argv[2]))
