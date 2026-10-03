"""Foundations for LaBuche-Stump nodes.

Nothing here is required for normal LXMF messaging: a Stump node is just an
LXMF peer. This module adds what a client needs to understand one:

- beacon detection (the `stump.node` announce on the node's identity)
- a parser for Stump's line protocol (symbols, DMs, AUTH tokens)
- the `/auth` challenge answer, built from the standard Reticulum identity
- the nick cleaning rules a node applies to announced display names

Reference: CLIENT_QUICKSTART.md and COMMANDS.md from the Stump project.
"""
import re
import time

import RNS
import RNS.vendor.umsgpack as msgpack

BEACON_APP, BEACON_ASPECT = "stump", "node"
AUTH_WINDOW_S = 60              # fixed on the node
SINGLE_PACKET_LIMIT = 295       # LXMF opportunistic content limit (title + content + fields)

_NICK_OK = re.compile(r"[A-Za-z0-9\-_\[\]{}\\^`|]")


def clean_nick(name, maxlen=16):
    """What a Stump node will turn an announced display name into (before any numeric suffix)."""
    out = "".join(c if _NICK_OK.match(c) else "-" for c in (name or ""))
    return out[:maxlen] or "guest"


# ---------------------------------------------------------------- beacons
class BeaconHandler:
    """Registers with RNS.Transport; calls on_stump(lxmf_hash_hex, name, version)."""
    aspect_filter = f"{BEACON_APP}.{BEACON_ASPECT}"
    receive_path_responses = True

    def __init__(self, on_stump):
        self.on_stump = on_stump

    def received_announce(self, destination_hash, announced_identity, app_data):
        info = parse_beacon(app_data)
        if info:
            self.on_stump(*info)


def parse_beacon(app_data):
    """["stump", version, node name, 16-byte LXMF hash] -> (lxmf_hex, name, version) or None."""
    try:
        data = msgpack.unpackb(app_data)
        if not isinstance(data, list) or len(data) < 4 or data[0] != "stump":
            return None
        lxmf_hash = data[3]
        if not isinstance(lxmf_hash, (bytes, bytearray)) or len(lxmf_hash) != 16:
            return None
        return lxmf_hash.hex(), str(data[2]), str(data[1])
    except Exception:
        return None


def beacon_hash_for(identity):
    """Destination hash of a peer's Stump beacon. request_path() on it to ask 'are you a Stump?'."""
    return RNS.Destination.hash(identity, BEACON_APP, BEACON_ASPECT)


# ---------------------------------------------------------------- line protocol
_DM = re.compile(r"^\[DM\] <([^>]+)>: (.*)$", re.S)
_ROOM_MSG = re.compile(r"^<([^>]+)> (.*)$", re.S)
_ROOM_LIST = re.compile(r"^#(\S+) ·(\d+)\s*(\[(minted|hybrid)\])?\s*(.*)$")


def parse_line(line):
    """Classify one line from a Stump node. Returns a dict with at least 'kind'."""
    line = line.rstrip("\r")
    head, _, rest = line.partition(" ")
    if head in ("AUTH-CHALLENGE", "AUTH-OK", "AUTH-FAIL"):   # wire tokens, never translated
        return {"kind": head.lower().replace("-", "_"), "arg": rest.strip()}
    m = _DM.match(line)
    if m:
        return {"kind": "dm", "author": m.group(1), "text": m.group(2)}
    if line.startswith("✓ "):
        return {"kind": "join", "nick": line[2:].strip()}
    if line.startswith("✗ "):
        return {"kind": "part", "nick": line[2:].strip()}
    if line.startswith("✎ "):
        body = line[2:]
        if body.startswith("#"):
            room, _, topic = body.partition(" ")
            return {"kind": "topic", "room": room[1:], "topic": topic}
        old, _, new = body.partition(" → ")
        return {"kind": "rename", "old": old.strip(), "new": new.strip()}
    if line.startswith("→ #"):
        return {"kind": "moved", "room": line[3:].strip()}
    if line.startswith("⊖ "):
        return {"kind": "no_such_nick", "nick": line[2:].strip()}
    if line.startswith("* "):
        nick, _, text = line[2:].partition(" ")
        return {"kind": "action", "nick": nick, "text": text}
    m = _ROOM_LIST.match(line)
    if m:
        return {"kind": "room", "room": m.group(1), "count": int(m.group(2)),
                "tier": m.group(4) or "open", "topic": m.group(5)}
    if line.startswith("#") and ": " in line:
        room, _, names = line.partition(": ")
        return {"kind": "names", "room": room[1:], "names": [n.strip() for n in names.split(",") if n.strip()]}
    m = _ROOM_MSG.match(line)
    if m:
        return {"kind": "msg", "nick": m.group(1), "text": m.group(2)}
    return {"kind": "text", "text": line}


def parse_message(content):
    """A Stump LXMF message may carry several lines, one item each."""
    return [parse_line(l) for l in content.split("\n") if l.strip()]


def is_mesh_nick(nick):
    """'~rod' marks a mesh user in room activity; the '~' is not part of the nick."""
    return nick.startswith("~")


# ---------------------------------------------------------------- /auth
def auth_answer(identity, nonce_hex):
    """'/auth <pubkey_hex> <sig_hex>' for a challenge.

    The public key is the standard Reticulum identity key: X25519 public (32)
    + Ed25519 public (32) = 128 hex chars. The signature is Ed25519 over the
    ASCII text of the nonce, NOT the bytes it decodes to.
    """
    nonce_hex = nonce_hex.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", nonce_hex):
        raise ValueError("challenge nonce must be 32 hex characters")
    pub = identity.get_public_key().hex()
    sig = identity.sign(nonce_hex.encode("ascii")).hex()
    return f"/auth {pub} {sig}"


class AuthSession:
    """One /auth handshake with one node. Only one may be in flight per node,
    because a second /auth silently replaces the pending challenge."""
    IDLE, WAITING_CHALLENGE, WAITING_RESULT, OK, FAILED = range(5)

    def __init__(self, node_hash, max_restarts=2):
        self.node_hash = node_hash
        self.state = self.IDLE
        self.started = None
        self.challenge_at = None
        self.identity_hash = None
        self.reason = None
        self.restarts = 0
        self.max_restarts = max_restarts
        self.rtt = None           # seconds from /auth sent to challenge received

    @property
    def busy(self):
        return self.state in (self.WAITING_CHALLENGE, self.WAITING_RESULT)

    def start(self):
        self.state = self.WAITING_CHALLENGE
        self.started = time.time()
        self.reason = None
        return "/auth"

    def on_line(self, parsed, identity):
        """Feed a parsed line. Returns a message to send (or None)."""
        k = parsed["kind"]
        if k == "auth_challenge" and self.state == self.WAITING_CHALLENGE:
            self.challenge_at = time.time()
            self.rtt = self.challenge_at - self.started
            self.state = self.WAITING_RESULT
            return auth_answer(identity, parsed["arg"])
        if k == "auth_ok" and self.state == self.WAITING_RESULT:
            self.state = self.OK
            self.identity_hash = parsed["arg"]
            return None
        if k == "auth_fail" and self.busy:
            self.reason = parsed["arg"]
            if "expired" in self.reason and self.restarts < self.max_restarts:
                self.restarts += 1
                return self.start()           # every challenge is single-use: just start over
            self.state = self.FAILED
        return None
