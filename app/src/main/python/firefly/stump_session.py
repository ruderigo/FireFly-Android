"""A chat session with one Stump node, over the mesh or over Wi-Fi.

CLIENT_QUICKSTART.md: "Treat LoRa/LXMF and WiFi/HTTP as the same protocol
over different pipes, not two different clients." That's this file. A
session owns the node's state (room, rooms, people, your nick, /auth) and
turns every line the node sends into room lines and DM threads in the Store.
Two small pipe classes differ only in how lines travel:

  MeshPipe  LXMF messages to the node's lxmf.delivery address. Replies and
            room traffic come back as LXMF messages, one line per item.
  WifiPipe  POST /rrc/send (replies in the response) and GET /rrc/poll
            (room traffic, incoming DMs), polled at 2 s with backoff+jitter.

DMs follow the quickstart exactly: a DM you send shows as *pending* until the
node's own `[DM] <you>: text` line confirms a recipient was found, or `⊖ nick`
says there's no one by that name.
"""
import json
import re
import queue
import random
import threading
import time
import urllib.parse
import urllib.request

import RNS

from . import stump

MESH, WIFI = "mesh", "wifi"
POLL_BASE_S, POLL_MAX_S, POLL_JITTER = 2.0, 30.0, 0.30
MESH_LANDING_ROOM = "lora-mesh"     # was #lxmf before Stump 1.0.0-beta.4; learned from the node's → #room
HELLO_OK = ("/auth", "/nick", "/help", "/whoami")    # what a Stump takes before the greeting (beta.31)
HELLO_WAIT_S = 25                   # how long a line waits for the greeting before it's "not sent"
GREET_EVERY_S = 20                  # at most one greeting this often, if the Stump keeps asking
BUSY = 503                          # Stump 1.0.0-beta.24: full right now, "Retry-After: 2"
BUSY_RETRY_S = 2
BUSY_TRIES = 3
SLOW_WINDOW_S = 60                  # a ⧗ answers something we sent within this
MOVE_WINDOW_S = 60        # a move's own "✓ me" arrives within this; our lines sent this recently can be re-filed
JOIN_WINDOW_S = 120       # how long a reply can still be the answer to our /join
DM_COMMANDS = ("/msg ", "/m ", "/w ")
VOICE_MIN_MS, VOICE_MAX_MS = 600, 15000       # the node's limits for a voice DM
OPUS_REFUSAL_WINDOW_S = 60                      # how long after sending an Opus DM a sentence can be its refusal


def voice_label(mode, data):
    """How the node writes a voice note in a DM line: ♪ 5.0 s. The length comes from
    the note itself: whole Codec 2 frames, or the Ogg pages of an Opus file."""
    from .audio import duration_ms, label
    return label(duration_ms(mode, data) or 0)


def mesh_key(node_hex): return f"{MESH}:{node_hex}"
def wifi_key(base): return f"{WIFI}:{normalize_base(base)}"


def normalize_base(base):
    base = (base or "").strip().rstrip("/")
    if base and "://" not in base:
        base = "http://" + base
    return base


# ====================================================================== pipes
class MeshPipe:
    kind = MESH

    def __init__(self, core, node_hex):
        self.core, self.node_hex = core, node_hex

    @property
    def address(self): return self.node_hex

    def start(self, session): self.session = session
    def stop(self): pass

    def send(self, line, control=False):
        """Returns the LXMF message id, so room lines and DMs can show delivery to the node."""
        if control:
            # /auth must stay one packet: empty title, no fields, opportunistic, never propagated.
            if stump_size(line) > stump.SINGLE_PACKET_LIMIT:
                raise ValueError("control line does not fit in one packet")
            return self.core.send(self.node_hex, line, method="opportunistic")
        return self.core.send(self.node_hex, line, method="auto", allow_propagation=False)

    def send_voice(self, nick, mode, data):
        return self.core.send(self.node_hex, f"/msg {nick}", method="auto", allow_propagation=False,
                              audio=(mode, data))


class UrllibHttp:
    """Default HTTP transport (desktop, tests). On Android, Kotlin supplies one
    bound to the Wi-Fi network, so a hotspot without internet still works."""

    def request(self, method, url, headers_json, body, timeout_ms):
        headers = json.loads(headers_json) if headers_json else {}
        req = urllib.request.Request(url, data=bytes(body) if body is not None else None,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout_ms / 1000.0) as r:
                return _Result(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Result(e.code, e.read() or b"")
        except Exception as e:
            return _Result(-1, str(e).encode())


    def udpAsk(self, targets_json, port, payload, wait_ms):
        """Send `payload` over UDP to each target and collect answers for wait_ms.
        "broadcast" is 255.255.255.255 (FIREFLY_BROADCAST, comma-separated, overrides
        it: the tests' loopback Stumps). -> JSON [{"from", "data"}]"""
        import os, socket
        targets = []
        for t in json.loads(targets_json or "[]"):
            if t == "broadcast":
                targets += [x.strip() for x in os.environ.get("FIREFLY_BROADCAST", "255.255.255.255").split(",") if x.strip()]
            else:
                targets.append(t)
        out = []
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("0.0.0.0", 0))
            for t in targets:
                try:
                    s.sendto(bytes(payload), (t, int(port)))
                except OSError:
                    pass
            end = time.time() + wait_ms / 1000.0
            while True:
                left = end - time.time()
                if left <= 0:
                    break
                s.settimeout(left)
                try:
                    data, addr = s.recvfrom(4096)
                except (socket.timeout, OSError):
                    break
                out.append({"from": addr[0], "data": data.decode("utf-8", "replace")})
        finally:
            s.close()
        return json.dumps(out)


class _Result:
    def __init__(self, status, body): self.status, self.body = status, body


