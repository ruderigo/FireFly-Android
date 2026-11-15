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


# ---------------------------------------------------------------- a Stump's own name, any form
# A Stump answers DMs under its name as typed ("LaBuche-Stump-eK0ms"); in its rooms and to
# FireFly it's known by its nick (cleaned, 16 characters at most, maybe tagged:
# "LaBuche-Stump-eK"). Stump 1.0.0-beta.64 counts any form as the Stump itself; so does FireFly,
# or its answer opened a new thread, as someone else (Nov 12). Same rules as the Stump's rrc.py.
STUMP_NICK_LEN = 16
_STUMP_NICK_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_[]{}\\^`|")
_TAG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789")


def bare_nick(n):
    """A nick from /names without its mark: ~ (verified), @ (op) and % (mod) since Stump
    1.0.0-beta.64 (rrcd's), + and & as IRC has them."""
    return (n or "").strip().lstrip("~@%+&")


def stump_nick(name):
    """The nick a Stump makes of a name (rrc.clean_nick): other characters to "-", 16 at most,
    no "-" at the ends."""
    return "".join(ch if ch in _STUMP_NICK_CHARS else "-" for ch in (name or ""))[:STUMP_NICK_LEN].strip("-")


def _tagged_from(nick, base):
    b = base[:STUMP_NICK_LEN - 5]
    return nick == base or (nick.startswith(b + "-") and len(nick) == len(b) + 5
                            and all(c in _TAG_CHARS for c in nick[len(b) + 1:]))


def _made_from(nick, want):
    """Is this nick made from that name: itself, with a tag, or with a number (before beta.60)?"""
    base = want[:STUMP_NICK_LEN - 2]
    return _tagged_from(nick, want) or (nick.startswith(base) and nick[len(base):].isdigit())


def is_stump_itself(nick, name):
    """Is "<nick>" the Stump named `name` (as typed) answering itself? Its name, its nick, or a
    name that cleans to it. A person's nick is already clean, so it can't pass for the Stump."""
    if not nick or not name:
        return False
    who = stump_nick(name)
    if nick in (name, who):
        return True
    c = stump_nick(nick)
    return bool(c) and c != nick and (c == who or _made_from(who, c))


# ---------------------------------------------------------------- fresh or replayed
REAL_CLOCK_SINCE = 1704067200      # 2024-01-01: an announce made later says its sender knows the time


def announce_blob(dest_hash, packet_hash=None):
    """The random blob of the announce Reticulum just took for dest_hash: 5 random bytes, then
    the second its sender made it. None when it can't be told which announce it was."""
    try:
        entry = RNS.Transport.path_table.get(dest_hash)
        if not entry:
            return None
        if packet_hash is not None and len(entry) > 6 and entry[6] != packet_hash:
            return None
        blobs = entry[4]
        return bytes(blobs[-1]) if blobs else None
    except Exception:
        return None


def fresh_announce(memory, dest_hash, blob, is_path_response, window_s, now=None):
    """Did this announce come from its sender just now, or is it a relay's remembered copy?
    (Nov 2026: offline Stumps showed online. Any Reticulum relay answers a path request with the
    last announce it passed on, for days; FireFly took that for hearing the Stump.)
      - one already seen: a copy, not fresh;
      - heard live (not a path response): fresh (a relay passes a live announce on once, as it
        comes in), even if its sender's clock went back (a Heltec Stump restarted);
      - a path response: fresh only if it's newer than the last one from that sender, and,
        when the sender knows the time, made within the window.
    The sender's times are compared with each other, and with the phone's clock only when they
    look real (a Heltec Stump with no internet counts from 2000). memory: {hex: state}."""
    if blob is None:
        return not is_path_response          # can't tell: trust only what's heard live
    m = memory.setdefault(dest_hash.hex(), {"emit": None, "blobs": []})
    if blob in m["blobs"]:
        return False
    m["blobs"] = (m["blobs"] + [blob])[-32:]
    emit = int.from_bytes(blob[5:10], "big")
    before = m["emit"]
    m["emit"] = emit if before is None else max(before, emit)
    if not is_path_response:
        return True
    if before is not None and emit <= before:
        return False
    if emit >= REAL_CLOCK_SINCE:
        return (now or time.time()) - emit <= window_s
    # A clock that isn't real (a Heltec with no internet counts from 2000): newer than one seen
    # before is fresh; the first one this run, by path request, can't be told from a relay's
    # copy: not counted (its next live announce, every 2 min, is).
    return before is not None


# ---------------------------------------------------------------- beacons
class BeaconHandler:
    """Registers with RNS.Transport; calls on_stump(lxmf_hash_hex, name, version)."""
    aspect_filter = f"{BEACON_APP}.{BEACON_ASPECT}"
    receive_path_responses = True

    BEACON_WINDOW_S = 35 * 60          # a Stump beacons every 30 min

    def __init__(self, on_stump):
        self.on_stump = on_stump
        self.seen = {}

    def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash=None,
                          is_path_response=False):
        info = parse_beacon(app_data)
        if info and not beacon_is_own(info[0], announced_identity):
            return              # a beacon naming someone else's LXMF address: forged (Stump 1.0.0-beta.37)
        if info:
            fresh = fresh_announce(self.seen, destination_hash, announce_blob(destination_hash, announce_packet_hash),
                                   is_path_response, self.BEACON_WINDOW_S)
            self.on_stump(*info, beacon_http(app_data), boot=beacon_boot(app_data), fresh=fresh,
                          release=beacon_release(app_data))


# Rooms renamed in Stump 1.0.0-beta.4: #lxmf is #lora-mesh (LoRa phones and handhelds
# land there) and the chain's room is #wifi-mesh. A node still answers to the old
# names, but replies with the new ones; an older node still calls it #lxmf. FireFly
# keeps whatever name the node uses and only treats these as the same room.
ROOM_ALIASES = {"lxmf": "lora-mesh", "lora": "lora-mesh", "chained": "wifi-mesh", "mesh": "wifi-mesh"}
MESH_LANDING = ("lora-mesh", "lxmf", "lora")


def canonical_room(room):
    room = (room or "").strip().lstrip("#").lower()
    return ROOM_ALIASES.get(room, room)


def release_beta(release):
    """1.0.0-beta.71 -> 71; a release without "-beta" (1.0.0) -> a number above every beta;
    unknown or unreadable -> None."""
    m = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?\s*", str(release or ""))
    if not m:
        return None
    if m.group(4) is not None:
        return int(m.group(4)) if m.group(1) == "1" and m.group(2) == "0" and m.group(3) == "0" else 10 ** 6
    return 10 ** 6


# From Stump 1.0.0-beta.71 a LoRa sender whose LXMF signature checks out counts as verified
# for everything (room tiers, /invite, /whoami): FireFly sends no /auth over LoRa at all.
LORA_SIGNED_FROM_BETA = 71


def same_room(a, b):
    return canonical_room(a) == canonical_room(b)


def beacon_is_own(lxmf_hex, identity):
    """A stump.node beacon must name its own identity's LXMF address."""
    try:
        return identity is not None and RNS.Destination.hash(identity, "lxmf", "delivery").hex() == lxmf_hex
    except Exception:
        return False


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


def beacon_http(app_data):
    """The beacon's fifth item since Stump 1.0.0-beta.21: the web addresses it serves
    on, the same list as its STUMP? answer's "http". [] on older Stumps."""
    try:
        data = msgpack.unpackb(app_data)
        extra = data[4] if isinstance(data, list) and len(data) > 4 else None
        return [u for u in extra if isinstance(u, str) and u.startswith("http")][:6] if isinstance(extra, list) else []
    except Exception:
        return []


def beacon_caps(app_data):
    """The beacon's sixth item: the Stump's caps dict ({} when absent or not a dict). Unknown
    keys are kept and ignored: each Stump release may add some."""
    try:
        data = msgpack.unpackb(app_data)
        c = data[5] if isinstance(data, list) and len(data) > 5 else None
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def beacon_boot(app_data):
    """caps["b"] since Stump 1.0.0-beta.43: an id for this boot, new at every restart.
    None on older Stumps (or anything that isn't a short string)."""
    b = beacon_caps(app_data).get("b")
    return b if isinstance(b, str) and 0 < len(b) <= 32 else None


def beacon_release(app_data):
    """caps["r"]: the Stump's full release ("1.0.0-beta.72"). The beacon's version item is its
    short name ("Beta A"), so what it can do goes by this. None when absent."""
    r = beacon_caps(app_data).get("r")
    return r if isinstance(r, str) and 0 < len(r) <= 40 else None


_ADDRESS_TEXT = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?<?([0-9a-f]{32})>?(?:[:/?#].*)?$")


def address_from_text(text):
    """An LXMF address out of what a QR code or a paste gives: the plain 32 hex characters (a
    Stump's /address page, Stump 1.0.0-beta.51), `<…>`, or a link like `lxmf://<address>`
    (anything after the address is ignored). -> 32 lowercase hex, or None."""
    a = parse_address_link(text)
    return a["address"] if a else None


def parse_address_link(text):
    """What a scanned code, a paste or a tapped link says. -> None, or {"address": 32 hex,
    "link": it was a link (lxmf://…), "stump": the name given by stump= (None: not said to be
    a Stump), "key": the 64-byte public key given by key= (None: none), "key_bad": key= was
    there but isn't 64 bytes of base64url}.
    Stump 1.0.0-beta.52: lxmf://<address>?stump=<name, %-encoded UTF-8>&key=<base64url>."""
    from urllib.parse import parse_qs, unquote
    raw = "".join(str(text or "").split())
    m = _ADDRESS_TEXT.match(raw.lower())
    if not m:
        return None
    out = {"address": m.group(1), "link": "://" in raw, "stump": None, "key": None, "key_bad": False,
           "scheme": raw.split("://", 1)[0].lower() if "://" in raw else None}
    # lxma://<address>:<public key> (Columba, MeshChatX, the T-Deck LXMF Messenger): the key
    # after the colon, as hex (128) or base64url (86).
    rest = raw.split("://", 1)[1] if "://" in raw else raw
    after = rest[rest.find(m.group(1)) + 32:] if m.group(1) in rest.lower() else ""
    if after.startswith(":"):
        k = re.split(r"[?#/]", after[1:], maxsplit=1)[0]
        key = _key_bytes(k)
        if key is not None:
            out["key"] = key
        elif k:
            out["key_bad"] = True
    q = raw.split("?", 1)[1].split("#", 1)[0] if "?" in raw else ""
    if q:
        try:
            params = parse_qs(q, keep_blank_values=True)
        except Exception:
            params = {}
        if "stump" in params:
            name = unquote(params["stump"][0]).strip()
            out["stump"] = "".join(ch for ch in name if ch.isprintable())[:40]
        if "key" in params:
            key = _key_bytes(params["key"][0])
            if key is not None:
                out["key"] = key
            else:
                out["key_bad"] = True
    return out


def _key_bytes(k):
    """A 64-byte Reticulum public key written as hex (128) or base64url (86, padding optional)."""
    import base64
    k = (k or "").strip()
    try:
        if len(k) == 128 and all(c in "0123456789abcdefABCDEF" for c in k):
            return bytes.fromhex(k)
        key = base64.urlsafe_b64decode(k + "=" * (-len(k) % 4))
        return key if len(key) == 64 else None
    except Exception:
        return None


def app_tag(version=None, build=None):
    """What FireFly says it is, as the fourth item of its LXMF announce (asked by Project
    Stump, beta.53): a string starting "stump", so a Stump knows a Stump app from Sideband
    over LoRa too and never rewords what FireFly reads. "stump:firefly-android/0.6.0.b.53": the
    app's version (its numbers: 0.6.0-beta -> 0.6.0), then b. and its build."""
    m = re.match(r"\d+(?:\.\d+){0,2}", str(version or ""))
    tag = "stump:firefly-android"         # the handheld says stump:firefly-rk3326/…
    if m:
        parts = (m.group(0).split(".") + ["0", "0"])[:3]
        tag += "/" + ".".join(parts)
        if build:
            tag += f".b.{int(build)}"
    return tag


def with_app_tag(app_data, tag, caps=None):
    """LXMF's own announce data [name, stamp cost, [functions]] with the app tag after it,
    then `caps` when given: what this app can do, {"z": 1} = it reads packed text (ztext.py).
    Upstream LXMF reads the first three items only, so every LXMF app reads it as before."""
    if not app_data or not tag:
        return app_data
    try:
        d = msgpack.unpackb(app_data)
    except Exception:
        return app_data
    if not isinstance(d, list) or len(d) < 3:
        return app_data
    return msgpack.packb(list(d[:3]) + [tag] + ([caps] if caps else []))


def reads_packed(app_data):
    """An LXMF announce whose fifth item says its sender reads packed text ({"z": 1}): a Stump
    since 1.0.0-beta.74, another FireFly."""
    try:
        d = msgpack.unpackb(app_data)
        z = d[4].get("z") if isinstance(d, list) and len(d) > 4 and isinstance(d[4], dict) else None
        return isinstance(z, int) and not isinstance(z, bool) and z >= 1
    except Exception:
        return False


def is_paper_message(text):
    """An LXMF paper message (lxm://<base64url>, Sideband and any LXMF app): a whole encrypted
    message in a QR code or a link, not an address."""
    return "".join(str(text or "").split()).lower().startswith("lxm://")


def identity_for(address_hex, key):
    """The public key a link gives, checked against its address (Stump 1.0.0-beta.52: anyone
    can print a QR code). -> an RNS.Identity whose lxmf.delivery address is `address_hex`,
    or None when it doesn't match (drop the link)."""
    try:
        ident = RNS.Identity(create_keys=False)
        ident.load_public_key(bytes(key))
        return ident if RNS.Destination.hash(ident, "lxmf", "delivery").hex() == address_hex else None
    except Exception:
        return None


def beacon_hash_for(identity):
    """Destination hash of a peer's Stump beacon. request_path() on it to ask 'are you a Stump?'."""
    return RNS.Destination.hash(identity, BEACON_APP, BEACON_ASPECT)


# ---------------------------------------------------------------- line protocol
_DM = re.compile(r"^\[DM\] <([^>]+)>: (.*)$", re.S)
_ROOM_MSG = re.compile(r"^<([^>]+)> (.*)$", re.S)
# "#main ·3 topic", "#vip ·1 [minted] topic"; a Stump on a computer with its own Heltec may
# mark #lora-mesh "[LoRa]" (only LoRa people and whom its owner invites). Any [word] is a tier.
_ROOM_LIST = re.compile(r"^#(\S+) ·(\d+)\s*(\[([A-Za-z-]+)\])?\s*(.*)$")


# Stump 1.0.0-beta.21, hybrid mode: anyone reads every room and writes in #main; a line
# elsewhere (or a private message) before /auth gets this, in the visitor's language
# (stumpid's "auth_required_generic"). It's sentence text, not a token: matched whole.
AUTH_REQUIRED = (
    "auth required for that -- send /auth to verify your identity",
    "identité vérifiée requise — envoie /auth pour te vérifier",
    "se requiere identidad verificada — envía /auth para verificarte",
)


# Stump 1.0.0-beta.38: its LoRa radio (or one along the chain) is down. A line for LoRa is
# kept and goes out when it's back: "⧗ — the radio has been down for N min: your message will
# go out when it's back (kept up to an hour)". Not a refusal. In the person's language.
LORA_DOWN_MARKERS = ("radio has been down", "la radio est coupée", "la radio lleva")

# The billboard over LoRa (Stump 1.0.0-beta.38): /board, /board <id>, /post <title> | <body>.
BOARD_LINE = re.compile(r"^#(\d{6,}(?:\.\d+)?) (.*)$")
POST_OK = re.compile("^\u2713 #(\\d{6,})")
BOARD_NOTHING = ("nothing on the billboard", "rien au babillard", "nada en el tablón")
# Stump 1.0.0-beta.53 names itself in each language: "cette Bûche", "este Tronco" (before: "ce Stump").
BOARD_OFF = ("no billboard on this stump", "pas de babillard sur ce stump", "no hay tablón en este stump",
             "pas de babillard sur cette bûche", "no hay tablón en este tronco")
BOARD_GONE = ("no notice #", "pas d'annonce #", "no hay anuncio #")


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
    if line.startswith("= #"):                          # already in that room (reply to /join, /part)
        return {"kind": "already", "room": line[3:].split()[0] if line[3:].split() else "", "text": line}
    if line.startswith("? "):                           # no such command
        return {"kind": "unknown_command", "text": line}
    if line.startswith("⊘ #"):                          # the room's tier refused you; you stay put
        parts = line[3:].split()
        return {"kind": "tier_refused", "room": parts[0] if parts else "",
                "tier": parts[1] if len(parts) > 1 else "", "text": line}
    if line.strip() in AUTH_REQUIRED:
        return {"kind": "needs_auth", "text": line}  # this needs a verified identity: /auth
    if line.startswith("⧗") and any(m in line.lower() for m in LORA_DOWN_MARKERS):
        return {"kind": "lora_waits", "text": line}     # kept: it goes out when the radio is back
    if line.startswith("⧗"):
        return {"kind": "slow", "text": line}           # slow down: voice notes, or plugin commands
    if line.startswith("⊖ "):
        return {"kind": "no_such_nick", "nick": line[2:].strip()}
    if line.startswith("* "):
        nick, _, text = line[2:].partition(" ")
        return {"kind": "action", "nick": nick, "text": text}
    lora_locked = line.rstrip().endswith("[LoRa]")             # rrc.py appends it after the topic
    if lora_locked:
        line = line.rstrip()[:-len("[LoRa]")].rstrip()
    m = _ROOM_LIST.match(line)
    if m:
        return {"kind": "room", "room": m.group(1), "count": int(m.group(2)),
                "tier": m.group(4) or ("LoRa" if lora_locked else "open"), "topic": m.group(5)}
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
