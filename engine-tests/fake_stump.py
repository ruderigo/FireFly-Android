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
from urllib.parse import parse_qs, urlparse
import re

NICK_OK = re.compile(r"[A-Za-z0-9\-_\[\]{}\\^`|]")


def clean(n):
    return ("".join(c if NICK_OK.match(c) else "-" for c in n or ""))[:16] or "guest"


class FakeRRC:
    def __init__(self, verify):
        self.verify = verify            # (pub_hex, sig_hex, nonce_hex) -> identity hash hex or None
        self.lock = threading.RLock()
        self.next_id = 1
        self.rooms = {"main": [], "lxmf": [], "lounge": []}
        self.topics = {"main": "General. Be decent.", "lxmf": "Mesh", "lounge": ""}
        self.tiers = {"lounge": "hybrid"}
        self.users = {"alice": {"nick": "alice", "room": "main", "mesh": False}}
        self.inbox = {}                 # key -> [dm dicts]
        self.nonces = {}

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
            if not line.startswith("/"):
                self._post(room, me, line[:400])
                return [], None
            cmd, _, rest = line.partition(" ")
            cmd = cmd.lower()
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
                return ([f"AUTH-OK {h}"] if h else ["AUTH-FAIL signature does not match"]), None
            if cmd in ("/join", "/j"):
                new = re.sub(r"[^a-z0-9]", "-", rest.strip().lstrip("#").lower())[:20]
                if new == room:
                    return [f"you're already in #{room}"], None
                self.rooms.setdefault(new, []); self.topics.setdefault(new, "")
                self._post(room, "*", f"✗ {me}", "system")
                u["room"] = new
                self._post(new, "*", f"✓ {me}", "system")
                return [f"→ #{new}"], new
            if cmd == "/rooms":
                out = []
                for r in sorted(self.rooms):
                    count = sum(1 for x in self.users.values() if x["room"] == r)
                    tier = f" [{self.tiers[r]}]" if r in self.tiers else ""
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
                new = clean(rest.strip())
                self._post(room, "*", f"✎ {me} → {new}", "system")
                u["nick"] = new
                return [], None
            return ["unknown command"], None

    def leave(self, key):
        with self.lock:
            u = self.users.pop(key, None)
            if u:
                self._post(u["room"], "*", f"✗ {'~' if u['mesh'] else ''}{u['nick']}", "system")

    def poll(self, key, since):
        with self.lock:
            u = self.users[key]
            msgs = [m for m in self.rooms[u["room"]] if m["id"] > since]
            dms = [d for d in self.inbox.get(key, []) if d["id"] > since]
            return {"room": u["room"], "nick": u["nick"], "topic": self.topics[u["room"]],
                    "rooms": sorted(self.rooms), "messages": msgs, "dms": dms,
                    "users": [x["nick"] for x in self.users.values() if x["room"] == u["room"]], "stumps": []}


def rns_verify(pub_hex, sig_hex, nonce_hex):
    import RNS
    pk = RNS.Identity(create_keys=False)
    pk.load_public_key(bytes.fromhex(pub_hex))
    if pk.validate(bytes.fromhex(sig_hex), nonce_hex.encode()):
        return RNS.Identity.truncated_hash(bytes.fromhex(pub_hex)).hex()
    return None


def serve_http(port, rrc):
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

        def do_GET(self):
            u = urlparse(self.path)
            if u.path != "/rrc/poll":
                return self._json({"error": "not found"}, 404)
            q = parse_qs(u.query)
            self._json(rrc.poll(self._key(), int(q.get("since", ["0"])[0])))

        def do_POST(self):
            if self.path != "/rrc/send":
                return self._json({"error": "not found"}, 404)
            n = int(self.headers.get("Content-Length", "0"))
            line = self.rfile.read(n).decode("utf-8")
            replies, room = rrc.handle(self._key(), line)
            self._json({"replies": replies, "room": room})

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
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

    def reply(src, text):
        who = RNS.Identity.recall(src)
        t = time.time()
        while who is None and time.time() - t < 15:
            RNS.Transport.request_path(src); time.sleep(0.5); who = RNS.Identity.recall(src)
        d = RNS.Destination(who, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
        router.handle_outbound(LXMF.LXMessage(d, local, text, desired_method=LXMF.LXMessage.OPPORTUNISTIC))

    def on_msg(m):
        key, text = "mesh:" + m.source_hash.hex(), m.content_as_string()
        print(f"NODE got [{m.method}] {text[:70]!r}", flush=True)
        if key in active and time.time() - active[key] > timeout:
            rrc.leave(key)            # went quiet: no longer a room participant
        active[key] = time.time()
        if rrc.arrive(key, names.get(m.source_hash, "guest"), "lxmf", mesh=True) and key not in sent_upto:
            sent_upto[key] = 0          # first contact only: a re-landing doesn't replay history
        if text.startswith("/auth ") and m.method != LXMF.LXMessage.OPPORTUNISTIC:
            return reply(m.source_hash, "AUTH-FAIL test: answer was not a single packet")
        replies, _ = rrc.handle(key, text)
        if replies:
            reply(m.source_hash, "\n".join(replies))

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
                    lines.append(f"[DM] <{d['nick']}>: {d['body']}")
                ids = [x["id"] for x in data["messages"] + data["dms"]]
                if ids:
                    sent_upto[key] = max(ids)
                if lines:
                    reply(bytes.fromhex(key[5:]), "\n".join(lines[:10]))

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
