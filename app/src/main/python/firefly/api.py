"""The engine's API for the Android app. JSON strings in and out.

Every function returns quickly: sending, connecting and radio work happen on
the engine's own threads, and results arrive as events on the listener
({"type": "changed" | "status" | "message" | "stump_dm"}) or in the Store,
which the app re-reads when told something changed.
"""
import json
import os
import threading
import time

from .core import CODEC2_FRAMES, Core, single_packet_size
from .audio import PLAYABLE, valid_voice
from . import stump

_core = None
_lock = threading.Lock()


def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


def _need():
    if _core is None or not _core.running:
        raise RuntimeError("engine not running")
    return _core


# ------------------------------------------------------------------ lifecycle
def start(home, links, http, listener):
    """Start once per process. Must run on the Python main thread (see core.start)."""
    global _core
    with _lock:
        if _core is not None and _core.running:
            return _core.address
        _core = Core(str(home), links=links, http=http, listener=listener)
        return _core.start()


def stop():
    global _core
    with _lock:
        if _core is not None:
            _core.stop()


def is_running():
    return _core is not None and _core.running


# ------------------------------------------------------------------ status
def status():
    c = _need()
    return _j({
        "address": c.address,
        "identity": c.identity.hash.hex(),
        "display_name": c.settings["display_name"],
        "stump_nick": stump.clean_nick(c.settings["display_name"]),
        "radio": c.radio.snapshot() if c.radio else {"state": "off"},
        "radio_wanted": {k: c.settings["radio"][k] for k in
                         ("frequency", "bandwidth", "spreading_factor", "coding_rate", "tx_power")},
        "keep_awake": bool(c.settings["radio"]["keep_awake"]),
        "voice_mode": c.settings["voice_mode"],
        "interfaces": c.interface_stats(),
        "propagation": _propagation_status(c),
        "last_announce": c.last_announce or None,
        "note": c.status_note,
        "uptime": time.time() - c.started_at,
    })


# ------------------------------------------------------------------ LXMF chat
def conversations():
    c = _need()
    rows = c.store.conversations()
    for r in rows:
        r["hops"] = c.hops(r["peer"])
    return _j(rows)


def peers():
    c = _need()
    rows = [p for p in c.store.peers("lxmf") if not p["stump"]]
    for r in rows:
        r["hops"] = c.hops(r["hash"])
    return _j(rows)


def peer(hash_hex):
    c = _need()
    p = c.store.peer(hash_hex) or {"hash": hash_hex, "name": None}
    p["hops"] = c.hops(hash_hex)
    p["is_stump"] = c.is_stump(hash_hex)
    return _j(p)


def messages(peer_hex):
    rows = _need().store.messages(peer_hex)
    for r in rows:
        if r.get("audio_mode") is not None:
            r["audio_ms"] = r.get("audio_ms") or _codec2_ms(r)
            r["audio_playable"] = r["audio_mode"] in PLAYABLE and bool(r.get("audio_len"))
    return _j(rows)


def _codec2_ms(r):
    """Notes stored before 0.2.7 have no stored length: Codec 2 can tell from the size."""
    from .core import audio_duration_ms
    return audio_duration_ms(r["audio_mode"], r.get("audio_len") or 0)


def voice_mode_for(peer_hex):
    """The audio mode the app should record for a note to this peer."""
    return _need().voice_mode_for(peer_hex)


def voice_mode_for_stump(key=None):
    return _need().stump_voice_mode(key)


def send_voice(peer_hex, mode, data):
    """A voice note: Codec 2 bytes, already encoded by the app (audio/Codec2.kt)."""
    data = bytes(data)
    mode = int(mode)
    if not valid_voice(mode, data):
        raise ValueError("not a voice note this app can send (0.6 to 15 s, Codec 2 or Opus)")
    return _need().send(peer_hex, "", audio=(mode, data)) or -1


