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
def _wifi_stumps(c):
    """The Wi-Fi Stumps open now, and how many answer: for the Wi-Fi light."""
    keys = [k for k in list(c.sessions) if k.startswith("wifi:")]
    online = sum(1 for k in keys if (c.store.stump_node(k) or {}).get("online"))
    return {"open": len(keys), "online": online}


def status():
    c = _need()
    return _j({
        "address": c.address,
        "identity": c.identity.hash.hex(),
        "display_name": c.settings["display_name"],
        "stump_nick": stump.clean_nick(c.settings["display_name"]),
        "radio": c.radio.snapshot() if c.radio else {"state": "off"},
        "wifi_stumps": _wifi_stumps(c),
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
        nick = stump.clean_nick(c.settings["display_name"])
        for sess in list(c.sessions.values()):          # Wi-Fi Stumps hear no announce
            try:
                sess.rename(nick)
            except Exception:
                pass
    if changed & {"auto_interface", "tcp_peers"}:
        threading.Thread(target=c.reattach_optional_interfaces, daemon=True).start()
    if changed & {"propagation_mode", "propagation_node"}:
        c._apply_propagation_setting()
    if "stump_prefer" in changed:                    # the route toggle: /hello again, with it
        for sess in list(c.sessions.values()):
            if getattr(sess, "hello_v2", False):
                sess.greet(force=True)
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
        c.emit({"type": "status"})              # "busy" right away: the button waits, the light flashes


BLE_PORT_PREFIX = "ble:"


def add_ble_device(address, name):
    c = _need()
    devs = [d for d in c.settings["radio"]["ble_devices"] if d["address"].upper() != address.upper()]
    devs.append({"address": address.upper(), "name": name or "RNode"})
    c.settings.update({"radio": {"ble_devices": devs, "enabled": True}})
    c.radio.search_now()


def use_ble_device(address):
    """"Use" on a paired RNode: switch to it now, and prefer it from then on."""
    c = _need()
    if c.radio:
        c.radio.use(BLE_PORT_PREFIX + str(address).upper())
        c.emit({"type": "status"})


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
    """One entry per Stump: a node reached over both LoRa and Wi-Fi (the same LXMF address,
    which a Wi-Fi node gives in its poll) is one row. The row is the transport to open first
    (Wi-Fi while it answers, else LoRa); the other ones are in "others"."""
    c = _need()
    rows = c.store.stump_nodes()
    for r in rows:
        r["open"] = r["key"] in c.sessions
        if r["transport"] == "mesh":
            r["hops"] = c.hops(r["address"])
            r["lxmf"] = r["address"]
    groups, order = {}, []
    for r in rows:
        g = r.get("lxmf") or r["key"]
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(r)
    out = []
    for g in order:
        members = groups[g]
        # Open first what can reach the Stump now: Wi-Fi while it answers, else LoRa if the
        # radio (or another path) can carry it, else Wi-Fi anyway (it may answer again).
        reach = {r["key"]: c.can_reach(r["address"]) for r in members if r["transport"] == "mesh"}
        members.sort(key=lambda r: (0 if r["transport"] == "wifi" and r.get("online") else
                                    1 if r["transport"] == "mesh" and reach.get(r["key"]) else
                                    2 if r["transport"] == "wifi" else 3))
        first = dict(members[0])
        first["others"] = members[1:]
        first["transports"] = sorted({m["transport"] for m in members})
        first["unread_dms"] = sum(m.get("unread_dms") or 0 for m in members)
        first["name"] = next((m["name"] for m in members if m.get("name") and not str(m["name"]).startswith("Stump Wi-Fi (")),
                             first.get("name"))
        first["group"] = g                  # stays the same whichever transport opens first
        out.append(first)
    # A steady order: by name, not by who was heard last (Stumps close to each other beacon and
    # answer all the time, and a list sorted by "last heard" kept jumping).
    out.sort(key=lambda r: (str(r.get("name") or r["address"]).casefold(), r["group"]))
    return _j(out)


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


def stump_connect_checked(base):
    """Open a Stump typed by hand, only once /node.json (or an older node's JSON) says
    one is there: a Wi-Fi's gateway is a Stump only on a Stump's own hotspot.
    -> {"key", "base", "name"} or {"error", "base"}"""
    from . import discovery
    from .stump_session import normalize_base
    c = _need()
    b = normalize_base(base)
    info = discovery.find(c.http, b)
    if not info:
        return _j({"base": b, "error": discovery.why_not(c.http, b)})
    sess = c.open_stump_wifi(info["base"])
    if info.get("lxmf"):
        c.retire_old_wifi(info["lxmf"], keep=sess.key)      # the same Stump at an older address
    return _j({"key": sess.key, "base": info["base"], "name": info.get("name")})


def stump_close(key, forget=False):
    _need().close_stump(key, forget=bool(forget))


def stump_view(key, room=None):
    c = _need()
    asked = key
    key = c.resolve(key)
    sess = c.session(key)
    node = c.store.stump_node(key) or {}
    room = room or sess.room
    node["room"] = sess.room
    node["nick"] = sess.my_nick()
    node["lines"] = c.store.lines(key, room)
    node["viewing"] = room
    node["threads"] = c.store.dm_threads(c.same_stump(key))     # over LoRa and Wi-Fi alike
    node["blocked"] = c.store.stump_blocked(c.stump_id(key))
    node["propagation"] = _stump_pn(c, node) if key.startswith("mesh:") else None
    node["hw"] = getattr(sess, "hw", None)        # the node's radio, for a status light (Wi-Fi poll)
    node["offline_reason"] = getattr(sess, "offline_reason", None)     # why the Wi-Fi poll gets nothing
    node["key"] = key
    node["moved_to"] = key if key != asked else None      # the screen follows the Stump to its new address
    sess.auth_timed_out()
    node["auth"] = (c.store.stump_node(key) or {}).get("auth", node.get("auth"))
    node["auth_detail"] = (c.store.stump_node(key) or {}).get("auth_detail", node.get("auth_detail"))
    if key.startswith("mesh:"):
        # Over LoRa with nothing to carry it: offer this Stump's Wi-Fi entry instead.
        node["reachable"] = c.can_reach(node["address"])
        node["wifi_alt"] = next((n["key"] for n in c.store.stump_nodes()
                                 if n["transport"] == "wifi" and n.get("lxmf") == node["address"]), None)
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
    key = c.resolve(key)
    keys = c.same_stump(key)            # the messages that came over LoRa and over Wi-Fi, together
    c.store.mark_dms_read(keys, nick)
    rows = c.store.dm_thread(keys, nick)
    for r in rows:
        if r.get("audio_mode") is not None:
            r["audio_ms"] = r.get("audio_ms") or _codec2_ms(r)
            r["audio_playable"] = r["audio_mode"] in PLAYABLE and bool(r.get("audio_len"))
    return _j(rows)


def stump_dm_delete(key, nick):
    """Delete the private conversation with `nick` on this Stump (over LoRa and Wi-Fi)."""
    c = _need()
    c.store.delete_dm_thread(c.same_stump(c.resolve(key)), str(nick).lstrip("~"))


def stump_block_nick(key, nick):
    """Block `nick` on this Stump: the conversation is deleted, and their private messages
    are dropped from now on (no notification). The Stump isn't told: it has no such command."""
    c = _need()
    key = c.resolve(key)
    nick = str(nick).lstrip("~")
    c.store.block_stump_nick(c.stump_id(key), nick)
    c.store.delete_dm_thread(c.same_stump(key), nick)


def stump_unblock_nick(key, nick):
    c = _need()
    c.store.unblock_stump_nick(c.stump_id(c.resolve(key)), str(nick).lstrip("~"))


def stump_send_voice(key, nick, mode, data):
    c = _need()
    return c.session(_reply_key(c, key)).send_voice_dm(nick, mode, bytes(data))


def stump_dm_audio(dm_id):
    mode, data = _need().store.dm_audio(int(dm_id))
    return data if data is not None else b""


def _reply_key(c, key):
    """Where a private message to this Stump can go now: the LoRa side when something can
    carry it, else a Wi-Fi side that answers (a DM opened from its notification may be on
    LoRa with no radio attached)."""
    key = c.resolve(key)
    if key.startswith("mesh:") and not c.can_reach(key[5:]):
        for k in c.same_stump(key)[1:]:
            if k.startswith("wifi:") and (c.store.stump_node(k) or {}).get("online"):
                return k
    return key


def stump_send_dm(key, nick, text):
    c = _need()
    return c.session(_reply_key(c, key)).send_dm(nick, text) or -1


# ------------------------------------------------------------------ Stump board & files (Wi-Fi)
def _wifi_base(key):
    if not str(key).startswith("wifi:"):
        raise ValueError("the billboard and files are Wi-Fi only")
    return _need().resolve(str(key))[5:]          # where the Stump is now, if it moved


def stump_board(key, q=""):
    from . import stump_web
    return _j(stump_web.board(_need().http, _wifi_base(key), q=str(q or "")))


def stump_board_post(key, title, body=""):
    from . import stump_web
    return _j(stump_web.board_post(_need().http, _wifi_base(key), title, body or ""))


def stump_files(key, q="", cat=""):
    from . import stump_web
    return _j(stump_web.files(_need().http, _wifi_base(key), q=str(q or ""), cat=str(cat or "")))


def stump_plugins(key):
    from . import stump_web
    return _j(stump_web.plugins(_need().http, _wifi_base(key)))


def stump_tools(key, my_version):
    from . import stump_web
    return _j(stump_web.tools(_need().http, _wifi_base(key), str(my_version)))


def stump_chain(key):
    from . import stump_web
    return _j(stump_web.chain(_need().http, _wifi_base(key)))


def stump_radio(key):
    """The node's radio settings beside ours: {"node": {...} | None, "differs": [...]}."""
    from . import stump_web
    c = _need()
    base = _wifi_base(key)
    node = stump_web.node_radio(c.http, base)
    return _j({"node": node, "differs": stump_web.radio_differences(node, c.settings["radio"]) if node else [],
               "mode": stump_web.node_mode(c.http, base)})


def stump_use_radio(key):
    """Set our radio to the node's (frequency, bandwidth, SF, CR) and retune now."""
    from . import stump_web
    c = _need()
    node = stump_web.node_radio(c.http, _wifi_base(key))
    if not node:
        return False
    c.settings.update({"radio": stump_web.radio_patch(node)})
    if c.radio:
        c.radio.request_apply(0.2)
    return True


def stump_download_url(key, name):
    from . import stump_web
    return stump_web.download_url(_wifi_base(key), name)


def stump_upload_url(key):
    return _wifi_base(key) + "/upload"


def stump_upload_cost(name, credits=True):
    from . import stump_web
    return stump_web.upload_cost(name, bool(credits))


# ------------------------------------------------------------------ finding Stumps
def discover_probe(base):
    """A Stump at this address (the Wi-Fi gateway, or a hotspot just joined)? Plus how steady."""
    from . import discovery
    from .stump_session import normalize_base
    return _j(discovery.probe(_need().http, normalize_base(base)))


def discover_lan(own_ip, prefix, skip_json="[]"):
    """Every Stump on the phone's local network: the ones that answer STUMP? (Stump
    1.0.0-beta.21+), those they heard, then the /24 sweep for older ones. A few seconds.
    Stumps we already know are followed to the address they answered from."""
    from . import discovery
    c = _need()
    found = discovery.find_all(c.http, str(own_ip) if own_ip else None, int(prefix or 24))
    skip = set(json.loads(skip_json or "[]"))
    found = [f for f in found if f["base"].split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0] not in skip]
    try:
        c.refresh_wifi([{"lxmf": f.get("lxmf"), "base": f["base"], "name": f.get("name")} for f in found if f.get("lxmf")])
    except Exception:
        pass
    return _j(found)


def stump_refresh_wifi():
    """Known Stumps answering on this Wi-Fi: opened there, or followed to a new address.
    Cheap (one broadcast, ~1.5 s); the Stumps tab calls it when shown."""
    c = _need()
    now = time.time()
    if now - getattr(c, "_refreshed_at", 0) < 20:
        return _j([])
    c._refreshed_at = now
    return _j(c.refresh_wifi())


def stump_find_wifi(lxmf):
    """The Wi-Fi entry of this Stump (by identity), found now. -> {"key"} or {"error"}"""
    key = _need().find_wifi(str(lxmf or "").lower())
    return _j({"key": key} if key else {"error": "no answer from this Stump on this Wi-Fi"})


def discover_ssids(scan_json):
    """Mark a Wi-Fi scan: [{"ssid", "bssid", "rssi", "freq", "secured"}] -> the same with
    "stump" (yes | likely | maybe) and "bars", strongest first, one row per name."""
    from . import discovery
    c = _need()
    known = c.settings.get("stump_ssids") or []
    best = {}
    for ap in json.loads(scan_json or "[]"):
        ssid = (ap.get("ssid") or "").strip()
        kind = discovery.looks_like_stump(ssid, known)
        if kind == "no":
            continue
        if ssid not in best or (ap.get("rssi") or -999) > (best[ssid].get("rssi") or -999):
            best[ssid] = dict(ap, ssid=ssid, stump=kind, bars=discovery.signal_bars(ap.get("rssi")))
    order = {"yes": 0, "likely": 1, "maybe": 2}
    return _j(sorted(best.values(), key=lambda a: (order[a["stump"]], -(a.get("rssi") or -999))))


def remember_stump_ssid(ssid):
    c = _need()
    ssid = (ssid or "").strip()
    known = list(c.settings.get("stump_ssids") or [])
    if ssid and ssid not in known:
        c.settings.update({"stump_ssids": (known + [ssid])[-50:]})


def stump_lora_link(key):
    """For a Stump heard on LoRa: hops, and RSSI/SNR of the last packet from it, with bars."""
    import RNS
    from . import discovery
    c = _need()
    if not str(key).startswith("mesh:"):
        return _j({})
    addr = str(key)[5:]
    try:
        hops = RNS.Transport.hops_to(bytes.fromhex(addr)) if RNS.Transport.has_path(bytes.fromhex(addr)) else None
    except Exception:
        hops = None
    sig = c.store.last_signal(addr)
    snr = sig.get("snr") if sig else None
    return _j({"hops": hops, "rssi": sig.get("rssi") if sig else None, "snr": snr,
               "heard": sig.get("ts") if sig else None, "bars": discovery.lora_bars(snr)})


# ------------------------------------------------------------------ identity
def identity_export():
    """The 64-byte private key, for the app to write where the user chooses."""
    return bytes(_need().export_identity())


def identity_import(key_bytes):
    """Store a key for the next start. Returns its identity hash."""
    return _need().stage_identity_import(bytes(key_bytes))
