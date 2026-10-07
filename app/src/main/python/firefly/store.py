"""SQLite storage. Thread-safe: one lock, one connection.

`version` increases on every write, so the app can refresh only when
something actually changed.

Plain LXMF lives in `peers` and `messages` (same schema as FireFly on the
handheld). Stump chat lives in its own three tables, because a Stump node is
not a conversation: it is rooms, people and private threads behind one address.
"""
import json
import sqlite3

from .audio import duration_ms as _voice_ms
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS peers (
    hash        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,         -- lxmf | propagation | nomadnode
    name        TEXT,
    last_heard  REAL,
    hops        INTEGER,
    saved       INTEGER DEFAULT 0,
    stump       TEXT,                  -- Stump node name if it beacons as one
    stump_ver   TEXT,
    stamp_cost  INTEGER,
    extra       TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    peer        TEXT NOT NULL,
    outgoing    INTEGER NOT NULL,
    content     TEXT NOT NULL,
    title       TEXT,
    ts          REAL NOT NULL,
    state       TEXT NOT NULL,         -- pending stamping sending sent delivered stored failed received
    method      TEXT,
    lxm_hash    TEXT,
    rssi        REAL,
    snr         REAL,
    verified    INTEGER DEFAULT 1,
    attachments TEXT,
    unread      INTEGER DEFAULT 0,
    reason      TEXT,
    audio_mode  INTEGER,               -- LXMF.AM_* of a voice note, else NULL
    audio       BLOB,                  -- the encoded voice note
    audio_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS messages_peer ON messages(peer, ts);
CREATE INDEX IF NOT EXISTS messages_hash ON messages(lxm_hash);

CREATE TABLE IF NOT EXISTS blocked (
    hash        TEXT PRIMARY KEY,      -- LXMF address: works without ever hearing them again
    name        TEXT,                  -- last known name, if any
    ts          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stump_nodes (
    key          TEXT PRIMARY KEY,     -- mesh:<lxmf hex>  or  wifi:<base url>
    transport    TEXT NOT NULL,        -- mesh | wifi
    address      TEXT NOT NULL,
    name         TEXT,
    room         TEXT,
    nick         TEXT,
    topic        TEXT,
    rooms        TEXT,                 -- JSON [{name, count, tier, topic}]
    users        TEXT,                 -- JSON [nick]
    stumps       TEXT,                 -- JSON [nick] of users that are Stump nodes
    auth         TEXT,                 -- none | waiting | ok | failed
    auth_detail  TEXT,
    online       INTEGER DEFAULT 0,
    last_seen    REAL,
    active       INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS stump_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node        TEXT NOT NULL,
    room        TEXT NOT NULL,
    kind        TEXT NOT NULL,         -- msg action system reply
    nick        TEXT,
    body        TEXT NOT NULL,
    ts          REAL NOT NULL,
    mine        INTEGER DEFAULT 0,
    msg_id      INTEGER,               -- our LXMF message carrying it (mesh), for delivery state
    remote_id   INTEGER
);
CREATE INDEX IF NOT EXISTS stump_lines_room ON stump_lines(node, room, id);
CREATE TABLE IF NOT EXISTS stump_dms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node        TEXT NOT NULL,
    nick        TEXT NOT NULL,         -- the other person
    outgoing    INTEGER NOT NULL,
    body        TEXT NOT NULL,
    ts          REAL NOT NULL,
    state       TEXT NOT NULL,         -- pending delivered failed received
    reason      TEXT,
    msg_id      INTEGER,
    unread      INTEGER DEFAULT 0,
    audio_mode  INTEGER,
    audio       BLOB,
    audio_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS stump_dms_thread ON stump_dms(node, nick, id);
"""

ROOM_LOG_KEEP = 400   # per room; the node itself keeps only 60


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        for table in ("messages", "stump_dms"):                # databases from older versions
            cols = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for col, kind in (("audio_mode", "INTEGER"), ("audio", "BLOB"), ("audio_ms", "INTEGER")):
                if col not in cols:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
        self.db.commit()
        self.version = 0

    def _write(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            self.version += 1
            return cur

    def _read(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # ---------------------------------------------------------------- peers
    def upsert_peer(self, hash_hex, kind, name=None, hops=None, heard=True, **extra):
        with self.lock:
            row = self.db.execute("SELECT hash FROM peers WHERE hash=?", (hash_hex,)).fetchone()
            now = time.time() if heard else None
            if row is None:
                self._write("INSERT INTO peers(hash, kind, name, last_heard, hops) VALUES (?,?,?,?,?)",
                            (hash_hex, kind, name, now, hops))
            else:
                sets, args = [], []
                if name:
                    sets.append("name=?"); args.append(name)
                if hops is not None:
                    sets.append("hops=?"); args.append(hops)
                if now:
                    sets.append("last_heard=?"); args.append(now)
                if sets:
                    self._write(f"UPDATE peers SET {', '.join(sets)} WHERE hash=?", (*args, hash_hex))
            for k, v in extra.items():
                if k in ("stump", "stump_ver", "stamp_cost", "saved", "extra"):
                    self._write(f"UPDATE peers SET {k}=? WHERE hash=?", (v, hash_hex))

    def peer(self, hash_hex):
        rows = self._read("SELECT * FROM peers WHERE hash=?", (hash_hex,))
        return rows[0] if rows else None

    def peers(self, kind="lxmf"):
        return self._read("SELECT * FROM peers WHERE kind=? ORDER BY saved DESC, last_heard DESC", (kind,))

    def delete_peer(self, hash_hex):
        self._write("DELETE FROM peers WHERE hash=?", (hash_hex,))

    # ---------------------------------------------------------------- messages
    def add_message(self, peer, outgoing, content, state, ts=None, title="", method=None, lxm_hash=None,
                    rssi=None, snr=None, verified=True, attachments=None, unread=False, audio=None):
        audio_mode, audio_bytes = (audio if audio else (None, None))
        cur = self._write(
            "INSERT INTO messages(peer, outgoing, content, title, ts, state, method, lxm_hash, rssi, snr,"
            " verified, attachments, unread, audio_mode, audio, audio_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (peer, 1 if outgoing else 0, content, title, ts or time.time(), state, method, lxm_hash,
             rssi, snr, 1 if verified else 0, attachments, 1 if unread else 0,
             audio_mode, sqlite3.Binary(audio_bytes) if audio_bytes is not None else None,
             _voice_ms(audio_mode, audio_bytes)))
        return cur.lastrowid

    def has_message(self, lxm_hash):
        return bool(self._read("SELECT 1 FROM messages WHERE lxm_hash=?", (lxm_hash,)))

    def update_message(self, msg_id, **fields):
        allowed = {"state", "method", "lxm_hash", "reason"}
        sets = [(k, v) for k, v in fields.items() if k in allowed]
        if sets:
            self._write(f"UPDATE messages SET {', '.join(k + '=?' for k, _ in sets)} WHERE id=?",
                        (*[v for _, v in sets], msg_id))

    def message(self, msg_id):
        rows = self._read("SELECT id, peer, outgoing, content, state, method, reason, audio_mode FROM messages WHERE id=?",
                          (msg_id,))
        return rows[0] if rows else None

    def messages(self, peer, limit=300):
        """Without the audio bytes (they're fetched one note at a time, to play)."""
        rows = self._read("""SELECT id, peer, outgoing, content, title, ts, state, method, lxm_hash, rssi, snr,
                                    verified, attachments, unread, reason, audio_mode, length(audio) AS audio_len,
                                    audio_ms
                             FROM messages WHERE peer=? ORDER BY ts DESC, id DESC LIMIT ?""", (peer, limit))
        return rows[::-1]

    def message_audio(self, msg_id):
        with self.lock:
            row = self.db.execute("SELECT audio_mode, audio FROM messages WHERE id=?", (msg_id,)).fetchone()
        return (row[0], bytes(row[1])) if row and row[1] is not None else (None, None)

    def mark_read(self, peer):
        with self.lock:
            if self.db.execute("SELECT 1 FROM messages WHERE peer=? AND unread=1", (peer,)).fetchone():
                self._write("UPDATE messages SET unread=0 WHERE peer=?", (peer,))

    def conversations(self):
        """Plain LXMF conversations. Stump nodes are listed separately (stump_nodes)."""
        return self._read("""
            SELECT m.peer, m.content, m.ts, m.outgoing, m.state, m.audio_mode,
                   (SELECT COUNT(*) FROM messages u WHERE u.peer=m.peer AND u.unread=1) AS unread,
                   p.name, p.hops
            FROM messages m LEFT JOIN peers p ON p.hash=m.peer
            WHERE m.id = (SELECT id FROM messages x WHERE x.peer=m.peer ORDER BY ts DESC, id DESC LIMIT 1)
              AND (p.stump IS NULL OR p.stump = '')
            ORDER BY m.ts DESC""")

    def delete_conversation(self, peer):
        """The whole conversation: every message, and the contact itself (name, saved,
        last heard). They can still write, and reappear in "heard" if they announce."""
        with self.lock:
            self.db.execute("DELETE FROM messages WHERE peer=?", (peer,))
            self.db.execute("DELETE FROM peers WHERE hash=? AND kind='lxmf'", (peer,))
            self.db.commit()
            self.version += 1

    def delete_stump(self, key, address=None):
        """Everything about a Stump on this phone: rooms, DMs, the node itself; for a mesh
        node also its raw LXMF traffic and what we knew of it as a peer."""
        with self.lock:
            self.db.execute("DELETE FROM stump_lines WHERE node=?", (key,))
            self.db.execute("DELETE FROM stump_dms WHERE node=?", (key,))
            self.db.execute("DELETE FROM stump_nodes WHERE key=?", (key,))
            if address and key.startswith("mesh:"):
                self.db.execute("DELETE FROM messages WHERE peer=?", (address,))
                self.db.execute("DELETE FROM peers WHERE hash=?", (address,))
            self.db.commit()
            self.version += 1

    # ---------------------------------------------------------------- blocked people
    def block(self, hash_hex, name=None):
        self._write("INSERT OR REPLACE INTO blocked(hash, name, ts) VALUES (?,?,?)", (hash_hex, name, time.time()))

    def unblock(self, hash_hex):
        self._write("DELETE FROM blocked WHERE hash=?", (hash_hex,))

    def blocked(self):
        return self._read("SELECT * FROM blocked ORDER BY ts DESC")

    def hidden_stump_count(self):
        return self._read("SELECT COUNT(*) AS n FROM stump_nodes WHERE active=0")[0]["n"]

    def restore_hidden_stumps(self):
        self._write("UPDATE stump_nodes SET active=1 WHERE active=0")

    def pending_outgoing(self):
        return self._read("SELECT id FROM messages WHERE outgoing=1 AND state IN ('pending','stamping','sending')")

    # ---------------------------------------------------------------- stump nodes
    def upsert_stump_node(self, key, transport, address, **fields):
        allowed = {"name", "room", "nick", "topic", "rooms", "users", "stumps", "auth", "auth_detail",
                   "online", "last_seen", "active"}
        with self.lock:
            if not self.db.execute("SELECT 1 FROM stump_nodes WHERE key=?", (key,)).fetchone():
                self._write("INSERT INTO stump_nodes(key, transport, address, auth) VALUES (?,?,?,'none')",
                            (key, transport, address))
            sets = []
            for k, v in fields.items():
                if k not in allowed:
                    continue
                if k in ("rooms", "users", "stumps") and not isinstance(v, str):
                    v = json.dumps(v, ensure_ascii=False)
                sets.append((k, v))
            if sets:
                cur = self.db.execute("SELECT " + ", ".join(k for k, _ in sets) + " FROM stump_nodes WHERE key=?",
                                      (key,)).fetchone()
                changed = [(k, v) for (k, v), old in zip(sets, cur) if old != v]
                if changed:
                    self._write(f"UPDATE stump_nodes SET {', '.join(k + '=?' for k, _ in changed)} WHERE key=?",
                                (*[v for _, v in changed], key))

    def stump_node(self, key):
        rows = self._read("SELECT * FROM stump_nodes WHERE key=?", (key,))
        return _decode_node(rows[0]) if rows else None

    def stump_nodes(self):
        rows = self._read("""
            SELECT n.*,
                   (SELECT COUNT(*) FROM stump_dms d WHERE d.node=n.key AND d.unread=1) AS unread_dms
            FROM stump_nodes n WHERE n.active=1 ORDER BY n.last_seen DESC""")
        return [_decode_node(r) for r in rows]

    # ---------------------------------------------------------------- stump room log
    def add_line(self, node, room, kind, body, nick=None, mine=False, msg_id=None, remote_id=None, ts=None):
        cur = self._write(
            "INSERT INTO stump_lines(node, room, kind, nick, body, ts, mine, msg_id, remote_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (node, room, kind, nick, body, ts or time.time(), 1 if mine else 0, msg_id, remote_id))
        line_id = cur.lastrowid
        if line_id % 50 == 0:
            self._write("""DELETE FROM stump_lines WHERE node=? AND room=? AND id <= (
                               SELECT id FROM stump_lines WHERE node=? AND room=?
                               ORDER BY id DESC LIMIT 1 OFFSET ?)""",
                        (node, room, node, room, ROOM_LOG_KEEP))
        return line_id

    def move_recent_own_lines(self, node, from_room, to_room, since_ts):
        with self.lock:
            if self.db.execute("SELECT 1 FROM stump_lines WHERE node=? AND room=? AND mine=1 AND ts>=?",
                               (node, from_room, since_ts)).fetchone():
                self._write("UPDATE stump_lines SET room=? WHERE node=? AND room=? AND mine=1 AND ts>=?",
                            (to_room, node, from_room, since_ts))

    def lines(self, node, room, limit=200):
        rows = self._read("""
            SELECT l.*, m.state AS state, m.reason AS reason
            FROM stump_lines l LEFT JOIN messages m ON m.id = l.msg_id
            WHERE l.node=? AND l.room=? ORDER BY l.id DESC LIMIT ?""", (node, room, limit))
        return rows[::-1]

    # ---------------------------------------------------------------- stump DMs
    def add_dm(self, node, nick, outgoing, body, state, msg_id=None, unread=False, ts=None, audio=None):
        audio_mode, audio_bytes = audio if audio else (None, None)
        cur = self._write(
            "INSERT INTO stump_dms(node, nick, outgoing, body, ts, state, msg_id, unread, audio_mode, audio, audio_ms)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (node, nick, 1 if outgoing else 0, body, ts or time.time(), state, msg_id, 1 if unread else 0,
             audio_mode, sqlite3.Binary(audio_bytes) if audio_bytes is not None else None,
             _voice_ms(audio_mode, audio_bytes)))
        return cur.lastrowid

    def dm_audio(self, dm_id):
        with self.lock:
            row = self.db.execute("SELECT audio_mode, audio FROM stump_dms WHERE id=?", (dm_id,)).fetchone()
        return (row[0], bytes(row[1])) if row and row[1] is not None else (None, None)

    def update_dm(self, dm_id, **fields):
        sets = [(k, v) for k, v in fields.items() if k in ("state", "reason", "msg_id")]
        if sets:
            self._write(f"UPDATE stump_dms SET {', '.join(k + '=?' for k, _ in sets)} WHERE id=?",
                        (*[v for _, v in sets], dm_id))

    _DM_COLS = ("id, node, nick, outgoing, body, ts, state, reason, msg_id, unread, audio_mode,"
                " length(audio) AS audio_len, audio_ms")

    def pending_dms(self, node):
        return self._read(f"SELECT {self._DM_COLS} FROM stump_dms WHERE node=? AND outgoing=1 AND state='pending'"
                          " ORDER BY id", (node,))

    def dm_thread(self, node, nick, limit=300):
        rows = self._read(f"SELECT {self._DM_COLS} FROM stump_dms WHERE node=? AND nick=? ORDER BY id DESC LIMIT ?",
                          (node, nick, limit))
        return rows[::-1]

    def dm_threads(self, node):
        return self._read("""
            SELECT d.nick, d.body, d.ts, d.outgoing, d.state,
                   (SELECT COUNT(*) FROM stump_dms u WHERE u.node=d.node AND u.nick=d.nick AND u.unread=1) AS unread
            FROM stump_dms d
            WHERE d.node=? AND d.id = (SELECT MAX(id) FROM stump_dms x WHERE x.node=d.node AND x.nick=d.nick)
            ORDER BY d.id DESC""", (node,))

    def mark_dms_read(self, node, nick):
        with self.lock:
            if self.db.execute("SELECT 1 FROM stump_dms WHERE node=? AND nick=? AND unread=1",
                               (node, nick)).fetchone():
                self._write("UPDATE stump_dms SET unread=0 WHERE node=? AND nick=?", (node, nick))


def _decode_node(row):
    for k in ("rooms", "users", "stumps"):
        try:
            row[k] = json.loads(row[k]) if row.get(k) else []
        except ValueError:
            row[k] = []
    return row