def message_audio(msg_id):
    """The encoded voice note of a message, to decode and play."""
    mode, data = _need().store.message_audio(int(msg_id))
    return data if data is not None else b""


def mark_read(peer_hex):
    _need().store.mark_read(peer_hex)


def send(peer_hex, text):
    return _need().send(peer_hex, text) or -1


def packet_size(text):
    return single_packet_size(text or "")


def add_contact(hex_addr):
    return _need().add_contact(hex_addr)


def announce():
    _need().announce()


def sync():
    return _need().sync()


# ------------------------------------------------------------------ settings
def settings_get():
    return _j(_need().settings.data)


def settings_set(patch_json):
    """Apply a partial settings update and do whatever it takes to make it live."""
    c = _need()
    changed = c.settings.update(json.loads(patch_json))
    if "radio" in changed and c.radio:
        c.radio.request_apply()
    if "display_name" in changed:
        c.announce()
    if changed & {"auto_interface", "tcp_peers"}:
        threading.Thread(target=c.reattach_optional_interfaces, daemon=True).start()
    if changed & {"propagation_mode", "propagation_node"}:
        c._apply_propagation_setting()
    if "rnode_hosts" in changed and c.radio:
        c.radio.search_now()
    c.emit({"type": "status"})
    return _j(sorted(changed))


def _propagation_status(c):
    sel = next((n for n in c.propagation_nodes() if n["selected"]), {})
    return {"node": c.propagation_node(), "state": c.sync_state, "last_sync": c.last_sync or None,
            "rounds": c.sync_rounds, "last_result": c.router.propagation_transfer_last_result,
            "name": sel.get("name"), "stump": sel.get("stump"), "enabled": sel.get("enabled"),
            "transfer_limit_kb": sel.get("transfer_limit_kb"), "stamp_cost": sel.get("stamp_cost"),
            "mode": c.settings["propagation_mode"], "fast_stamps": c.fast_stamps}


def sync_soon():
    """The app came to the foreground: collect anything left for us (rate-limited)."""
    _need().sync_soon()


def propagation_nodes():
    return _j(_need().propagation_nodes())


def use_propagation_node(hex_or_auto):
    """'auto' (a Stump's node first, then the nearest), 'off', or a node's address."""
    c = _need()
    if hex_or_auto in ("auto", "off"):
        c.settings.update({"propagation_mode": hex_or_auto, "propagation_node": None})
        if hex_or_auto == "auto":
            c._auto_pick_propagation()
    else:
        c.add_propagation_node(hex_or_auto)
    c.emit({"type": "status"})


def stump_defaults():
    from .settings import STUMP_RADIO
    return _j(STUMP_RADIO)


# ------------------------------------------------------------------ radio
def radio_search_now():
    c = _need()
    if c.radio:
        c.radio.search_now()


def add_ble_device(address, name):
    c = _need()
    devs = [d for d in c.settings["radio"]["ble_devices"] if d["address"].upper() != address.upper()]
    devs.append({"address": address.upper(), "name": name or "RNode"})
    c.settings.update({"radio": {"ble_devices": devs, "enabled": True}})
    c.radio.search_now()


def remove_ble_device(address):
    c = _need()
    devs = [d for d in c.settings["radio"]["ble_devices"] if d["address"].upper() != address.upper()]
    patch = {"ble_devices": devs}
    if c.settings["radio"]["port"].upper() == ("BLE:" + address).upper():
        patch["port"] = "auto"
    c.settings.update({"radio": patch})
    c.radio.request_apply(0.2)


# ------------------------------------------------------------------ Stump
def stump_nodes():
    c = _need()
    rows = c.store.stump_nodes()
    for r in rows:
        r["open"] = r["key"] in c.sessions
        if r["transport"] == "mesh":
            r["hops"] = c.hops(r["address"])
    return _j(rows)


def delete_conversation(peer_hex):
    _need().delete_conversation(peer_hex)


