"""The messaging core: Reticulum + LXMF + Stump sessions, independent of any UI.

Ported from FireFly on the handheld. What differs on Android:

- Start-up can't be taken down by an optional interface. Reticulum's config
  has no interfaces at all; AutoInterface, TCP peers and the radio are
  attached afterwards, each inside its own try/except. (Reticulum exits the
  process if an interface in its config file fails to construct.)
- The radio is a USB or Bluetooth LE RNode, found by the RadioManager.
- Events go to a listener (Kotlin) instead of a pygame loop.

Threads: RNS and LXMF call back from their own threads. Shared state goes
through the Store (locked) or small locked dicts here.
"""
import json
import os
import socket
import threading
import time

import RNS
import RNS.vendor.umsgpack as msgpack
import LXMF
from LXMF.LXMessage import LXMessage
from LXMF.LXMRouter import LXMRouter

from . import stump
from .radio import RadioManager
from .settings import Paths, Settings
from .store import Store
from .stump_session import MeshPipe, StumpSession, UrllibHttp, WifiPipe, mesh_key, normalize_base, wifi_key

PATH_WAIT_S = 20
INTRODUCE_AFTER_S = 30      # no extra announce if we announced this recently
MONITOR_INTERVAL_S = 1.0
IDENTITY_SIZE = 64

FIELD_NAMES = {
    LXMF.FIELD_IMAGE: "image",
    LXMF.FIELD_AUDIO: "voice message",
    LXMF.FIELD_FILE_ATTACHMENTS: "file",
    LXMF.FIELD_TELEMETRY: "location/telemetry",
    LXMF.FIELD_EMBEDDED_LXMS: "embedded message",
}


def single_packet_size(text, title=""):
    """LXMF content size the way LXMessage.pack() measures it (limit 295 for one packet)."""
    payload = msgpack.packb([time.time(), title.encode(), text.encode(), {}])
    return len(payload) - LXMessage.TIMESTAMP_SIZE - LXMessage.STRUCT_OVERHEAD


class NullLinks:
    """No radio hardware (desktop runs without a test double)."""
    def usbPortsJson(self): return "[]"
    def open(self, port, timeout_ms): return None