class WifiPipe:
    kind = WIFI

    def __init__(self, base, http):
        self.base = normalize_base(base)
        self.http = http
        self.since = 0
        self.running = False
        self.outq = queue.Queue()
        self.session = None
        self.interval = POLL_BASE_S
        self.wake = threading.Event()

    @property
    def address(self): return self.base

    def start(self, session):
        self.session = session
        self.running = True
        threading.Thread(target=self._poll_loop, daemon=True, name="stump-poll").start()
        threading.Thread(target=self._send_loop, daemon=True, name="stump-send").start()

    def stop(self):
        self.running = False
        self.outq.put(None)
        self.wake.set()

    def send(self, line, control=False):
        if control:
            # /auth, /nick of the greeting: right away, not behind lines that wait for it.
            threading.Thread(target=self._send_one, args=(line,), daemon=True, name="stump-control").start()
        else:
            self.outq.put(line)
        return None

    def send_voice(self, nick, mode, data):
        self.outq.put(("voice", nick, int(mode), bytes(data)))
        return None

    def fetch_voice(self, dm_id):
        status, body = self._call("GET", f"/rrc/voice?id={int(dm_id)}", timeout_ms=15000)
        return body if status == 200 and body else None

    def poke(self):
        self.wake.set()

    # ---------------------------------------------------------------- http
    def _call(self, method, path, body=None, timeout_ms=8000, content_type="text/plain; charset=utf-8"):
        headers = {}
        if body is not None:
            headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        r = self.http.request(method, self.base + path, json.dumps(headers), body, timeout_ms)
        status = int(_attr(r, "status"))
        raw = _attr(r, "body")
        return status, bytes(raw) if raw is not None else b""

    def _send_loop(self):
        while self.running:
            line = self.outq.get()
            if line is None:
                break
            self._send_one(line)

    def _send_one(self, line):
        item = line                             # as queued: resent as is if the Stump moved
        if isinstance(line, tuple):             # ("voice", nick, mode, frames)
            _, nick, mode, data = line
            line = f"/msg {nick} {voice_label(mode, data)}"
            path = f"/rrc/voice?to={urllib.parse.quote(nick, safe='')}&mode={mode}"
            body_bytes, ctype = data, "application/octet-stream"
        else:
            path, body_bytes, ctype = "/rrc/send", line.encode("utf-8"), "text/plain; charset=utf-8"
        greeting_line = isinstance(item, str) and item.strip().lower().split(" ", 1)[0] in HELLO_OK
        if not greeting_line and self.session and not self.session.wait_hello():
            self.session.on_http_error("not sent: FireFly couldn't introduce itself to this Stump (/auth, /nick)",
                                       sent=line, unsent=True)
            return
        try:
            status, body = self._call("POST", path, body_bytes, content_type=ctype)
            if status == 200 and not greeting_line and b'"hello_required"' in body:
                # The Stump wants the greeting first (a new session): greet, then send it again.
                self.session.hello_needed()
                if self.session.wait_hello():
                    status, body = self._call("POST", path, body_bytes, content_type=ctype)
            for _ in range(BUSY_TRIES):             # 503 busy: the Stump is full right now
                if status != BUSY or not self.running:
                    break
                time.sleep(BUSY_RETRY_S)
                status, body = self._call("POST", path, body_bytes, content_type=ctype)
            if status == 200:
                data = json.loads(body.decode("utf-8") or "{}") if body else {}
                if data.get("error") == "hello_required":
                    self.session.on_http_error("not sent: this Stump wants FireFly to introduce itself first; "
                                               "tap Verify me, then send again", sent=line, unsent=True)
                else:
                    self.session.on_http_replies(data.get("replies") or [], data.get("room"), sent=line,
                                                 hello=data.get("hello"))
            elif status == 404:
                self.session.on_http_error("chat is not offered on this node")
            elif status == 413:
                self.session.on_http_error("message too large for the node (8 KB max)")
            elif status == BUSY:
                self.session.on_http_error("not sent: the Stump is busy (too many connections); tap to send again",
                                           sent=line, unsent=True)
            elif status < 0:
                if not self._resend_elsewhere(item, line):
                    self.session.on_http_error(f"not sent: {net_reason(body)}", sent=line, unsent=True)
            else:
                self.session.on_http_error(f"send failed (HTTP {status})", sent=line, unsent=True)
        except Exception as e:
            if not self._resend_elsewhere(item, line):
                self.session.on_http_error(f"not sent: {net_reason(str(e))}", sent=line, unsent=True)
        self.wake.set()                 # see our line / its effects on the next poll, now

    def hello(self, identity, name, prefer, known_lxmf=None):
        """/hello (Stump 1.0.0-beta.32): both sides prove who they are, in one exchange.
        -> (outcome, answer): "done", "name" (done, but the name is someone else's),
        "refused" (403), "forged" (the Stump's signature doesn't check out), "unsupported"
        (an older Stump, or one without identities: use /auth + /nick), "unreachable"."""
        def call(method, body=None):
            for _ in range(4):
                status, raw = self._call(method, "/hello", body, timeout_ms=8000,
                                         content_type="application/json") if body is not None \
                    else self._call(method, "/hello", timeout_ms=8000)
                if status == 429 or (status == BUSY and raw.strip() == b"busy"):
                    try:
                        wait = float(json.loads(raw.decode("utf-8")).get("retry_after") or 2)
                    except Exception:
                        wait = BUSY_RETRY_S
                    time.sleep(min(max(wait, 0.5), 10))
                    continue
                return status, raw
            return status, raw

        status, raw = call("GET")
        if status < 0:
            return "unreachable", None
        try:
            d = json.loads(raw.decode("utf-8")) if raw[:1] == b"{" else None
        except Exception:
            d = None
        if status != 200 or not isinstance(d, dict) or not d.get("nonce"):
            return "unsupported", d
        nonce, lx = str(d["nonce"]), str(d.get("lxmf") or "").lower()
        if d.get("pub") and d.get("sig"):
            try:
                them = RNS.Identity(create_keys=False)
                them.load_public_key(bytes.fromhex(d["pub"]))
                ok = them.validate(bytes.fromhex(d["sig"]), (nonce + lx).encode("ascii")) and \
                    RNS.Destination.hash(them, "lxmf", "delivery").hex() == lx
            except Exception:
                ok = False
            if not ok:
                return "forged", d
        elif known_lxmf:
            return "forged", d                  # a Stump we know always signs
        if known_lxmf and lx != known_lxmf.lower():
            return "other", d                   # a real Stump, but another one: the router moved them
        body = json.dumps({"pub": identity.get_public_key().hex(),
                           "sig": identity.sign(nonce.encode("ascii")).hex(),
                           "name": name, "prefer": prefer}).encode("utf-8")
        status, raw = call("POST", body)
        try:
            a = json.loads(raw.decode("utf-8")) if raw[:1] == b"{" else {}
        except Exception:
            a = {}
        if status == 200 and a.get("hello") == "done":
            return ("name" if a.get("error") == "name" else "done"), a
        if status == 403:
            return "refused", a
        if status < 0:
            return "unreachable", a
        return "unsupported", a

    def _resend_elsewhere(self, item, line):
        """Nothing answered: maybe the router gave the Stump a new address. Find it by
        its identity and send this there. A /auth isn't carried over (its challenge
        belongs to this session): Verify again over there."""
        moved = self.session.relocate() if self.session else None
        if moved is None:
            return False
        if isinstance(line, str) and line.lower().startswith(("/auth", "/nick")):
            return True                     # the greeting: the session at the new address greets by itself
        moved.pipe.outq.put(item)
        moved.pipe.poke()
        return True

    def _poll_loop(self):
        while self.running:
            ok = False
            try:
                room = urllib.parse.quote(self.session.room or "main", safe="")
                status, body = self._call("GET", f"/rrc/poll?room={room}&since={self.since}", timeout_ms=10000)
                if status == 200:
                    data = json.loads(body.decode("utf-8"))
                    ids = [m.get("id", 0) for m in (data.get("messages") or []) + (data.get("dms") or [])]
                    self.session.on_http_poll(data)
                    if ids:
                        self.since = max(self.since, max(ids))
                    ok = True
                elif status == 404:
                    self.session.on_http_error("chat is not offered on this node")
                elif status == BUSY:
                    ok = None                           # full right now (Retry-After: 2): ask again soon
                elif status < 0:
                    self.session.set_online(False, net_reason(body))
                    if self.session.relocate():         # found at a new address: this pipe is done
                        break
                else:
                    self.session.set_online(False, f"HTTP {status}: not a Stump's chat")
            except Exception as e:
                RNS.log(f"Stump poll {self.base} failed: {e}", RNS.LOG_DEBUG)
                self.session.set_online(False, net_reason(str(e)))
                if self.session.relocate():
                    break
            # Reference cadence: 2 s, doubled per failure up to 30 s, reset on success, +0..30% jitter.
            self.interval = (POLL_BASE_S if ok else BUSY_RETRY_S if ok is None
                             else min(self.interval * 2, POLL_MAX_S))
            self.wake.wait(self.interval * (1 + random.random() * POLL_JITTER))
            self.wake.clear()