def block(peer_hex):
    _need().block(peer_hex)


def unblock(peer_hex):
    _need().unblock(peer_hex)


def blocked():
    return _j(_need().store.blocked())


def stump_hide(key):
    _need().hide_stump(key)


def stump_remove(key):
    _need().remove_stump(key)


def stump_hidden_count():
    return _need().store.hidden_stump_count()


def stump_restore_hidden():
    _need().store.restore_hidden_stumps()


def stump_connect_wifi(base):
    return _need().open_stump_wifi(base).key


def stump_close(key, forget=False):
    _need().close_stump(key, forget=bool(forget))


def stump_view(key, room=None):
    c = _need()
    sess = c.session(key)
    node = c.store.stump_node(key) or {}
    room = room or sess.room
    node["room"] = sess.room
    node["nick"] = sess.my_nick()
    node["lines"] = c.store.lines(key, room)
    node["viewing"] = room
    node["threads"] = c.store.dm_threads(key)
    node["propagation"] = _stump_pn(c, node) if key.startswith("mesh:") else None
    return _j(node)


def _stump_pn(c, node):
    """This Stump's own propagation node, if it runs one and we've heard it."""
    import RNS
    try:
        ident = RNS.Identity.recall(bytes.fromhex(node["address"]))
        pn = RNS.Destination.hash(ident, "lxmf", "propagation").hex() if ident else None
    except Exception:
        return None
    n = next((x for x in c.propagation_nodes() if x["hash"] == pn), None) if pn else None
    if not n:
        return None
    return {"hash": pn, "enabled": n.get("enabled"), "selected": n.get("selected")}


def stump_post(key, text):
    _need().session(key).post(text)


def stump_join(key, room):
    _need().session(key).join(room)


def stump_command(key, line):
    _need().session(key).command(line)


def stump_auth(key):
    return _need().session(key).start_auth()


def stump_dm_thread(key, nick):
    c = _need()
    c.store.mark_dms_read(key, nick)
    rows = c.store.dm_thread(key, nick)
    for r in rows:
        if r.get("audio_mode") is not None:
            r["audio_ms"] = r.get("audio_ms") or _codec2_ms(r)
            r["audio_playable"] = r["audio_mode"] in PLAYABLE and bool(r.get("audio_len"))
    return _j(rows)


def stump_send_voice(key, nick, mode, data):
    return _need().session(key).send_voice_dm(nick, mode, bytes(data))


def stump_dm_audio(dm_id):
    mode, data = _need().store.dm_audio(int(dm_id))
    return data if data is not None else b""


def stump_send_dm(key, nick, text):
    return _need().session(key).send_dm(nick, text) or -1


# ------------------------------------------------------------------ Stump board & files (Wi-Fi)
def _wifi_base(key):
    if not str(key).startswith("wifi:"):
        raise ValueError("the billboard and files are Wi-Fi only")
    return str(key)[5:]


def stump_board(key):
    from . import stump_web
    return _j(stump_web.board(_need().http, _wifi_base(key)))


def stump_board_post(key, title, body=""):
    from . import stump_web
    return _j(stump_web.board_post(_need().http, _wifi_base(key), title, body or ""))


def stump_files(key):
    from . import stump_web
    return _j(stump_web.files(_need().http, _wifi_base(key)))


def stump_download_url(key, name):
    from . import stump_web
    return stump_web.download_url(_wifi_base(key), name)


def stump_upload_url(key):
    return _wifi_base(key) + "/upload"


def stump_upload_cost(name, credits=True):
    from . import stump_web
    return stump_web.upload_cost(name, bool(credits))


# ------------------------------------------------------------------ identity
def identity_export():
    """The 64-byte private key, for the app to write where the user chooses."""
    return bytes(_need().export_identity())


def identity_import(key_bytes):
    """Store a key for the next start. Returns its identity hash."""
    return _need().stage_identity_import(bytes(key_bytes))