class Core:
    def __init__(self, home, links=None, http=None, listener=None, log=print):
        self.paths = Paths(home)
        self.paths.ensure()
        self.links = links or NullLinks()
        self.http = http or UrllibHttp()
        self.listener = listener
        self.log = log
        self.settings = Settings(self.paths.settings)
        self.store = Store(self.paths.database)
        self.lock = threading.RLock()
        self.outbound = {}          # message id -> LXMessage in flight
        self.stumps = {}            # lxmf hash hex -> (name, version)
        self.sessions = {}          # session key -> StumpSession
        self.extra_ifaces = []      # AutoInterface / TCP interfaces we attached
        self._introduced = set()    # peers we've sent to this session (announced to first)
        self.iface_errors = {}
        self.running = False
        self.radio = None
        self.started_at = time.time()
        self.last_announce = 0.0
        self.last_sync = 0.0
        self.sync_state = "idle"
        self.status_note = ""

    # ================================================================ lifecycle
    def start(self):
        s = self.settings
        _write_rns_config(s, self.paths.rns_config_dir)
        if threading.current_thread() is not threading.main_thread():
            # Reticulum and LXMF install SIGINT/SIGTERM handlers, which Python
            # only allows on its main thread. The Android app starts Python on
            # its dedicated engine thread for exactly this reason.
            self.log("warning: engine not started on the Python main thread")
        self.reticulum = RNS.Reticulum(configdir=self.paths.rns_config_dir, loglevel=int(s["log_level"]))
        self.identity = self._load_identity()
        self.router = LXMRouter(identity=self.identity, storagepath=self.paths.lxmf_storage)
        self.local = self.router.register_delivery_identity(self.identity, display_name=s["display_name"])
        self.router.register_delivery_callback(self._on_message)

        RNS.Transport.register_announce_handler(_DeliveryAnnounces(self))
        RNS.Transport.register_announce_handler(_PropagationAnnounces(self))
        RNS.Transport.register_announce_handler(stump.BeaconHandler(self._on_stump_beacon))

        for p in self.store.peers("lxmf"):
            if p["stump"]:
                self.stumps[p["hash"]] = (p["stump"], p["stump_ver"])
        self._apply_propagation_setting()
        for m in self.store.pending_outgoing():
            self.store.update_message(m["id"], state="failed", reason="app closed before delivery")
        for dm_node in self.store.stump_nodes():
            for dm in self.store.pending_dms(dm_node["key"]):
                self.store.update_dm(dm["id"], state="failed", reason="app closed before confirmation")

        self.running = True
        self._attach_optional_interfaces()
        for node in self.store.stump_nodes():
            if node["transport"] == "wifi":
                self.open_stump_wifi(node["address"])
        threading.Thread(target=self._housekeeping, daemon=True, name="firefly-housekeeping").start()
        threading.Thread(target=self._watch_store, daemon=True, name="firefly-watch").start()
        self.radio = RadioManager(self, self.links, on_change=lambda: self.emit({"type": "status"}))
        self.radio.start()
        self.log(f"FireFly address {self.address}  identity {self.identity.hash.hex()}")
        return self.address

    def stop(self):
        self.running = False
        for sess in list(self.sessions.values()):
            sess.stop()
        if self.radio:
            self.radio.stop()
        for fn in (self.router.exit_handler, RNS.Transport.exit_handler):
            try:
                fn()
            except Exception:
                pass

    def emit(self, event):
        if self.listener is None:
            return
        try:
            self.listener.onEvent(json.dumps(event, ensure_ascii=False))
        except Exception as e:
            RNS.log(f"listener error: {e}", RNS.LOG_WARNING)

    def _watch_store(self):
        seen = -1
        while self.running:
            v = self.store.version
            if v != seen:
                seen = v
                self.emit({"type": "changed", "version": v})
            time.sleep(0.25)

    # ================================================================ interfaces
    def _attach_optional_interfaces(self):
        from RNS.Interfaces import TCPInterface
        wanted = []
        if self.settings["auto_interface"]:
            wanted.append(("Local network", _auto_interface_class(), {"name": "Local network"}))
        for i, peer in enumerate(self.settings["tcp_peers"]):
            host, _, port = str(peer).strip().rpartition(":")
            if host and port.isdigit():
                name = f"TCP {host}:{port}"
                wanted.append((name, TCPInterface.TCPClientInterface,
                               {"name": name, "target_host": host, "target_port": int(port)}))
        for name, cls, cfg in wanted:
            iface = None
            try:
                iface = cls(RNS.Transport, cfg)
                self.reticulum._add_interface(iface)
                self.extra_ifaces.append(iface)
                self.iface_errors.pop(name, None)
            except Exception as e:
                # It can fail after Reticulum registered it (in final_init):
                # take it back out, or it lingers as a dead duplicate entry.
                if iface is not None:
                    try:
                        RNS.Transport.remove_interface(iface)
                    except Exception:
                        pass
                    try:
                        iface.detach()
                    except Exception:
                        pass
                self.iface_errors[name] = str(e)
                RNS.log(f"Interface {name} not started: {e}", RNS.LOG_WARNING)

    def reattach_optional_interfaces(self):
        for iface in self.extra_ifaces:
            try:
                iface.detach()
            except Exception:
                pass
            try:
                RNS.Transport.remove_interface(iface)
            except Exception:
                pass
        self.extra_ifaces = []
        self._attach_optional_interfaces()

    def on_radio_online(self):
        # Let the LoRa side hear us straight away: peers and Stump nodes learn our path.
        threading.Thread(target=lambda: (time.sleep(2), self.announce()), daemon=True).start()

    def interface_stats(self):
        out = []
        for i in list(RNS.Transport.interfaces):
            out.append({"name": str(getattr(i, "name", i)), "type": type(i).__name__,
                        "online": bool(getattr(i, "online", False)),
                        "rx": getattr(i, "rxb", 0), "tx": getattr(i, "txb", 0),
                        "bitrate": getattr(i, "bitrate", None)})
        for name, err in self.iface_errors.items():
            out.append({"name": name, "type": "error", "online": False, "error": err, "rx": 0, "tx": 0})
        return out

    # ================================================================ identity
    def _load_identity(self):
        """Load the identity key, never silently replacing a damaged one."""
        path, backup = self.paths.identity, self.paths.identity + ".bak"
        for candidate in (path, backup):
            if _plausible_key(candidate):
                ident = RNS.Identity.from_file(candidate)
                if ident:
                    if candidate == backup:
                        self.status_note = "Identity key was damaged and restored from backup"
                    self._write_identity(ident)
                    return ident
        if os.path.exists(path):
            damaged = f"{path}.damaged-{int(time.time())}"
            os.replace(path, damaged)
            self.status_note = "Identity key was unreadable: a NEW address was created"
        ident = RNS.Identity()
        self._write_identity(ident)
        return ident

    def _write_identity(self, ident):
        _write_key_files(self.paths.identity, ident.get_private_key())

    def export_identity(self):
        return self.identity.get_private_key()

    def stage_identity_import(self, key):
        """Validate and store a key to be used from the next start (Reticulum can't swap identities live)."""
        key = bytes(key)
        if len(key) != IDENTITY_SIZE or len(set(key)) <= 8:
            raise ValueError("not a Reticulum identity file (expected 64 bytes)")
        ident = RNS.Identity.from_bytes(key)
        if ident is None:
            raise ValueError("not a valid Reticulum identity")
        _write_key_files(self.paths.identity, key)
        return ident.hash.hex()

    @property
    def address(self):
        return self.local.hash.hex()

    # ================================================================ announces
    def announce(self):
        self.local.display_name = self.settings["display_name"]
        self.router.announce(self.local.hash)
        self.last_announce = time.time()

    def _on_delivery_announce(self, dest_hash, identity, app_data):
        if dest_hash == self.local.hash:
            return
        name = LXMF.display_name_from_app_data(app_data) if app_data else None
        hops = RNS.Transport.hops_to(dest_hash)
        self.store.upsert_peer(dest_hash.hex(), "lxmf", name=name, hops=hops,
                               stamp_cost=LXMF.stamp_cost_from_app_data(app_data) if app_data else None)
        h = dest_hash.hex()
        if identity and h not in self.stumps and h not in self._beacon_asked:
            # Asking is faster than waiting up to 30 minutes for the next beacon.
            self._beacon_asked.add(h)
            RNS.Transport.request_path(stump.beacon_hash_for(identity))
        if h in self.stumps:
            self.store.upsert_stump_node(mesh_key(h), "mesh", h, last_seen=time.time())

    _beacon_asked = set()

    def _on_stump_beacon(self, lxmf_hex, name, version):
        self.stumps[lxmf_hex] = (name, version)
        self.store.upsert_peer(lxmf_hex, "lxmf", heard=False, stump=name, stump_ver=version)
        self.store.upsert_stump_node(mesh_key(lxmf_hex), "mesh", lxmf_hex, name=name, last_seen=time.time())

    def is_stump(self, peer_hex):
        return peer_hex in self.stumps

    def _on_propagation_announce(self, dest_hash, app_data):
        if not LXMF.pn_announce_data_is_valid(app_data):
            return
        try:
            enabled = bool(msgpack.unpackb(app_data)[2])
        except Exception:
            enabled = False
        name = None
        try:
            name = LXMF.pn_name_from_app_data(app_data)
        except Exception:
            pass
        self.store.upsert_peer(dest_hash.hex(), "propagation", name=name or "Propagation node",
                               hops=RNS.Transport.hops_to(dest_hash), extra="on" if enabled else "off")
        if self.settings["propagation_mode"] == "auto" and enabled:
            self._auto_pick_propagation()

    # ================================================================ propagation
    def _apply_propagation_setting(self):
        mode = self.settings["propagation_mode"]
        if mode == "manual" and self.settings["propagation_node"]:
            try:
                self.router.set_outbound_propagation_node(bytes.fromhex(self.settings["propagation_node"]))
            except ValueError:
                pass
        elif mode == "auto":
            self._auto_pick_propagation()

    def _auto_pick_propagation(self):
        best = None
        for p in self.store.peers("propagation"):
            if p["extra"] != "on" or not p["last_heard"] or time.time() - p["last_heard"] > 86400:
                continue
            if best is None or (p["hops"] or 99) < (best["hops"] or 99):
                best = p
        if best:
            current = self.router.get_outbound_propagation_node()
            if current is None or current.hex() != best["hash"]:
                self.router.set_outbound_propagation_node(bytes.fromhex(best["hash"]))

    def propagation_node(self):
        if self.settings["propagation_mode"] == "off":
            return None
        node = self.router.get_outbound_propagation_node()
        return node.hex() if node else None

    def sync(self):
        if not self.propagation_node():
            self.sync_state = "no propagation node"
            return False
        self.last_sync = time.time()
        self.router.request_messages_from_propagation_node(self.identity)
        return True

    def sync_status(self):
        st = self.router.propagation_transfer_state
        names = {
            LXMRouter.PR_IDLE: "idle", LXMRouter.PR_PATH_REQUESTED: "finding node",
            LXMRouter.PR_LINK_ESTABLISHING: "connecting", LXMRouter.PR_LINK_ESTABLISHED: "connected",
            LXMRouter.PR_REQUEST_SENT: "requesting", LXMRouter.PR_RECEIVING: "receiving",
            LXMRouter.PR_RESPONSE_RECEIVED: "received", LXMRouter.PR_COMPLETE: "done",
            LXMRouter.PR_NO_PATH: "no path to node", LXMRouter.PR_LINK_FAILED: "link failed",
            LXMRouter.PR_TRANSFER_FAILED: "transfer failed", LXMRouter.PR_NO_IDENTITY_RCVD: "node needs identity",
            LXMRouter.PR_NO_ACCESS: "no access",
        }
        return names.get(st, "failed")

    # ================================================================ sending
    def send(self, peer_hex, text, method="auto", title="", allow_propagation=True):
        """Queue a message. Returns the message id; state updates arrive in the Store."""
        text = text.rstrip()
        if not text:
            return None
        msg_id = self.store.add_message(peer_hex, True, text, "pending", title=title)
        threading.Thread(target=self._send_worker,
                         args=(msg_id, peer_hex, text, method, title, allow_propagation),
                         daemon=True).start()
        return msg_id

    def _recall(self, dest_hash, wait=PATH_WAIT_S):
        ident = RNS.Identity.recall(dest_hash)
        if ident is None:
            RNS.Transport.request_path(dest_hash)
            deadline = time.time() + wait
            while ident is None and time.time() < deadline and self.running:
                time.sleep(0.5)
                ident = RNS.Identity.recall(dest_hash)
        return ident

    def _send_worker(self, msg_id, peer_hex, text, method, title, allow_propagation):
        if peer_hex not in self._introduced:
            # The recipient checks our signature with our public key, which it
            # only has once it has heard an announce. Announce just before the
            # first message of the session, so it isn't flagged "unverified".
            self._introduced.add(peer_hex)
            if time.time() - self.last_announce > INTRODUCE_AFTER_S:
                self.announce()
                time.sleep(1.0)
        dest_hash = bytes.fromhex(peer_hex)
        ident = self._recall(dest_hash)
        if ident is None:
            self.store.update_message(msg_id, state="failed",
                                      reason="unknown peer: no announce or path heard yet")
            self._notify_sessions_failed(peer_hex, msg_id)
            return
        dest = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
        if method == "propagated" and self.propagation_node():
            desired = LXMessage.PROPAGATED
        elif method == "direct":
            desired = LXMessage.DIRECT
        else:
            # LoRa-friendly default: one packet, no link setup. LXMF falls back
            # to a link by itself when the message is too big for one packet.
            desired = LXMessage.OPPORTUNISTIC
        lxm = LXMessage(dest, self.local, text, title=title, desired_method=desired)
        lxm.register_delivery_callback(lambda m, i=msg_id: self._on_delivered(i, m))
        lxm.register_failed_callback(
            lambda m, i=msg_id: self._on_failed(i, m, peer_hex, text, title, allow_propagation))
        with self.lock:
            self.outbound[msg_id] = lxm
        try:
            self.router.handle_outbound(lxm)
        except Exception as e:
            self.store.update_message(msg_id, state="failed", reason=str(e))
            with self.lock:
                self.outbound.pop(msg_id, None)
            self._notify_sessions_failed(peer_hex, msg_id)
            return
        self.store.update_message(msg_id, lxm_hash=lxm.hash.hex() if lxm.hash else None,
                                  method=_method_name(lxm.method))

    def _on_delivered(self, msg_id, lxm):
        state = "stored" if lxm.method == LXMessage.PROPAGATED else "delivered"
        self.store.update_message(msg_id, state=state, method=_method_name(lxm.method))
        with self.lock:
            self.outbound.pop(msg_id, None)

    def _on_failed(self, msg_id, lxm, peer_hex, text, title, allow_propagation):
        with self.lock:
            self.outbound.pop(msg_id, None)
        if (allow_propagation and lxm.method != LXMessage.PROPAGATED
                and self.settings["fallback_to_propagation"] and self.propagation_node()):
            self.store.update_message(msg_id, state="pending", reason="retrying via propagation node")
            threading.Thread(target=self._send_worker,
                             args=(msg_id, peer_hex, text, "propagated", title, False), daemon=True).start()
            return
        self.store.update_message(msg_id, state="failed", reason="not delivered")
        self._notify_sessions_failed(peer_hex, msg_id)

    def _notify_sessions_failed(self, peer_hex, msg_id):
        sess = self.sessions.get(mesh_key(peer_hex))
        if sess:
            sess.on_lxmf_failed(msg_id)

    def _monitor_outbound(self):
        names = {LXMessage.GENERATING: "stamping", LXMessage.OUTBOUND: "pending",
                 LXMessage.SENDING: "sending", LXMessage.SENT: "sent"}
        with self.lock:
            items = list(self.outbound.items())
        for msg_id, lxm in items:
            st = names.get(lxm.state)
            if st:
                row = self.store.message(msg_id)
                if row and row["state"] != st:
                    self.store.update_message(msg_id, state=st)

    # ================================================================ receiving
    def _on_message(self, lxm):
        peer = lxm.source_hash.hex()
        lxm_hash = lxm.hash.hex() if lxm.hash else None
        if lxm_hash and self.store.has_message(lxm_hash):
            return
        content = lxm.content_as_string() if lxm.content else ""
        attachments = [label for fid, label in FIELD_NAMES.items() if lxm.fields and fid in lxm.fields]
        verified = bool(getattr(lxm, "signature_validated", False))
        if not verified:
            RNS.Transport.request_path(lxm.source_hash)
        self.store.upsert_peer(peer, "lxmf", heard=True)
        self.store.add_message(peer, False, content, "received", ts=lxm.timestamp or time.time(),
                               title=lxm.title_as_string() if lxm.title else "",
                               method=_method_name(lxm.method), lxm_hash=lxm_hash,
                               rssi=getattr(lxm, "rssi", None), snr=getattr(lxm, "snr", None),
                               verified=verified, attachments=", ".join(attachments) or None,
                               unread=not self.is_stump(peer))
        if self.is_stump(peer) or mesh_key(peer) in self.sessions:
            self.open_stump_mesh(peer).on_mesh_content(content)
        else:
            peer_row = self.store.peer(peer) or {}
            self.emit({"type": "message", "peer": peer, "name": peer_row.get("name"), "text": content})

    # ================================================================ Stump
    def open_stump_mesh(self, node_hex):
        key = mesh_key(node_hex)
        with self.lock:
            sess = self.sessions.get(key)
            if sess is None:
                name = self.stumps.get(node_hex, (None,))[0] or (self.store.peer(node_hex) or {}).get("name")
                sess = StumpSession(self, MeshPipe(self, node_hex), name=name)
                self.sessions[key] = sess
            return sess

    def open_stump_wifi(self, base):
        key = wifi_key(base)
        with self.lock:
            sess = self.sessions.get(key)
            if sess is None:
                sess = StumpSession(self, WifiPipe(normalize_base(base), self.http),
                                    name=f"Stump Wi-Fi ({normalize_base(base).split('://')[-1]})")
                self.sessions[key] = sess
            return sess

    def session(self, key):
        if key in self.sessions:
            return self.sessions[key]
        kind, _, address = key.partition(":")
        if kind == "mesh":
            return self.open_stump_mesh(address)
        if kind == "wifi":
            return self.open_stump_wifi(address)
        raise KeyError(key)

    def close_stump(self, key, forget=False):
        with self.lock:
            sess = self.sessions.pop(key, None)
        if sess:
            sess.stop()
        if forget:
            node = self.store.stump_node(key)
            if node:
                self.store.upsert_stump_node(key, node["transport"], node["address"], active=0)

    # ================================================================ contacts
    def add_contact(self, hex_addr):
        hex_addr = hex_addr.strip().lower().replace("<", "").replace(">", "").replace(":", "")
        if len(hex_addr) != 32 or any(c not in "0123456789abcdef" for c in hex_addr):
            raise ValueError("an LXMF address is 32 hex characters")
        self.store.upsert_peer(hex_addr, "lxmf", heard=False, saved=1)
        RNS.Transport.request_path(bytes.fromhex(hex_addr))
        return hex_addr

    def hops(self, peer_hex):
        try:
            h = RNS.Transport.hops_to(bytes.fromhex(peer_hex))
            return None if h == RNS.Transport.PATHFINDER_M else h
        except Exception:
            return None

    # ================================================================ background jobs
    def _housekeeping(self):
        time.sleep(3)
        if self.settings["announce_interval_min"] > 0:
            self.announce()
        first_sync_done = False
        online_seen = set()
        while self.running:
            now = time.time()
            self._monitor_outbound()
            # A new way onto the network (radio attached, TCP connected, Wi-Fi
            # peer found): announce, so Stump nodes and peers learn our name and
            # path from a real announce, not only a path response.
            online = {id(i) for i in list(RNS.Transport.interfaces) if getattr(i, "online", False)}
            if online - online_seen and now - self.last_announce > 15:
                self.announce()
            online_seen = online
            ai = self.settings["announce_interval_min"] * 60
            if ai and now - self.last_announce > ai:
                self.announce()
            si = self.settings["sync_interval_min"] * 60
            if self.propagation_node() and self.router.propagation_transfer_state in (
                    LXMRouter.PR_IDLE, LXMRouter.PR_COMPLETE) and (
                    (not first_sync_done and now - self.started_at > 60) or (si and now - self.last_sync > si)):
                first_sync_done = True
                self.sync()
            self.sync_state = self.sync_status()
            time.sleep(MONITOR_INTERVAL_S)


