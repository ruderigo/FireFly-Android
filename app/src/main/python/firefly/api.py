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

from .core import Core, single_packet_size
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
        "interfaces": c.interface_stats(),
        "propagation": {"node": c.propagation_node(), "state": c.sync_state,
                        "last_sync": c.last_sync or None},
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
    return _j(_need().store.messages(peer_hex))


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
    return _j(node)


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
    return _j(c.store.dm_thread(key, nick))


def stump_send_dm(key, nick, text):
    return _need().session(key).send_dm(nick, text) or -1


# ------------------------------------------------------------------ identity
def identity_export():
    """The 64-byte private key, for the app to write where the user chooses."""
    return bytes(_need().export_identity())


def identity_import(key_bytes):
    """Store a key for the next start. Returns its identity hash."""
    return _need().stage_identity_import(bytes(key_bytes))