def net_reason(body):
    """Why a request got no HTTP answer at all (WifiHttp gives status -1 and the
    exception's text), in words: refused, timed out, nothing at that address."""
    t = (bytes(body).decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")).strip()
    low = t.lower()
    if "refused" in low:
        return "connection refused: nothing is listening at that address"
    if "ehostunreach" in low or "no route to host" in low:
        return "no device at that address on this Wi-Fi"
    if "enetunreach" in low or "network is unreachable" in low:
        return "this Wi-Fi can't reach that address"
    if "timed out" in low or "timeout" in low or re.search(r"after \d+ ?ms", low):
        return "no answer (timed out)"
    if "unable to resolve host" in low or "no address associated" in low:
        return "can't find that name"
    if "cleartext" in low:
        return "plain HTTP refused by the phone"
    return t[:140] or "no answer"


def _attr(obj, name):
    """Kotlin objects expose @JvmField fields; Python doubles expose attributes."""
    v = getattr(obj, name, None)
    if v is None and hasattr(obj, "get" + name.capitalize()):
        v = getattr(obj, "get" + name.capitalize())()
    return v


def _is_hash(v):
    return isinstance(v, str) and len(v) == 32 and all(c in "0123456789abcdefABCDEF" for c in v)


def stump_size(line):
    from .core import single_packet_size
    return single_packet_size(line)


# ====================================================================== session
def _while_open(handler):
    """Run a handler for what the node sent only while the session is open, under its lock."""
    import functools

    @functools.wraps(handler)
    def run(self, *args, **kwargs):
        with self.lock:
            if self.closed:
                return None
            return handler(self, *args, **kwargs)
    return run


class StumpSession:
    def __init__(self, core, pipe, name=None, carry=None):
        self.core = core
        self.store = core.store
        self.pipe = pipe
        self.key = f"{pipe.kind}:{pipe.address}"
        self.lock = threading.RLock()
        node = self.store.stump_node(self.key) or {}
        # Where mesh users land by default is #lora-mesh (#lxmf on older nodes); walk-up Wi-Fi
        # visitors share #main. The node's own replies correct this either way.
        self.room = node.get("room") or (MESH_LANDING_ROOM if pipe.kind == MESH else "main")
        self.nick = node.get("nick")
        self.auth = None
        self.landing = MESH_LANDING_ROOM if pipe.kind == MESH else "main"
        self.voice_sent_at = {}             # pending voice DM id -> when sent
        self.batch_room = None              # the room named in the title of the batch being read
        self.last_move_at = 0.0             # when the node last told us "→ #room"
        self.move_join_pending = False      # that move's own "✓ me" hasn't arrived yet
        self.join_target, self.join_at = None, 0.0
        self.seen_remote = set()            # HTTP message ids already stored (dedupe)
        if carry:                           # the same Stump at a new address: what we already have
            self.seen_remote |= set(carry.get("seen") or ())
            if hasattr(pipe, "since"):
                pipe.since = max(pipe.since, int(carry.get("since") or 0))
        self.closed = False                 # set by stop(): late results are dropped
        self.last_command_at = 0.0          # our last /command or !plugin line (⧗ can answer either)
        self.last_sent = None               # the last thing we sent: {"kind", "id", "text", "at"} (what a ⧗ refused)
        self._reply_to = None               # the line an HTTP reply answers, while it's handled
        self.auto_auth_tried = pipe.kind != WIFI       # Wi-Fi: /auth once connected (Stump 1.0.0-beta.25)
        # The greeting (Stump 1.0.0-beta.31): an app without cookies says who it is before it
        # chats, /auth then /nick <its name>. None: the Stump doesn't ask; "required"; "done".
        # Over Wi-Fi it's "unknown" until the first answer says (lines wait for it, briefly).
        self.hello = "unknown" if pipe.kind == WIFI else None
        self.hello_ok = threading.Event()
        if self.hello is None:
            self.hello_ok.set()
        self.greeted_at = 0.0
        self.greeting_nick = None           # the /nick line we sent as part of the greeting
        self.hello_v2 = False               # this Stump speaks /hello (beta.32)
        self.auth_quiet, self.auth_before = False, None
        self.folded = set()                 # "renamed" pairs already applied
        self.offline_reason = None          # why the last Wi-Fi poll got nothing ("connection refused"…)
        self.hw = None                      # the node's radio state from the poll (Stump 1.0.0-beta.5+)
        self._save(name=name or node.get("name") or pipe.address, room=self.room, active=1,
                   last_seen=time.time())
        pipe.start(self)
        if pipe.kind == WIFI:
            self.greet()                    # say who we are first (Stump 1.0.0-beta.31; harmless before)

    @property
    def transport(self): return self.pipe.kind

    def stop(self):
        """After this, nothing from the node is stored any more: a poll or a reply still
        in flight when the Stump is hidden or removed must not write it back."""
        self.pipe.stop()
        with self.lock:
            self._save(online=0)
            self.closed = True

    def _save(self, **fields):
        self.store.upsert_stump_node(self.key, self.pipe.kind, self.pipe.address, **fields)

    def relocate(self):
        """Not answering at its address: the session of the same Stump at its new one, or None."""
        if self.pipe.kind != WIFI:
            return None
        if self.closed:
            # Already followed to its new address (by another line that failed first): the
            # lines still in flight here go there too.
            moved = self.core.resolve(self.key)
            return self.core.sessions.get(moved) if moved != self.key else None
        try:
            return self.core.relocate(self.key)
        except Exception as e:
            RNS.log(f"Stump relocate {self.key} failed: {e}", RNS.LOG_WARNING)
            return None

    def set_online(self, online, reason=None):
        self.offline_reason = None if online else reason
        if not online and getattr(self, "hello", None) == "unknown":
            self._hello_not_asked()         # unreachable: lines fail with the real reason, not after a wait
        self._save(online=1 if online else 0, **({"last_seen": time.time()} if online else {}))

    # ------------------------------------------------------------ outgoing
    def post(self, text):
        """Anything typed in the room box: plain text to the room, or a /command."""
        text = text.strip()
        if not text:
            return
        low = text.lower()
        if low.startswith(DM_COMMANDS):
            parts = text.split(" ", 2)
            if len(parts) == 3 and parts[1].strip():
                return self.send_dm(parts[1].lstrip("~"), parts[2])
        if low == "/auth":
            return self.start_auth()
        self._note_join(text)
        if text.startswith(("/", "!")):
            self.last_command_at = time.time()
            self.last_sent = {"kind": "command", "id": None, "text": text, "at": time.time()}
        else:
            self.last_sent = {"kind": "line", "id": None, "text": text, "at": time.time(), "room": self.room}
        msg_id = self.pipe.send(text)
        if self.pipe.kind == MESH and not text.startswith("/"):
            # Over LXMF your own room lines are never echoed back: draw it now,
            # with the LXMF delivery state of the message that carries it.
            line_id = self.store.add_line(self.key, self.room, "msg", text, nick=self.my_nick(), mine=True, msg_id=msg_id)
            if self.last_sent["kind"] == "line":
                self.last_sent["id"] = line_id
        elif text.startswith("/"):
            self.store.add_line(self.key, self.room, "command", text, nick=self.my_nick(), mine=True,
                                msg_id=msg_id)

    def join(self, room):
        room = room.strip().lstrip("#")
        if room:
            self._note_join(f"/join {room}")
            self.pipe.send(f"/join {room}")

    def _note_join(self, line):
        parts = line.split()
        if parts and parts[0].lower() in ("/join", "/j") and len(parts) > 1:
            self.join_target, self.join_at = parts[1].lstrip("#").lower(), time.time()

    def _set_room(self, room, why=None):
        """Move our idea of the room, filing any of our own lines that were sent
        in the last moments (to the room we wrongly believed in) where they landed."""
        room = room.lstrip("#")
        if room == self.room:
            return
        if self.room and stump.same_room(room, self.room):
            # The same room under its new name (#lxmf -> #lora-mesh): its history follows.
            self.store.rename_room(self.key, self.room, room)
            if stump.same_room(self.landing, room):
                self.landing = room
            self.room = room
            self._save(room=room)
            return
        old, self.room = self.room, room
        self._save(room=room)
        if old:
            self.store.move_recent_own_lines(self.key, old, room, time.time() - MOVE_WINDOW_S)
        if why:
            self.store.add_line(self.key, room, "system", why)

    def command(self, line):
        self._note_join(line)
        self.last_command_at = time.time()
        self.pipe.send(line, control=line.startswith("/auth"))

    def send_dm(self, nick, text):
        nick, text = nick.strip().lstrip("~"), text.strip()
        if not nick or not text:
            return None
        dm_id = self.store.add_dm(self.key, nick, True, text, "pending")
        self.last_sent = {"kind": "dm", "id": dm_id, "text": text, "at": time.time()}
        msg_id = self.pipe.send(f"/msg {nick} {text}")
        if msg_id:
            self.store.update_dm(dm_id, msg_id=msg_id)
        return dm_id

    def send_voice_dm(self, nick, mode, data):
        """A voice note as a DM, relayed by the node: Opus or Codec 2, as for any chat.
        Pending until its [DM] <me>: ♪ … line."""
        from .audio import STUMP_MAX_VOICE_BYTES, valid_voice
        nick, data, mode = nick.strip().lstrip("~"), bytes(data), int(mode)
        if not nick or not valid_voice(mode, data) or len(data) > STUMP_MAX_VOICE_BYTES:
            raise ValueError("a voice DM is 0.6 to 15 s of Opus or Codec 2, at most 16 KB")
        dm_id = self.store.add_dm(self.key, nick, True, voice_label(mode, data), "pending", audio=(mode, data))
        # The node refuses a voice note with a sentence, not a token: remember when we
        # sent, so such a sentence can fail this note with its words.
        self.voice_sent_at[dm_id] = time.time()
        self.last_sent = {"kind": "voice", "id": dm_id, "text": None, "at": time.time()}
        msg_id = self.pipe.send_voice(nick, mode, data)
        if msg_id:
            self.store.update_dm(dm_id, msg_id=msg_id)
        return dm_id

    def start_auth(self, quiet=False):
        if self.pipe.kind == MESH and not self.core.can_reach(self.pipe.address):
            # No radio and no other way to this Stump: the challenge could never arrive.
            self._save(auth="failed", auth_detail="no radio, and no other way to reach this Stump")
            return False
        with self.lock:
            if self.auth and self.auth.busy and time.time() - self.auth.started < stump.AUTH_WINDOW_S + 30:
                return False
            self.auth = stump.AuthSession(self.key)
            self.auth_quiet = quiet             # on connecting: no lines in the room, no "failed"
            self.auth_before = (self.store.stump_node(self.key) or {}).get("auth")
            line = self.auth.start()
        self._save(auth="waiting", auth_detail="")
        self.pipe.send(line, control=True)
        return True

    def hello_needed(self):
        """The Stump asks for the greeting (a poll or a send said "hello": "required")."""
        self.hello = "required"
        self.hello_ok.clear()
        self.greet()

    def _hello_done(self):
        self.hello = "done"
        self.hello_ok.set()

    def wait_hello(self, secs=HELLO_WAIT_S):
        """For a line about to go out: True once the Stump has our greeting (or never asked)."""
        return self.hello_ok.wait(secs) if self.hello in ("required", "unknown") else True

    @staticmethod
    def _replies_answer(sent):
        return sent is not None

    def _hello_not_asked(self):
        """The Stump answered without "hello": one from before the greeting."""
        if self.hello == "unknown":
            self.hello = None
            self.hello_ok.set()

    def greet(self, force=False):
        """Say who we are: /hello (Stump 1.0.0-beta.32), else /auth (quietly) whose AUTH-OK
        sends /nick <our name> if the Stump still needs one."""
        now = time.time()
        if not force and now - self.greeted_at < GREET_EVERY_S:
            return
        self.greeted_at = now
        self.auto_auth_tried = True
        if self.pipe.kind != WIFI:
            return self.start_auth(quiet=True)
        threading.Thread(target=self._hello_exchange, daemon=True, name="stump-hello").start()

    def _hello_exchange(self):
        name = stump.clean_nick(self.core.settings["display_name"])
        prefer = self.core.settings.get("stump_prefer", "wifi")
        known = (self.store.stump_node(self.key) or {}).get("lxmf")
        try:
            outcome, a = self.pipe.hello(self.core.identity, name, prefer, known)
        except Exception as e:
            RNS.log(f"Stump /hello {self.key} failed: {e}", RNS.LOG_WARNING)
            outcome, a = "unreachable", None
        if self.closed:
            return
        if outcome in ("done", "name"):
            self.hello_v2 = True
            with self.lock:
                if a.get("nick"):
                    self.nick = str(a["nick"])
                self._save(auth="ok", auth_detail=self.core.identity.hash.hex(),
                           **({"nick": self.nick} if self.nick else {}))
            self._hello_done()
            if outcome == "name":               # greeted, but under another name: say why
                self.store.add_line(self.key, self.room, "reply", str(a.get("message") or
                                    f"the name {name} is someone else's on this Stump: choose another in Settings"))
        elif outcome == "unsupported":
            self.hello_v2 = False
            self.start_auth(quiet=True)         # an older Stump: /auth, then /nick if it asks
        elif outcome == "forged":
            self._save(auth="failed", auth_detail="this Stump's signature doesn't check out: "
                                                  "it isn't the Stump it claims to be, so FireFly didn't introduce itself")
        elif outcome == "other":
            self._save(auth="none", auth_detail="")
            self.set_online(False, f"another Stump answers at this address now ({(a or {}).get('stump') or 'unknown'})")
            threading.Thread(target=self.relocate, daemon=True).start()     # find ours by its identity
        elif outcome == "refused":
            self._save(auth="failed", auth_detail=str((a or {}).get("message") or "the Stump refused FireFly's signature"))
        # "unreachable": the poll says so, and the next "required" greets again

    def _greeting_nick(self):
        """After AUTH-OK in a greeting: our name, unless the Stump already has it (from LoRa)."""
        name = stump.clean_nick(self.core.settings["display_name"])
        if self.hello == "required" and name:
            self.greeting_nick = f"/nick {name}"
            self.pipe.send(self.greeting_nick, control=True)

    def rename(self, name):
        """The user chose a new name in Settings: tell this Stump (over Wi-Fi the announce
        doesn't reach it). Over LoRa the next announce does it."""
        if self.pipe.kind == WIFI and self.hello_v2:
            return self.greet(force=True)       # /hello again, with the new name
        if self.pipe.kind == WIFI and self.hello is not None and name:
            self.greeting_nick = f"/nick {name}"
            self.pipe.send(self.greeting_nick, control=True)

    def _fold_renamed(self, renamed):
        """"renamed": {"old": "new"} (the last 30 minutes): someone changed nick (or a LoRa
        contact announced a new name). Their private conversation follows the new name,
        and so does a block. Our own rename is the poll's "nick"."""
        for old, new in list(renamed.items())[:50]:
            old, new = str(old).lstrip("~"), str(new).lstrip("~")
            if not old or not new or old == new or (old, new) in self.folded:
                continue
            self.folded.add((old, new))
            keys = self.core.same_stump(self.key)
            self.store.rename_dm_nick(keys, old, new)
            sid = self.core.stump_id(self.key)
            if old.lower() in self.store.stump_blocked(sid):
                self.store.block_stump_nick(sid, new)

    def auth_timed_out(self):
        """A verification nobody answered (out of reach): say so instead of waiting forever."""
        with self.lock:
            if self.auth and self.auth.busy and time.time() - self.auth.started > stump.AUTH_WINDOW_S + 30:
                self.auth = None
                if self.auth_quiet:             # our own try on connecting: no news is no failure
                    self._save(auth=self.auth_before or "none", auth_detail="")
                else:
                    self._save(auth="failed", auth_detail="no answer from the node")

    def my_nick(self):
        if self.nick:
            return self.nick
        if self.pipe.kind == MESH:
            return stump.clean_nick(self.core.settings["display_name"])
        return None

    @_while_open
    def on_lxmf_failed(self, msg_id):
        """The LXMF message carrying a DM never reached the node."""
        for dm in self.store.pending_dms(self.key):
            if dm["msg_id"] == msg_id:
                self.store.update_dm(dm["id"], state="failed", reason="did not reach the node")

    # ------------------------------------------------------------ incoming: mesh
    @_while_open
    def on_mesh_content(self, content, audio=None, title=None):
        self.set_online(True)
        # Each pushed batch names its room in the LXMF title (#lxmf, #main); a batch of
        # DMs or replies has none. Lines queued just before a /join keep their own room.
        t = (title or "").strip()
        self.batch_room = t[1:] if t.startswith("#") and len(t) > 1 else None
        try:
            self._handle_batch(content, audio)
        finally:
            self.batch_room = None

    def _handle_batch(self, content, audio):
        for parsed in stump.parse_message(content):
            if parsed["kind"] == "dm":
                self._handle_dm(parsed["author"], parsed["text"], audio=audio)
            else:
                self._handle(parsed)

    # ------------------------------------------------------------ incoming: wifi
    @_while_open
    def on_http_poll(self, data):
        self.set_online(True)
        if data.get("hello") == "required":
            # Nothing to read until we've said who we are (no messages, people or rooms in this
            # answer): keep what we show, and greet. Not a sign of anything wrong.
            self.hello_needed()
            return
        if data.get("hello") == "done":
            self._hello_done()
        elif data.get("hello") is None:
            self._hello_not_asked()
        fields = {}
        if data.get("room") and data["room"] != self.room:
            self.room = data["room"]
            fields["room"] = self.room
        if data.get("nick"):
            self.nick = data["nick"]
            fields["nick"] = self.nick
        fields["topic"] = data.get("topic") or ""
        hw = data.get("hw")
        if isinstance(hw, dict) and hw.get("state") in ("none", "online", "connecting", "error"):
            self.hw = {"state": hw["state"], "title": str(hw.get("title") or "")[:300]}
        node_info = data.get("node")
        if isinstance(node_info, dict) and node_info.get("name"):
            fields["name"] = str(node_info["name"])[:64]     # the node's own name, as on LoRa
        if isinstance(node_info, dict) and _is_hash(node_info.get("lxmf")):
            fields["lxmf"] = node_info["lxmf"].lower()        # matches this node's LoRa entry
            if fields["lxmf"] != (self.store.stump_node(self.key) or {}).get("lxmf"):
                # The same Stump at a new address (a router hands out another one): this
                # entry replaces the old one, which would only ever be offline now.
                self.core.retire_old_wifi(fields["lxmf"], keep=self.key)
        node = self.store.stump_node(self.key) or {}
        known = {r["name"]: r for r in node.get("rooms", [])}
        fields["rooms"] = [known.get(n, {"name": n}) for n in data.get("rooms") or []]
        fields["users"] = data.get("users") or []
        fields["stumps"] = data.get("stumps") or []
        self._save(**fields)
        renamed = data.get("renamed")
        if isinstance(renamed, dict):
            self._fold_renamed(renamed)
        if not self.auto_auth_tried and data.get("hello") is None:      # a Stump from before the greeting
            # One phone on Wi-Fi and LoRa is one person on the Stump only once it proves its
            # identity over Wi-Fi: so /auth as soon as we're connected, not only when asked.
            self.auto_auth_tried = True
            if (self.store.stump_node(self.key) or {}).get("auth") != "waiting":
                self.start_auth(quiet=True)
        for m in data.get("messages") or []:
            if m.get("id") in self.seen_remote:
                continue
            self.seen_remote.add(m.get("id"))
            kind = m.get("kind", "msg")
            body = m.get("body", "")
            if kind == "system":
                p = stump.parse_line(body)
                if p["kind"] == "rename" and p["old"].lstrip("~") == self.nick:
                    self.nick = p["new"].lstrip("~")
                    self._save(nick=self.nick)
                self.store.add_line(self.key, self.room, "system", body, remote_id=m.get("id"), ts=m.get("ts"))
            elif kind == "dm":
                self._incoming_dm(m.get("nick", "?"), body, ts=m.get("ts"))
            else:
                self.store.add_line(self.key, self.room, "action" if kind == "action" else "msg", body,
                                    nick=m.get("nick"), mine=(m.get("nick") == self.nick),
                                    remote_id=m.get("id"), ts=m.get("ts"))
        for d in data.get("dms") or []:
            if d.get("id") in self.seen_remote:
                continue
            self.seen_remote.add(d.get("id"))
            audio = None
            voice = d.get("voice")
            if isinstance(voice, dict) and hasattr(self.pipe, "fetch_voice"):
                try:
                    frames = self.pipe.fetch_voice(d.get("id"))
                except Exception:
                    frames = None
                # Without the frames it still shows as a voice note, marked as not playable.
                audio = (int(voice.get("mode", 4)), frames) if frames else (int(voice.get("mode", 4)), None)
            self._incoming_dm(d.get("nick", "?"), d.get("body", ""), ts=d.get("ts"), audio=audio)

    @_while_open
    def on_http_replies(self, replies, room, sent=None, hello=None):
        self.set_online(True)
        if hello == "done":
            self._hello_done()
        elif hello == "required":
            self.hello = "required"         # (read by AUTH-OK: is a /nick still needed?)
            self.hello_ok.clear()
        elif self.hello == "unknown" and self._replies_answer(sent):
            self._hello_not_asked()
        if sent is not None and sent == self.greeting_nick:
            self.greeting_nick = None
            if hello != "done":             # refused (the name is taken?): say so, once
                for r in replies:
                    self.store.add_line(self.key, self.room, "reply", str(r))
            return
        if room:
            self.room = room
            self._save(room=room)
            self.store.add_line(self.key, room, "system", f"→ #{room}")
        self._reply_to = sent           # over HTTP a reply names the line it answers (a ⧗ refused that one)
        try:
            for r in replies:
                for parsed in stump.parse_message(str(r)):
                    if parsed["kind"] == "moved" and room:
                        continue            # already drawn from the structured `room` field
                    self._handle(parsed)
        finally:
            self._reply_to = None
        if hasattr(self.pipe, "poke"):
            self.pipe.poke()

    @_while_open
    def on_http_error(self, text, sent=None, unsent=False):
        """The node didn't take `sent` (no answer, or an HTTP error). Only a ⧗ from the
        node means "too fast"; this is drawn as "not sent", with the reason beside it."""
        self.store.add_line(self.key, self.room, "reply", text)
        if unsent and sent and sent.lower().startswith("/auth"):
            self.auth = None
            self._save(auth="failed", auth_detail=text)
            return
        if unsent and sent and not sent.startswith(("/", "!")):
            line_id = self.store.add_line(self.key, self.room, "msg", sent, nick=self.my_nick(), mine=True)
            self.store.set_line_state(line_id, "error")
            return
        if sent and sent.lower().startswith(DM_COMMANDS):
            nick = sent.split(" ", 2)[1] if len(sent.split(" ", 2)) > 1 else ""
            for dm in self.store.pending_dms(self.key):
                if dm["nick"] == nick:
                    self.store.update_dm(dm["id"], state="failed", reason=text)
                    break

    # ------------------------------------------------------------ the line protocol
    def _handle(self, p):
        k = p["kind"]
        if k.startswith("auth_"):
            return self._handle_auth(p)
        if k == "dm":
            return self._handle_dm(p["author"], p["text"])
        if k == "already":
            if p["room"]:
                self._set_room(p["room"])
            self.join_target = None
            return self.store.add_line(self.key, self.room, "reply", p["text"])
        if k == "tier_refused":
            self.join_target = None                     # refused: we stay where we are
            return self.store.add_line(self.key, self.room, "reply", p["text"])
        if k == "unknown_command":
            if self.auth_quiet and self.auth and self.auth.busy and "/auth" in p["text"]:
                self.auth = None                # a node without /auth: nothing to prove there
                return self._save(auth=self.auth_before or "none", auth_detail="")
            return self.store.add_line(self.key, self.room, "reply", p["text"])
        if k == "needs_auth":                         # not "auth_…": those are the /auth tokens
            return self._needs_auth(p["text"])
        if k == "slow":
            # ⧗ is the node saying "slow down": lines too fast (3 at once, then one every 2 s),
            # too many voice notes, plugin commands or wrong passwords. It names nothing: it
            # refused the last thing we sent, which wasn't sent (offer to resend).
            return self._slowed(p.get("text", ""))
        if k == "no_such_nick":
            nick = p["nick"].lstrip("~")
            for dm in self.store.pending_dms(self.key):
                if dm["nick"].lower() == nick.lower():
                    self.store.update_dm(dm["id"], state="failed", reason=f"no one here is called {nick}")
                    return
            return self.store.add_line(self.key, self.room, "reply", f"⊖ {nick}")
        if k == "moved":
            self.last_move_at = time.time()
            self.move_join_pending = True
            if not (self.join_target and time.time() - self.join_at < JOIN_WINDOW_S) and self.pipe.kind == MESH:
                # Not a /join of ours: the node landed us. That's the landing room, by its real
                # name on this node (#lora-mesh, #lxmf on older ones, #wifi-mesh over a chain).
                self.landing = p["room"].lstrip("#")
            self.join_target = None
            self._set_room(p["room"])
            return self.store.add_line(self.key, self.room, "system", f"→ #{self.room}")
        if k == "room":
            node = self.store.stump_node(self.key) or {}
            rooms = {r["name"]: r for r in node.get("rooms", [])}
            rooms[p["room"]] = {"name": p["room"], "count": p["count"], "tier": p["tier"], "topic": p["topic"]}
            return self._save(rooms=sorted(rooms.values(), key=lambda r: r["name"]))
        if k == "names":
            if p["room"] == self.room:
                self._save(users=p["names"])
            return self.store.add_line(self.key, self.room, "reply", f"#{p['room']}: " + ", ".join(p["names"]))
        if k == "rename":
            if p["old"].lstrip("~") == self.my_nick():
                self.nick = p["new"].lstrip("~")
                self._save(nick=self.nick)
            return self.store.add_line(self.key, self._line_room(), "system", f"✎ {p['old']} → {p['new']}")
        if k == "topic":
            if p["room"] == self.room:
                self._save(topic=p["topic"])
            return self.store.add_line(self.key, self._line_room(), "system", f"✎ #{p['room']} {p['topic']}")
        if k in ("join", "part"):
            sym = "✓" if k == "join" else "✗"
            me = p["nick"].lstrip("~") == self.my_nick()
            if k == "join" and me and self.pipe.kind == MESH:
                if self.move_join_pending and time.time() - self.last_move_at < MOVE_WINDOW_S:
                    self.move_join_pending = False      # the "✓ me" of the move we just made
                else:
                    # We (re)joined without being moved: after going quiet the node
                    # drops mesh peers from their room, and the next message lands
                    # them in the landing room. It says only "✓ ~me", no "→ #room".
                    self.move_join_pending = False
                    # The batch's title says which room (a tiered #lxmf can land you in #main).
                    self._set_room(self.batch_room or self.landing)
            return self.store.add_line(self.key, self._line_room(), "system", f"{sym} {p['nick']}")
        if k == "action":
            return self.store.add_line(self.key, self._line_room(), "action", p["text"], nick=p["nick"])
        if k == "msg":
            text, room = p["text"], self._line_room()
            return self.store.add_line(self.key, room, "msg", text, nick=p["nick"],
                                       mine=(p["nick"] == self.nick))
        # Anything else is a sentence from the node (help, errors), in its own
        # language. If it names the room we just asked for ("tu es déjà dans
        # #lxmf"), we are in that room whatever we believed.
        text = p.get("text", "")
        if self._refused_voice(text):
            return
        if (self.join_target and time.time() - self.join_at < JOIN_WINDOW_S
                and f"#{self.join_target}" in text.lower() and "→" not in text):
            self._set_room(self.join_target)
            self.join_target = None
        return self.store.add_line(self.key, self.room, "reply", text)

    def _line_room(self):
        """Where a pushed room line belongs: the room its batch names, else where we are."""
        return self.batch_room or self.room

    # Words that appear in a node's voice-note refusals in any language: "(Opus or) Codec 2
    # expected", "must be 0.6 to 15 seconds". Any other sentence (help, a late-message
    # notice) leaves pending notes alone. (The "/msg <who>" hint can't apply: FireFly
    # always sends /msg <nick> with its notes.)
    VOICE_REFUSAL_MARKERS = ("Opus", "Codec", "0.6", "0,6")

    def _slowed(self, text):
        sent = getattr(self, "_reply_to", None)
        if sent is not None:                                    # Wi-Fi: we know exactly what was refused
            return self._refused(sent, text)
        last, self.last_sent = self.last_sent, None
        if not last or time.time() - last["at"] > SLOW_WINDOW_S:
            return self.store.add_line(self.key, self.room, "reply", text)
        if last["kind"] == "voice":
            self.voice_sent_at.pop(last["id"], None)
            self.store.update_dm(last["id"], state="failed",
                                 reason="too many voice notes too fast; wait a little and send again")
            return None
        if last["kind"] == "dm":
            self.store.update_dm(last["id"], state="failed", reason="sent too fast; wait a moment and send it again")
            return None
        if last["kind"] == "line":
            if last["id"]:                                      # over LoRa: already drawn, mark it
                self.store.set_line_state(last["id"], "failed")
            else:                                               # over Wi-Fi: it never arrives; draw it as not sent
                line_id = self.store.add_line(self.key, last.get("room") or self.room, "msg", last["text"],
                                              nick=self.my_nick(), mine=True)
                self.store.set_line_state(line_id, "failed")
            return None
        return self.store.add_line(self.key, self.room, "reply", text)      # a command: show the node's line

    def _needs_auth(self, text):
        """Hybrid (Stump 1.0.0-beta.21): the line we just sent needs a verified identity
        (a room other than #main, or a private message). Over Wi-Fi we know which line:
        it's kept, marked "verify first", and a tap sends it again once verified."""
        sent = getattr(self, "_reply_to", None)
        if sent is None or sent.startswith(("/", "!")) and not sent.lower().startswith(DM_COMMANDS):
            return self.store.add_line(self.key, self.room, "reply", text)
        if sent.lower().startswith(DM_COMMANDS):
            parts = sent.split(" ", 2)
            nick = parts[1].lstrip("~") if len(parts) > 1 else ""
            body = parts[2] if len(parts) > 2 else ""
            for dm in reversed(self.store.pending_dms(self.key)):
                if dm["nick"].lower() == nick.lower() and (dm["body"] == body or dm.get("audio_mode") is not None):
                    self.voice_sent_at.pop(dm["id"], None)
                    self.store.update_dm(dm["id"], state="failed",
                                         reason="this Stump takes private messages from verified people: tap ‘Verify me’, then send again")
                    return None
            return self.store.add_line(self.key, self.room, "reply", text)
        line_id = self.store.add_line(self.key, self.room, "msg", sent, nick=self.my_nick(), mine=True)
        self.store.set_line_state(line_id, "auth")
        return None

    def _refused(self, sent, text):
        """The node refused this exact line with ⧗: it wasn't sent."""
        if sent.lower().startswith(DM_COMMANDS):
            parts = sent.split(" ", 2)
            nick = parts[1].lstrip("~") if len(parts) > 1 else ""
            body = parts[2] if len(parts) > 2 else ""
            for dm in reversed(self.store.pending_dms(self.key)):
                if dm["nick"].lower() == nick.lower() and dm["body"] == body:
                    voice = dm.get("audio_mode") is not None
                    if voice:
                        self.voice_sent_at.pop(dm["id"], None)
                    self.store.update_dm(dm["id"], state="failed", reason=(
                        "too many voice notes too fast; wait a little and send again" if voice
                        else "sent too fast; wait a moment and send it again"))
                    return None
            return self.store.add_line(self.key, self.room, "reply", text)
        if sent.startswith(("/", "!")):
            return self.store.add_line(self.key, self.room, "reply", text)          # a command: the node's line
        line_id = self.store.add_line(self.key, self.room, "msg", sent, nick=self.my_nick(), mine=True)
        self.store.set_line_state(line_id, "failed")
        return None

    def _slowed_voice_note(self):
        """The pending voice note a ⧗ stopped: our most recent one, if it went out in the
        last minute and after our last command (otherwise the ⧗ is about the command)."""
        now = time.time()
        recent = {i: at for i, at in self.voice_sent_at.items() if now - at <= OPUS_REFUSAL_WINDOW_S}
        notes = [dm for dm in self.store.pending_dms(self.key)
                 if dm.get("audio_mode") is not None and dm["id"] in recent]
        if not notes:
            return None
        note = max(notes, key=lambda dm: recent[dm["id"]])
        return note if recent[note["id"]] > self.last_command_at else None

    def _refused_voice(self, text):
        """A voice-note refusal right after a voice DM that's still pending: fail that note."""
        now = time.time()
        for dm_id, at in list(self.voice_sent_at.items()):
            if now - at > OPUS_REFUSAL_WINDOW_S:
                self.voice_sent_at.pop(dm_id, None)
        if not text or not any(m in text for m in self.VOICE_REFUSAL_MARKERS):
            return False
        pending = [dm for dm in self.store.pending_dms(self.key) if dm["id"] in self.voice_sent_at]
        if not pending:
            return False
        dm = pending[-1]
        self.voice_sent_at.pop(dm["id"], None)
        self.store.update_dm(dm["id"], state="failed", reason=f"the node refused it: {text}")
        return True

    def _handle_dm(self, author, text, audio=None):
        author = author.lstrip("~")
        mine = author == self.my_nick()
        match = None
        pending = self.store.pending_dms(self.key)
        for dm in pending:
            if dm["body"] == text and (mine or self.nick is None):
                match = dm
                break
        if match is None and text.startswith("♪") and (mine or self.nick is None):
            # A voice confirmation names only the length; if our rounding differs from the
            # node's, take the closest pending voice note (it never says who it went to).
            voice = [dm for dm in pending if dm.get("audio_mode") is not None]
            if voice:
                want = _seconds(text)
                match = min(voice, key=lambda dm: abs(_seconds(dm["body"]) - want))
        if match is not None:
            # The node's echo of our own DM: a recipient by that name exists.
            self.store.update_dm(match["id"], state="delivered")
            self.voice_sent_at.pop(match["id"], None)
            if not self.nick or self.nick != author:
                self.nick = author          # the first confirmation shows our exact nick
                self._save(nick=author)
            return
        if mine:
            return                          # a confirmation we already matched (duplicate)
        self._incoming_dm(author, text, audio=audio if text.startswith("♪") else None)

    def _incoming_dm(self, nick, text, ts=None, audio=None):
        nick = nick.lstrip("~")
        if self.core.stump_blocks(self.key, nick):
            return                          # blocked here: dropped, no notification
        self.store.add_dm(self.key, nick, False, text, "received", unread=True, ts=ts, audio=audio)
        self.core.emit({"type": "stump_dm", "node": self.key, "nick": nick, "text": text,
                        "node_name": (self.store.stump_node(self.key) or {}).get("name")})

    def _handle_auth(self, p):
        with self.lock:
            sess = self.auth
            if sess is None:
                return
            reply = sess.on_line(p, self.core.identity)
        if reply:
            self.pipe.send(reply, control=True)
        if sess.state == stump.AuthSession.OK:
            self._save(auth="ok", auth_detail=sess.identity_hash or "")
            if self.hello == "required":
                self._greeting_nick()           # the second half of the greeting
            if not self.auth_quiet:
                self.store.add_line(self.key, self.room, "reply", "AUTH-OK " + (sess.identity_hash or ""))
        elif sess.state == stump.AuthSession.FAILED:
            if self.auth_quiet:
                self._save(auth=self.auth_before or "none", auth_detail="")
            else:
                self._save(auth="failed", auth_detail=sess.reason or "")
                self.store.add_line(self.key, self.room, "reply", "AUTH-FAIL " + (sess.reason or ""))


def _seconds(label):
    """5.0 from "♪ 5.0 s"; a large number if it doesn't parse."""
    try:
        return float(label.replace("♪", "").replace("s", "").strip().replace(",", "."))
    except ValueError:
        return 1e9