def _auto_interface_class():
    """AutoInterface, able to run on Python builds without socket.if_nametoindex.

    Chaquopy's Python lacks it. AutoInterface already has a second way to get
    an interface index (Reticulum's own netinfo, used on Windows); this uses
    it whenever the socket function is missing. A subclass, not a patch.
    """
    from RNS.Interfaces.AutoInterface import AutoInterface

    class FireFlyAutoInterface(AutoInterface):
        def interface_name_to_index(self, ifname):
            if hasattr(socket, "if_nametoindex"):
                return socket.if_nametoindex(ifname)
            return self.netinfo.interface_names_to_indexes()[ifname]

    return FireFlyAutoInterface


def _write_rns_config(settings, config_dir):
    """No interfaces here on purpose: see the module docstring."""
    os.makedirs(config_dir, exist_ok=True)
    yn = lambda b: "Yes" if b else "No"
    text = "\n".join([
        "# Generated by FireFly on every start. Interfaces are attached at runtime.",
        "[reticulum]",
        f"  enable_transport = {yn(settings['transport'])}",
        "  share_instance = No",
        "  panic_on_interface_error = No",
        "", "[logging]", f"  loglevel = {int(settings['log_level'])}",
        "", "[interfaces]", ""])
    with open(os.path.join(config_dir, "config"), "w", encoding="utf-8") as f:
        f.write(text)


def _write_key_files(path, key):
    for target in (path, path + ".bak"):
        tmp = target + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)


def _plausible_key(path):
    """64 bytes and not blank: a power cut can leave a zero-filled file."""
    try:
        if os.path.getsize(path) != IDENTITY_SIZE:
            return False
        with open(path, "rb") as f:
            data = f.read()
        return len(set(data)) > 8
    except OSError:
        return False


def _method_name(m):
    return {LXMessage.OPPORTUNISTIC: "opportunistic", LXMessage.DIRECT: "direct",
            LXMessage.PROPAGATED: "propagated", LXMessage.PAPER: "paper"}.get(m)


class _DeliveryAnnounces:
    aspect_filter = "lxmf.delivery"
    receive_path_responses = True

    def __init__(self, core): self.core = core

    def received_announce(self, destination_hash, announced_identity, app_data):
        self.core._on_delivery_announce(destination_hash, announced_identity, app_data)


class _PropagationAnnounces:
    aspect_filter = "lxmf.propagation"
    receive_path_responses = True

    def __init__(self, core): self.core = core

    def received_announce(self, destination_hash, announced_identity, app_data):
        self.core._on_propagation_announce(destination_hash, app_data)
