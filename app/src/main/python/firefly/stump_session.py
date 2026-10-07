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
MESH_LANDING_ROOM = "lxmf"
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
            if isinstance(line, tuple):             # ("voice", nick, mode, frames)
                _, nick, mode, data = line
                line = f"/msg {nick} {voice_label(mode, data)}"
                path = f"/rrc/voice?to={urllib.parse.quote(nick, safe='')}&mode={mode}"
                body_bytes, ctype = data, "application/octet-stream"
            else:
                path, body_bytes, ctype = "/rrc/send", line.encode("utf-8"), "text/plain; charset=utf-8"
            try:
                status, body = self._call("POST", path, body_bytes, content_type=ctype)
                if status == 200:
                    data = json.loads(body.decode("utf-8") or "{}") if body else {}
                    self.session.on_http_replies(data.get("replies") or [], data.get("room"), sent=line)
                elif status == 404:
                    self.session.on_http_error("chat is not offered on this node")
                elif status == 413:
                    self.session.on_http_error("message too large for the node (8 KB max)")
                else:
                    self.session.on_http_error(f"send failed ({status})", sent=line)
            except Exception as e:
                self.session.on_http_error(f"send failed: {e}", sent=line)
            self.wake.set()                 # see our line / its effects on the next poll, now

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
                else:
                    self.session.set_online(False)
            except Exception as e:
                RNS.log(f"Stump poll {self.base} failed: {e}", RNS.LOG_DEBUG)
                self.session.set_online(False)
            # Reference cadence: 2 s, doubled per failure up to 30 s, reset on success, +0..30% jitter.
            self.interval = POLL_BASE_S if ok else min(self.interval * 2, POLL_MAX_S)
            self.wake.wait(self.interval * (1 + random.random() * POLL_JITTER))
            self.wake.clear()


def _attr(obj, name):
    """Kotlin objects expose @JvmField fields; Python doubles expose attributes."""
    v = getattr(obj, name, None)
    if v is None and hasattr(obj, "get" + name.capitalize()):
        v = getattr(obj, "get" + name.capitalize())()
    return v


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
    def __init__(self, core, pipe, name=None):
        self.core = core
        self.store = core.store
        self.pipe = pipe
        self.key = f"{pipe.kind}:{pipe.address}"
        self.lock = threading.RLock()
        node = self.store.stump_node(self.key) or {}
        # Where mesh users land by default is #lxmf; walk-up Wi-Fi visitors share #main.
        self.room = node.get("room") or ("lxmf" if pipe.kind == MESH else "main")
        self.nick = node.get("nick")
        self.auth = None
        self.landing = MESH_LANDING_ROOM if pipe.kind == MESH else "main"
        self.voice_sent_at = {}             # pending voice DM id -> when sent
        self.batch_room = None              # the room named in the title of the batch being read
        self.last_move_at = 0.0             # when the node last told us "→ #room"
        self.move_join_pending = False      # that move's own "✓ me" hasn't arrived yet
        self.join_target, self.join_at = None, 0.0
        self.seen_remote = set()            # HTTP message ids already stored (dedupe)
        self.closed = False                 # set by stop(): late results are dropped
        self._save(name=name or node.get("name") or pipe.address, room=self.room, active=1,
                   last_seen=time.time())
        pipe.start(self)

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

    def set_online(self, online):
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
        msg_id = self.pipe.send(text)
        if self.pipe.kind == MESH and not text.startswith("/"):
            # Over LXMF your own room lines are never echoed back: draw it now,
            # with the LXMF delivery state of the message that carries it.
            self.store.add_line(self.key, self.room, "msg", text, nick=self.my_nick(), mine=True, msg_id=msg_id)
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
        old, self.room = self.room, room
        self._save(room=room)
        if old:
            self.store.move_recent_own_lines(self.key, old, room, time.time() - MOVE_WINDOW_S)
        if why:
            self.store.add_line(self.key, room, "system", why)

    def command(self, line):
        self._note_join(line)
        self.pipe.send(line, control=line.startswith("/auth"))

    def send_dm(self, nick, text):
        nick, text = nick.strip().lstrip("~"), text.strip()
        if not nick or not text:
            return None
        dm_id = self.store.add_dm(self.key, nick, True, text, "pending")
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
        msg_id = self.pipe.send_voice(nick, mode, data)
        if msg_id:
            self.store.update_dm(dm_id, msg_id=msg_id)
        return dm_id

    def start_auth(self):
        with self.lock:
            if self.auth and self.auth.busy and time.time() - self.auth.started < stump.AUTH_WINDOW_S + 30:
                return False
            self.auth = stump.AuthSession(self.key)
            line = self.auth.start()
        self._save(auth="waiting", auth_detail="")
        self.pipe.send(line, control=True)
        return True

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
        fields = {}
        if data.get("room") and data["room"] != self.room:
            self.room = data["room"]
            fields["room"] = self.room
        if data.get("nick"):
            self.nick = data["nick"]
            fields["nick"] = self.nick
        fields["topic"] = data.get("topic") or ""
        node_info = data.get("node")
        if isinstance(node_info, dict) and node_info.get("name"):
            fields["name"] = str(node_info["name"])[:64]     # the node's own name, as on LoRa
        node = self.store.stump_node(self.key) or {}
        known = {r["name"]: r for r in node.get("rooms", [])}
        fields["rooms"] = [known.get(n, {"name": n}) for n in data.get("rooms") or []]
        fields["users"] = data.get("users") or []
        fields["stumps"] = data.get("stumps") or []
        self._save(**fields)
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
    def on_http_replies(self, replies, room, sent=None):
        self.set_online(True)
        if room:
            self.room = room
            self._save(room=room)
            self.store.add_line(self.key, room, "system", f"→ #{room}")
        for r in replies:
            for parsed in stump.parse_message(str(r)):
                if parsed["kind"] == "moved" and room:
                    continue            # already drawn from the structured `room` field
                self._handle(parsed)
        if hasattr(self.pipe, "poke"):
            self.pipe.poke()

    @_while_open
    def on_http_error(self, text, sent=None):
        self.store.add_line(self.key, self.room, "reply", text)
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
            return self.store.add_line(self.key, self.room, "reply", p["text"])
        if k == "slow":
            voice = [dm for dm in self.store.pending_dms(self.key) if dm.get("audio_mode") is not None]
            if voice:
                self.store.update_dm(voice[-1]["id"], state="failed",
                                     reason="too many voice notes too fast; wait a little and send again")
                return
            return self.store.add_line(self.key, self.room, "reply", p.get("text", ""))
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
            self.store.add_line(self.key, self.room, "reply", "AUTH-OK " + (sess.identity_hash or ""))
        elif sess.state == stump.AuthSession.FAILED:
            self._save(auth="failed", auth_detail=sess.reason or "")
            self.store.add_line(self.key, self.room, "reply", "AUTH-FAIL " + (sess.reason or ""))


def _seconds(label):
    """5.0 from "♪ 5.0 s"; a large number if it doesn't parse."""
    try:
        return float(label.replace("♪", "").replace("s", "").strip().replace(",", "."))
    except ValueError:
        return 1e9
