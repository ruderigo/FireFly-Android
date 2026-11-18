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
import hashlib
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

from . import stump, ztext
from .radio import RadioManager
from .settings import Paths, Settings
from .store import Store
from .stump_session import MeshPipe, StumpSession, UrllibHttp, WifiPipe, mesh_key, normalize_base, wifi_key

PATH_WAIT_S = 20
INTRODUCE_AFTER_S = 30      # no extra announce if we announced this recently
PN_ASK_INTERVAL_S = 300     # how often to ask again for a Stump's propagation node until found
SYNC_MAX_ROUNDS = 5
SYNC_RETRY_WAITS_S = (10, 30)   # a failed round (no path, link failed) is retried after these
SYNC_SOON_MIN_GAP_S = 120   # event-driven syncs (radio up, app opened) at most this often
BEACON_ASK_GAP_S = 5        # at most one Stump-beacon path request per this, for announces heard
PEERS_HEARD_MAX = 500       # contacts only ever heard (never written to, not saved, not a Stump) kept
PN_SIZE_MARGIN = 96         # bytes a propagation stamp and transfer framing add to a packed message
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
    def __init__(self, home, links=None, http=None, listener=None, log=print, version=None, build=None):
        self.app_tag = stump.app_tag(version, build)      # in every announce: "I'm FireFly" (Stump beta.53)
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
        self.moved = {}             # an old Wi-Fi key -> the key of the same Stump at its new address
        self.beacon_http = stump.Recent()       # lxmf hex -> the web addresses its beacon gave (Stump 1.0.0-beta.21+)
        # Over LoRa, "online" is only what this run heard from the Stump itself, recently: its
        # announce or beacon, passed by fresh_announce (Nov 9: offline Stumps showed online, from
        # relays' remembered announces, opening a Stump, and times saved before the fix).
        self.announce_heard = stump.Recent()    # lxmf hex -> when its own announce came in (this run)
        self.beacon_heard = stump.Recent()      # lxmf hex -> when its own beacon came in (this run)
        self.boots = stump.Recent()             # lxmf hex -> its beacon's boot id, caps["b"] (Stump 1.0.0-beta.43+)
        self.restarted_at = stump.Recent()      # lxmf hex -> when its boot id last changed (it restarted)
        self.met_this_run = stump.RecentSet()   # Stumps whose own beacon we've heard since starting
        self._relocate_lock = threading.Lock()
        self._relocate_tried = stump.Recent()   # key -> when we last asked where it went
        self.extra_ifaces = []      # AutoInterface / TCP interfaces we attached
        self._introduced = stump.RecentSet()    # peers we've sent to this session (announced to first)
        self._beacon_asked = stump.RecentSet()  # peers whose Stump beacon we've asked a path for
        self.blocked = set()        # LXMF addresses whose announces and messages are ignored
        self.packs = stump.RecentSet()          # LXMF addresses whose announce says they read packed text (ztext.py)
        self.iface_errors = {}
        self.running = False
        self.radio = None
        self.started_at = time.time()
        self.last_announce = 0.0
        self.announced_on = set()
        self.last_sync = 0.0
        self.sync_state = "idle"
        self.sync_rounds = 0
        self._sync_chain = False
        self.pn_asked = stump.Recent()
        self._beacon_ask_at = 0.0       # the last Stump-beacon path asked for an announce heard          # propagation hash -> last time we asked for its path
        self.fast_stamps = False
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
        # Before any stamp is made (Reticulum 1.6.1 stamps announces): ours, single process.
        self.fast_stamps = _install_fast_stamper()
        if os.environ.get("FIREFLY_STAMP_VALUE") and hasattr(RNS.Transport, "DESTINATION_VALUE"):
            # Tests only (like the Stump team's reference tests): a cheap announce stamp.
            RNS.Transport.DESTINATION_VALUE = float(os.environ["FIREFLY_STAMP_VALUE"])
        self.identity = self._load_identity()
        self.router = LXMRouter(identity=self.identity, storagepath=self.paths.lxmf_storage)
        # Every announce says it's FireFly: the ones it sends, and the ones that answer a path
        # request (LXMF builds both with get_announce_app_data).
        # ... and, after the tag, that it reads packed text (unless switched off in the settings).
        lxmf_app_data = self.router.get_announce_app_data
        self.router.get_announce_app_data = lambda h: stump.with_app_tag(
            lxmf_app_data(h), self.app_tag, {"z": 1} if self.settings["packed_text"] else None)
        self.local = self.router.register_delivery_identity(self.identity, display_name=s["display_name"])
        self._stamp_in_background()
        self.router.register_delivery_callback(self._on_message)
        for b in self.store.blocked():
            self.blocked.add(b["hash"])
            self.router.ignore_destination(bytes.fromhex(b["hash"]))   # dropped inside LXMF too

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
                # Online only once it answers this run (not from the last run's mark).
                self.store.upsert_stump_node(node["key"], "wifi", node["address"], online=0)
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
            _bind_auto_sockets(self.http)
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
        # Messages may have been left for us while the radio was away.
        self.sync_soon(delay=15)

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
    def _stamp_in_background(self):
        """Reticulum 1.6.1 puts a proof-of-work stamp on every announce, and holds an announce
        (and the answer to a path request) until its stamp is made: once per address, then
        kept, but minutes on a phone the first time (Nov 18). Meanwhile FireFly couldn't be
        found or answered. So: no stamp kept yet -> announce at once without one, as before
        (no node asks for stamps unless set to), and make it in the background at low priority;
        once made (and kept by Reticulum), announce again, stamped, from then on.
        stamp_state: None (Reticulum before 1.6), "working", "ready" or "failed"."""
        self.stamp_state = None
        T = RNS.Transport
        if not hasattr(T, "STAMP_DESTINATIONS") or not hasattr(self.local, "_await_stamp"):
            return
        if not T.STAMP_DESTINATIONS or getattr(self.local, "stamp", None) is not None:
            self.stamp_state = "ready" if getattr(self.local, "stamp", None) is not None else None
            return
        T.STAMP_DESTINATIONS = False            # announce now, unstamped, while it's made
        self.stamp_state = "working"

        def work():
            try:                                # low priority: the app stays responsive
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
            except Exception:
                pass
            started, ok = time.time(), False
            try:
                ok = self.local._await_stamp()
            except Exception as e:
                RNS.log(f"Making the announce stamp failed: {e}", RNS.LOG_WARNING)
            if ok and getattr(self.local, "stamp", None) is not None:
                T.STAMP_DESTINATIONS = True
                self.stamp_state = "ready"
                RNS.log(f"Announce stamp made in {time.time() - started:.0f} s (kept): announcing stamped",
                        RNS.LOG_NOTICE)
                if self.running:
                    self.announce()
            else:
                self.stamp_state = "failed"     # announces stay unstamped, as before 1.6.1

        threading.Thread(target=work, daemon=True, name="firefly-stamp").start()

    def announce(self):
        self.local.display_name = self.settings["display_name"]
        self.router.announce(self.local.hash)
        self.last_announce = time.time()
        # Which interfaces this announce could actually go out on.
        self.announced_on = {id(i) for i in list(RNS.Transport.interfaces) if getattr(i, "online", False)}

    def _on_delivery_announce(self, dest_hash, identity, app_data, fresh=True):
        if dest_hash == self.local.hash or dest_hash.hex() in self.blocked:
            return
        name = LXMF.display_name_from_app_data(app_data) if app_data else None
        hops = RNS.Transport.hops_to(dest_hash)
        self.store.upsert_peer(dest_hash.hex(), "lxmf", name=name, hops=hops, heard=fresh,
                               stamp_cost=LXMF.stamp_cost_from_app_data(app_data) if app_data else None)
        h = dest_hash.hex()
        if app_data and stump.reads_packed(app_data):
            self.packs.add(h)
        else:
            self.packs.discard(h)
        if (identity and h not in self.stumps and h not in self._beacon_asked
                and time.time() - self._beacon_ask_at >= BEACON_ASK_GAP_S):
            # Asking is faster than waiting up to 30 minutes for the next beacon. At most one
            # every BEACON_ASK_GAP_S (Nov 17): a stream of new identities can't make the phone
            # transmit on every interface, LoRa included. One skipped is asked at its next announce.
            self._beacon_asked.add(h)
            self._beacon_ask_at = time.time()
            RNS.Transport.request_path(stump.beacon_hash_for(identity))
        if fresh:
            self.announce_heard[h] = time.time()
        if h in self.stumps and fresh:
            self.store.upsert_stump_node(mesh_key(h), "mesh", h, last_seen=time.time())

    def _on_stump_beacon(self, lxmf_hex, name, version, http=None, boot=None, fresh=True, release=None):
        """fresh: the beacon came from the Stump now (a relay's remembered copy isn't hearing
        it: it doesn't make it online, and its old boot id isn't a restart)."""
        first = lxmf_hex not in self.stumps
        if boot and fresh:
            # Another boot id (Stump 1.0.0-beta.43): it restarted, and forgot who proved who
            # they are. Its sessions drop "verified" and, if in use, verify again.
            before = self.boots.get(lxmf_hex)
            self.boots[lxmf_hex] = boot
            if before and before != boot:
                self.stump_restarted(lxmf_hex)
        if http:
            self.beacon_http[lxmf_hex] = list(http)
        self.stumps[lxmf_hex] = (name, version)
        if first:
            threading.Thread(target=self._ask_stump_pn, args=(lxmf_hex,), daemon=True).start()
        self.store.upsert_peer(lxmf_hex, "lxmf", heard=False, stump=name, stump_ver=version)
        rel = {"release": release} if release else {}
        if fresh:
            self.beacon_heard[lxmf_hex] = time.time()
            self.store.upsert_stump_node(mesh_key(lxmf_hex), "mesh", lxmf_hex, name=name, last_seen=time.time(), **rel)
            self._introduce_to_stump(lxmf_hex)
        else:
            self.store.upsert_stump_node(mesh_key(lxmf_hex), "mesh", lxmf_hex, name=name, **rel)

    def _introduce_to_stump(self, lxmf_hex):
        """The first time this run we hear a Stump itself: our announce, so it has our key before
        our first message and takes it as signed at once (Nov 13, with the Stump team). At most
        one announce per INTRODUCE_AFTER_S, however many Stumps are heard."""
        if lxmf_hex in self.met_this_run:
            return
        self.met_this_run.add(lxmf_hex)
        if time.time() - self.last_announce > INTRODUCE_AFTER_S:
            # Now, not in a thread: queued before anything we send it, so our first message
            # checks out (Nov 17: in a thread, a quick first message went out before it).
            try:
                self.announce()
            except Exception as e:
                RNS.log(f"announce for {lxmf_hex[:8]} failed: {e}", RNS.LOG_WARNING)

    def lora_signed(self, lxmf_hex):
        """Does this Stump (1.0.0-beta.71+) take a LoRa sender's checked LXMF signature as full
        proof (room tiers, /invite, /whoami)? Then FireFly sends it no /auth over LoRa."""
        # Its full release (beacon caps "r", kept), else the version item if it reads as one.
        rel = (self.store.stump_node(mesh_key(lxmf_hex)) or {}).get("release")
        beta = stump.release_beta(rel or (self.stumps.get(lxmf_hex) or (None, None))[1])
        return beta is not None and beta >= stump.LORA_SIGNED_FROM_BETA

    def stump_restarted(self, lxmf_hex):
        """A Stump beaconed a new boot id: it restarted. Over LoRa and Wi-Fi alike."""
        self.restarted_at[lxmf_hex] = time.time()
        RNS.log(f"Stump {lxmf_hex[:8]} restarted (new boot id)", RNS.LOG_NOTICE)
        for key in self.same_stump(mesh_key(lxmf_hex)):
            sess = self.sessions.get(key)
            if sess is not None:
                sess.peer_restarted()
            elif (self.store.stump_node(key) or {}).get("auth") == "ok" and not (
                    key.startswith("mesh:") and self.lora_signed(lxmf_hex)):
                # (Over LoRa, from Stump beta.71, our signed messages still prove who we are.)
                self.store.upsert_stump_node(key, key.partition(":")[0], key.partition(":")[2], auth="none")

    # ---------------------------------------------------------------- an address scanned or typed
    def address_check(self, text):
        """Where does this address go, the Stumps list or Chats? Decided at once when it can be
        (Oct-Nov 2026, with the Stump team): 1. the code says so (Stump 1.0.0-beta.52's link,
        stump= with a key that matches the address), 2. a Stump FireFly already knows, 3. a
        Stump on this Wi-Fi proving it's that one (STUMP? then a signed GET /hello, about a
        second), else 4. the user is asked ("ask", with a suggestion: Stump for the bare
        address a Stump's page shows, person for another app's link).
        -> {"kind": stump|person|ask|bad, "address", "name", "how", "suggest", "reachable", "error"}"""
        if stump.is_paper_message(text):
            return {"kind": "paper"}             # a message to read, not an address (LXMF paper message)
        a = stump.parse_address_link(text)
        if a is None:
            return {"kind": "bad", "error": "not an LXMF address (32 characters, 0-9 and a-f)"}
        h = a["address"]
        if a["key_bad"] or (a["key"] is not None and stump.identity_for(h, a["key"]) is None):
            return {"kind": "bad", "address": h,
                    "error": "not trusted: the code's key doesn't match its address"}
        if h == self.local.hash.hex():
            return {"kind": "bad", "address": h, "error": "that's FireFly's own address"}
        if h in self.blocked:
            return {"kind": "bad", "address": h, "error": "this address is blocked: unblock it in Settings first"}
        out = {"address": h, "reachable": a["key"] is not None or self._identity_known(h)}
        if a["stump"] is not None:
            return {**out, "kind": "stump", "how": "link", "name": a["stump"] or None}
        name = self.known_stump_name(h)
        if name is not None:
            return {**out, "kind": "stump", "how": "known", "name": name or None}
        card = self._stump_on_wifi(h)
        if card is not None:
            return {**out, "kind": "stump", "how": "wifi", "name": card.get("name") or None}
        if a["scheme"] == "lxma":
            # Columba's, MeshChatX's, a T-Deck's contact code: a person's, unless it's a Stump's
            # (a Stump shows one too since beta.53, for Columba): known, or on this Wi-Fi, above.
            return {**out, "kind": "person", "how": "lxma"}
        return {**out, "kind": "ask", "suggest": "person" if a["link"] else "stump"}

    def known_stump_name(self, h):
        """A Stump FireFly already knows by this LXMF address (its beacon, a LoRa entry, a Wi-Fi
        entry whose poll gave it): its name ("" when it has none yet), else None."""
        if h in self.stumps:
            return self.stumps[h][0] or ""
        node = self.store.stump_node(mesh_key(h))
        if node is not None:
            return node.get("name") or ""
        for n in self.store.stump_nodes():
            if n["transport"] == "wifi" and (n.get("lxmf") or "") == h:
                return n.get("name") or ""
        peer = self.store.peer(h) or {}
        return peer.get("stump") or None

    def _identity_known(self, h):
        try:
            return RNS.Identity.recall(bytes.fromhex(h)) is not None
        except Exception:
            return False

    def _stump_on_wifi(self, h):
        """A Stump on this Wi-Fi with this address, proven by its own signature (/hello). -> its
        card or None. About a second; nothing at all off Wi-Fi."""
        from . import discovery
        try:
            for c in discovery.ask(self.http, wait_ms=900):
                if c.get("lxmf") == h and c.get("base") and discovery.hello_proof(self.http, c["base"], h):
                    return c
        except Exception as e:
            RNS.log(f"Wi-Fi check for {h[:8]} failed: {e}", RNS.LOG_DEBUG)
        return None

    def read_paper_message(self, text):
        """An LXMF paper message (lxm://, a QR code from Sideband or any LXMF app): LXMF's own
        reader takes it, decrypts it if it's for us, and it arrives like any message (its
        sender's conversation, or its Stump). -> {"kind": "paper", "result": delivered |
        duplicate | not_for_you | bad, "peer": its sender, "stump": its Stumps entry if a Stump's}"""
        import base64
        uri = "".join(str(text or "").split())
        uri = "lxm://" + uri[6:]                  # the scheme as LXMF expects it, whatever the case
        out = {"kind": "paper", "peer": None, "stump": None}
        try:
            data = base64.urlsafe_b64decode(uri[6:].replace("/", "") + "==")
        except Exception:
            return {**out, "result": "bad"}
        if len(data) < LXMF.LXMessage.LXMF_OVERHEAD:
            return {**out, "result": "bad"}
        if data[:16] != self.local.hash:
            return {**out, "result": "not_for_you"}
        try:
            plain = self.local.decrypt(data[16:])
            out["peer"] = plain[:16].hex() if plain else None
        except Exception:
            plain = None
        if plain is None:
            return {**out, "result": "bad"}
        r = self.router.ingest_lxm_uri(uri, signal_local_delivery="delivered", signal_duplicate="duplicate")
        out["result"] = r if r in ("delivered", "duplicate") else "bad"
        if out["peer"] and self.known_stump_name(out["peer"]) is not None:
            out["stump"] = mesh_key(out["peer"])
        return out

    def address_add(self, text, kind):
        """Add what address_check said, or what the user chose for "ask": "stump" -> the Stumps
        list (the usual way in from there: its announce, its beacon, verifying on opening it);
        "person" -> a contact, for Chats. A key the link gives (checked) is handed to Reticulum
        first: the address can then be written to at once, before any announce.
        -> {"kind", "key" (Stumps) or "peer" (Chats), "new", "reachable"}"""
        a = stump.parse_address_link(text)
        if a is None:
            raise ValueError("not an LXMF address (32 characters, 0-9 and a-f)")
        h = a["address"]
        ident = None
        if a["key"] is not None:
            ident = stump.identity_for(h, a["key"])
            if ident is None:
                raise ValueError("not trusted: the code's key doesn't match its address")
            if not self._identity_known(h):
                RNS.Identity.remember(None, bytes.fromhex(h), ident.get_public_key(), None)
        if kind == "stump":
            r = self.add_stump_address(h, name=a["stump"] or None)
            if a.get("release") and not (self.store.stump_node(mesh_key(h)) or {}).get("release"):
                # Its release from the code (beta.79): only until a beacon of its own says.
                self.store.upsert_stump_node(mesh_key(h), "mesh", h, release=a["release"])
            r["kind"] = "stump"
        elif kind == "person":
            r = {"kind": "person", "peer": self.add_contact(h), "new": self.store.peer(h) is None}
        else:
            raise ValueError("a Stump or a person?")
        r["reachable"] = self._identity_known(h)
        return r

    def add_stump_address(self, text, name=None):
        """A Stump added by its address (its QR code or typed, Stump 1.0.0-beta.51's /address):
        into the Stumps list now, without waiting for it to announce itself. Then the usual way
        in: a path request brings its announce, which asks for its beacon (its name, its Wi-Fi
        addresses), and opening it verifies as for any Stump. -> {"key", "new"}."""
        h = stump.address_from_text(text)
        if h is None:
            raise ValueError("not an LXMF address (32 characters, 0-9 and a-f)")
        if h in self.blocked:
            raise ValueError("this address is blocked: unblock it in Settings first")
        if h == self.local.hash.hex():
            raise ValueError("that's FireFly's own address")
        key = mesh_key(h)
        node = self.store.stump_node(key)
        heard_name = self.stumps.get(h, (None,))[0]          # its beacon's (signed) has the last word
        if node is None:
            self.store.upsert_stump_node(key, "mesh", h, name=heard_name or name or f"<{h[:8]}>")
        else:
            if not node.get("active", 1):
                self.store.upsert_stump_node(key, "mesh", h, active=1)   # hidden before: back in the list
            if name and not heard_name and str(node.get("name") or "").startswith("<"):
                self.store.upsert_stump_node(key, "mesh", h, name=name)
        # Not a person to chat with: out of Chats (its beacon would do the same, later).
        self.store.upsert_peer(h, "lxmf", heard=False, stump=heard_name or name or f"<{h[:8]}>")
        self._beacon_asked.discard(h)            # ask it again, even if asked (and unanswered) before
        try:
            RNS.Transport.request_path(bytes.fromhex(h))
            ident = RNS.Identity.recall(bytes.fromhex(h))
            if ident is not None and h not in self.stumps:
                self._beacon_asked.add(h)
                RNS.Transport.request_path(stump.beacon_hash_for(ident))
        except Exception as e:
            RNS.log(f"Stump {h[:8]}: path request failed: {e}", RNS.LOG_WARNING)
        return {"key": key, "new": node is None}

    def is_stump(self, peer_hex):
        return peer_hex in self.stumps

    def _on_propagation_announce(self, dest_hash, app_data, identity=None, fresh=True):
        if not LXMF.pn_announce_data_is_valid(app_data):
            return
        try:
            d = msgpack.unpackb(app_data)
            info = {"enabled": bool(d[2]), "transfer_kb": int(d[3]), "sync_kb": int(d[4]),
                    "stamp_cost": int(d[5][0]), "stamp_flex": int(d[5][1])}
        except Exception:
            return
        name = None
        try:
            name = LXMF.pn_name_from_app_data(app_data)
        except Exception:
            pass
        stump = self._stump_on_identity(identity) if identity else None
        self.store.upsert_peer(dest_hash.hex(), "propagation", name=name or (stump or "Propagation node"),
                               hops=RNS.Transport.hops_to(dest_hash), extra=json.dumps(info),
                               stamp_cost=info["stamp_cost"], stump=stump, heard=fresh)
        if self.settings["propagation_mode"] == "auto" and info["enabled"]:
            self._auto_pick_propagation()
        self.emit({"type": "status"})

    def _stump_on_identity(self, identity):
        """The Stump whose LXMF address is on this identity, if any: its propagation node is the Stump's."""
        lxmf_hex = RNS.Destination.hash(identity, "lxmf", "delivery").hex()
        return self.stumps.get(lxmf_hex, (None,))[0]

    def _ask_stump_pn(self, lxmf_hex):
        """A propagation node announces itself rarely: upstream once when switched on, then at long
        intervals. A Stump's is on the same identity as its LXMF address, so derive it and ask."""
        ident = RNS.Identity.recall(bytes.fromhex(lxmf_hex))
        if ident is None:
            return
        pn = RNS.Destination.hash(ident, "lxmf", "propagation")
        if RNS.Identity.recall_app_data(pn) and self.store.peer(pn.hex()):
            return
        if time.time() - self.pn_asked.get(pn.hex(), 0) < PN_ASK_INTERVAL_S:
            return
        self.pn_asked[pn.hex()] = time.time()
        RNS.Transport.request_path(pn)
        # A path answer carries the announce data, which arrives through the announce handler.
        deadline = time.time() + PATH_WAIT_S
        while time.time() < deadline and self.running:
            data = RNS.Identity.recall_app_data(pn)
            if data:
                self._on_propagation_announce(pn, data, ident)
                return
            time.sleep(0.5)

    def add_propagation_node(self, hex_addr):
        """Use a propagation node given by address (a node may print it, as LaBuche does)."""
        hex_addr = hex_addr.strip().lower().strip("<>")
        if len(hex_addr) != 32 or any(c not in "0123456789abcdef" for c in hex_addr):
            raise ValueError("a propagation node address is 32 hex characters")
        self.settings.update({"propagation_mode": "manual", "propagation_node": hex_addr})
        self._apply_propagation_setting()
        self.pn_asked.pop(hex_addr, None)
        RNS.Transport.request_path(bytes.fromhex(hex_addr))
        self.sync_soon(delay=10, min_gap=0)
        return hex_addr

    def propagation_nodes(self):
        out = []
        current = self.propagation_node()
        for p in self.store.peers("propagation"):
            info = _pn_info(p)
            out.append({"hash": p["hash"], "name": p["name"], "stump": p["stump"], "hops": self.hops(p["hash"]),
                        "last_heard": p["last_heard"], "enabled": info.get("enabled", False),
                        "stamp_cost": info.get("stamp_cost"), "transfer_limit_kb": info.get("transfer_kb"),
                        "selected": p["hash"] == current})
        if current and not any(n["hash"] == current for n in out):
            out.append({"hash": current, "name": None, "stump": None, "hops": self.hops(current),
                        "enabled": None, "selected": True, "waiting": True})
        return out

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
        # Nodes don't peer yet (Stump), so everyone around a Stump should use that Stump's
        # node: a Stump's propagation node first, then the nearest.
        best, key = None, None
        for p in self.store.peers("propagation"):
            if not _pn_info(p).get("enabled") or not p["last_heard"] or time.time() - p["last_heard"] > 86400:
                continue
            k = (0 if p["stump"] else 1, self.hops(p["hash"]) or 99)
            if best is None or k < key:
                best, key = p, k
        if best:
            current = self.router.get_outbound_propagation_node()
            if current is None or current.hex() != best["hash"]:
                self.router.set_outbound_propagation_node(bytes.fromhex(best["hash"]))
                self.sync_soon(delay=5, min_gap=0)      # collect what's waiting there now

    def propagation_transfer_limit_kb(self):
        node = self.propagation_node()
        row = self.store.peer(node) if node else None
        limit = _pn_info(row).get("transfer_kb") if row else None
        if not limit and node:
            try:
                limit = int(msgpack.unpackb(RNS.Identity.recall_app_data(bytes.fromhex(node)))[3])
            except Exception:
                limit = None
        return limit

    def sync_soon(self, delay=0.0, min_gap=SYNC_SOON_MIN_GAP_S):
        """An event-driven sync (radio up, app opened, node chosen), never more often than min_gap."""
        def go():
            time.sleep(delay)
            if self.running and self.propagation_node() and not self._sync_chain \
                    and time.time() - self.last_sync > min_gap:
                self.sync()
        threading.Thread(target=go, daemon=True).start()

    def _syncing(self):
        return self.router.propagation_transfer_state not in (
            LXMRouter.PR_IDLE, LXMRouter.PR_COMPLETE, LXMRouter.PR_NO_PATH, LXMRouter.PR_LINK_FAILED,
            LXMRouter.PR_TRANSFER_FAILED, LXMRouter.PR_NO_IDENTITY_RCVD, LXMRouter.PR_NO_ACCESS)

    def propagation_node(self):
        if self.settings["propagation_mode"] == "off":
            return None
        node = self.router.get_outbound_propagation_node()
        return node.hex() if node else None

    def sync(self):
        """Collect what's waiting at the propagation node. One sync chain at a time:
        radio-up, app-opened, node-chosen and the button can all ask within seconds,
        and two chains sharing LXMF's one transfer state can end each other early."""
        with self.lock:
            if self._sync_chain:
                return False                    # already collecting: that chain covers this request
            if not self.propagation_node():
                self.sync_state = "no propagation node"
                return False
            self._sync_chain = True
        threading.Thread(target=self._sync_chain_run, daemon=True, name="firefly-sync").start()
        return True

    def _sync_chain_run(self):
        """A sync reply is capped (~14 KB on a Stump), and LXMF doesn't fetch the rest by
        itself: sync again while rounds bring messages, up to SYNC_MAX_ROUNDS."""
        done = (LXMRouter.PR_COMPLETE, LXMRouter.PR_NO_PATH, LXMRouter.PR_LINK_FAILED,
                LXMRouter.PR_TRANSFER_FAILED, LXMRouter.PR_NO_IDENTITY_RCVD, LXMRouter.PR_NO_ACCESS)
        try:
            rnd, failures = 1, 0
            while rnd <= SYNC_MAX_ROUNDS and self.running and self.propagation_node():
                self.last_sync = time.time()
                self.sync_rounds = rnd
                self.router.request_messages_from_propagation_node(self.identity)
                time.sleep(1)
                deadline = time.time() + 180
                while time.time() < deadline and self.running:
                    if self.router.propagation_transfer_state in done:
                        break
                    time.sleep(0.5)
                state = self.router.propagation_transfer_state
                if state != LXMRouter.PR_COMPLETE:
                    # No path yet, link failed, transfer failed: normal on LoRa, and
                    # likeliest right after start-up. Retry the same round, backing off.
                    if failures >= len(SYNC_RETRY_WAITS_S):
                        break
                    time.sleep(SYNC_RETRY_WAITS_S[failures])
                    failures += 1
                    continue
                if not (self.router.propagation_transfer_last_result or 0):
                    break                       # the node is empty for us
                rnd += 1
        finally:
            with self.lock:
                self._sync_chain = False

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
    def send(self, peer_hex, text, method="auto", title="", allow_propagation=True, audio=None):
        """Queue a message. `audio` = (LXMF audio mode, encoded bytes) for a voice note.
        Returns the message id; state updates arrive in the Store."""
        text = text.rstrip()
        if (not text and not audio) or peer_hex in self.blocked:
            return None
        msg_id = self.store.add_message(peer_hex, True, text, "pending", title=title, audio=audio)
        threading.Thread(target=self._send_worker,
                         args=(msg_id, peer_hex, text, method, title, allow_propagation, audio),
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

    def _send_worker(self, msg_id, peer_hex, text, method, title, allow_propagation, audio=None):
        if peer_hex not in self._introduced:
            # The recipient checks our signature with our public key, which it
            # only has once it has heard an announce. Announce just before the
            # first message of the session, so it isn't flagged "unverified".
            self._introduced.add(peer_hex)
            # A Stump: right before its first message, unless we announced just now (an announce
            # at start-up can go out before the link to it is up, Nov 17: it then took our
            # first message as unverified).
            if time.time() - self.last_announce > (2 if peer_hex in self.stumps else INTRODUCE_AFTER_S):
                self.announce()
                time.sleep(1.0)
        dest_hash = bytes.fromhex(peer_hex)
        ident = self._recall(dest_hash)
        if ident is None:
            self.store.update_message(msg_id, state="failed",
                                      reason="unknown peer: no announce or path heard yet")
            self._notify_sessions_failed(peer_hex, msg_id)
            return
        if method == "auto" and allow_propagation and self.propagation_node() and not self._wait_for_path(dest_hash):
            # We know who they are but nothing on the network knows a way to
            # them: they're offline. Direct attempts would only burn airtime
            # before failing, so leave it at the propagation node straight away.
            method = "propagated"
        if self.store.message(msg_id) is None:
            return                              # deleted with its conversation while we waited
        dest = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
        if method == "propagated" and self.propagation_node():
            desired = LXMessage.PROPAGATED
        elif method == "direct":
            desired = LXMessage.DIRECT
        else:
            # LoRa-friendly default: one packet, no link setup. LXMF falls back
            # to a link by itself when the message is too big for one packet.
            desired = LXMessage.OPPORTUNISTIC
        # The standard LXMF audio field, [mode, bytes]: what Sideband sends and plays.
        fields = {LXMF.FIELD_AUDIO: [int(audio[0]), bytes(audio[1])]} if audio else None
        # To someone who reads packed text, the text goes shorter: more of it in one LoRa packet
        # (the Store, and every retry, keep the text itself).
        content = ztext.pack(text.encode("utf-8")) if peer_hex in self.packs and self.settings["packed_text"] else text
        lxm = LXMessage(dest, self.local, content, title=title, desired_method=desired, fields=fields)
        if desired == LXMessage.PROPAGATED:
            limit_kb = self.propagation_transfer_limit_kb()
            if limit_kb:
                lxm.pack()
                size = len(lxm.packed) + PN_SIZE_MARGIN
                if size > limit_kb * 1000:
                    self.store.update_message(msg_id, state="failed", method="propagated",
                                              reason=f"too large for the propagation node "
                                                     f"({size / 1000:.1f} KB, its limit is {limit_kb} KB)")
                    self._notify_sessions_failed(peer_hex, msg_id)
                    return
        lxm.register_delivery_callback(lambda m, i=msg_id: self._on_delivered(i, m))
        lxm.register_failed_callback(
            lambda m, i=msg_id: self._on_failed(i, m, peer_hex, text, title, allow_propagation, audio))
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

    def _wait_for_path(self, dest_hash, wait=PATH_WAIT_S):
        if RNS.Transport.has_path(dest_hash):
            return True
        RNS.Transport.request_path(dest_hash)
        deadline = time.time() + wait
        while time.time() < deadline and self.running:
            if RNS.Transport.has_path(dest_hash):
                return True
            time.sleep(0.5)
        return False

    def _on_delivered(self, msg_id, lxm):
        state = "stored" if lxm.method == LXMessage.PROPAGATED else "delivered"
        with self.lock:                         # out of outbound first: see _monitor_outbound
            self.outbound.pop(msg_id, None)
            self.store.update_message(msg_id, state=state, method=_method_name(lxm.method))

    def _on_failed(self, msg_id, lxm, peer_hex, text, title, allow_propagation, audio=None):
        with self.lock:
            self.outbound.pop(msg_id, None)
        if self.store.message(msg_id) is None:
            return                              # deleted: no fallback to a propagation node
        if (allow_propagation and lxm.method != LXMessage.PROPAGATED
                and self.settings["fallback_to_propagation"] and self.propagation_node()):
            self.store.update_message(msg_id, state="pending", reason="retrying via propagation node")
            threading.Thread(target=self._send_worker,
                             args=(msg_id, peer_hex, text, "propagated", title, False, audio), daemon=True).start()
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
                # Under the lock, and only while the message is still outbound:
                # _on_delivered/_on_failed take it out of self.outbound under the
                # same lock before writing the final state. Without this, a pass
                # that read the list just before delivery could overwrite
                # "stored" with "sent" (LXMF leaves a propagated message in SENT
                # when it calls the delivery callback), and nothing would ever
                # correct it.
                with self.lock:
                    if self.outbound.get(msg_id) is not lxm:
                        continue
                    row = self.store.message(msg_id)
                    if row and row["state"] != st:
                        self.store.update_message(msg_id, state=st)

    # ================================================================ receiving
    def _on_message(self, lxm):
        peer = lxm.source_hash.hex()
        if peer in self.blocked:
            return                          # LXMF drops these first; this covers anything that slips by
        lxm_hash = lxm.hash.hex() if lxm.hash else None
        if lxm_hash and self.store.has_message(lxm_hash):
            return
        content = self._text_of(lxm)
        audio = _audio_field(lxm.fields)
        attachments = [label for fid, label in FIELD_NAMES.items()
                       if lxm.fields and fid in lxm.fields and not (fid == LXMF.FIELD_AUDIO and audio)]
        verified = bool(getattr(lxm, "signature_validated", False))
        if not verified:
            RNS.Transport.request_path(lxm.source_hash)
        self.store.upsert_peer(peer, "lxmf", heard=True)
        self.store.add_message(peer, False, content, "received", ts=lxm.timestamp or time.time(),
                               title=lxm.title_as_string() if lxm.title else "",
                               method=_method_name(lxm.method), lxm_hash=lxm_hash,
                               rssi=getattr(lxm, "rssi", None), snr=getattr(lxm, "snr", None),
                               verified=verified, attachments=", ".join(attachments) or None,
                               unread=not self.is_stump(peer), audio=audio)
        if self.is_stump(peer) or mesh_key(peer) in self.sessions:
            self.open_stump_mesh(peer).on_mesh_content(content, audio=audio,
                                                       title=lxm.title_as_string() if lxm.title else None)
        else:
            peer_row = self.store.peer(peer) or {}
            self.emit({"type": "message", "peer": peer, "name": peer_row.get("name"), "text": content,
                       "audio": bool(audio)})

    def _text_of(self, lxm):
        """A message's text: unpacked first when it came packed (ztext.py). Bytes that aren't
        UTF-8 show as replacement characters (LXMF's content_as_string() gives None for those,
        and the Store's content can't be NULL)."""
        raw = lxm.content
        if not raw:
            return ""
        if raw[0] >= ztext.PLAIN and self.settings["packed_text"]:
            text = ztext.unpack(raw)
            if text is not None:
                raw = text
        return bytes(raw).decode("utf-8", "replace")

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

    def open_stump_wifi(self, base, carry=None):
        key = wifi_key(base)
        with self.lock:
            sess = self.sessions.get(key)
            if sess is None:
                # A saved entry keeps its name ("macOS"); the address is only a placeholder
                # for a Stump not heard yet (its first poll gives its name).
                saved = (self.store.stump_node(key) or {}).get("name")
                sess = StumpSession(self, WifiPipe(normalize_base(base), self.http),
                                    name=saved or f"Stump Wi-Fi ({normalize_base(base).split('://')[-1]})", carry=carry)
                self.sessions[key] = sess
            return sess

    def retire_old_wifi(self, lxmf, keep):
        """Other Wi-Fi entries of the Stump with this LXMF address are its old addresses:
        their history moves to `keep`, and they go."""
        keep_addr = keep.partition(":")[2]
        for n in self.store.stump_nodes():
            if n["transport"] == "wifi" and n["key"] != keep and (n.get("lxmf") or "") == lxmf:
                self.close_stump(n["key"])
                self.store.rekey_stump(n["key"], keep, keep_addr)
                self.moved[n["key"]] = keep

    def move_wifi(self, old_key, new_base, note=None):
        """The Stump at `old_key` now answers at `new_base`: same entry, same history,
        new address. -> the new session."""
        new_base = normalize_base(new_base)
        new_key = wifi_key(new_base)
        if new_key == old_key:
            return self.session(old_key)
        old = self.sessions.get(old_key)
        carry = {"seen": set(old.seen_remote), "since": getattr(old.pipe, "since", 0)} if old else None
        self.close_stump(old_key)
        self.store.rekey_stump(old_key, new_key, new_base)
        self.moved[old_key] = new_key
        self.moved.pop(new_key, None)
        sess = self.open_stump_wifi(new_base, carry=carry)
        if note:
            self.store.add_line(new_key, sess.room, "reply", note)
        self.log(f"Stump moved: {old_key} -> {new_key}")
        return sess

    RELOCATE_EVERY_S = 15

    def relocate(self, key):
        """A Wi-Fi Stump stopped answering: ask the Wi-Fi where the Stump with its
        identity is now (STUMP?, then its beacon's addresses), and follow it.
        -> the session at its new address, or None (not found, or same address)."""
        with self._relocate_lock:
            if key in self.moved:
                return self.sessions.get(self.resolve(key))
            node = self.store.stump_node(key)
            lxmf = (node or {}).get("lxmf")
            if not node or node["transport"] != "wifi" or not lxmf:
                return None
            now = time.time()
            if now - self._relocate_tried.get(key, 0) < self.RELOCATE_EVERY_S:
                return None
            self._relocate_tried[key] = now
            from . import discovery
            card = discovery.locate(self.http, lxmf, self.beacon_http.get(lxmf, ()))
            if not card or not card["base"] or wifi_key(card["base"]) == key:
                return None
            name = node.get("name") or card.get("name") or "the Stump"
            return self.move_wifi(key, card["base"], note=f"found {name} at its new address ({card['base']})")

    def refresh_wifi(self, cards=None):
        """Stumps we know that answer on this Wi-Fi: open them there (a Wi-Fi entry for
        one we only knew over LoRa), or follow them to their new address. -> keys."""
        from . import discovery
        cards = discovery.ask(self.http) if cards is None else cards
        rows = self.store.stump_nodes()
        out = []
        for c in cards:
            lx = c.get("lxmf")
            if not lx or not c.get("base"):
                continue
            wifi = [n for n in rows if n["transport"] == "wifi" and (n.get("lxmf") or "") == lx]
            here = wifi_key(c["base"])
            if any(n["key"] == here for n in wifi):
                self.session(here)
                out.append(here)
            elif wifi:
                out.append(self.move_wifi(wifi[0]["key"], c["base"],
                                          note=f"found {wifi[0].get('name') or c.get('name') or 'the Stump'} at its new address ({c['base']})").key)
            elif any(n["transport"] == "mesh" and n["address"] == lx for n in rows):
                sess = self.open_stump_wifi(c["base"])
                self.store.upsert_stump_node(sess.key, "wifi", sess.pipe.address, lxmf=lx,
                                             name=c.get("name") or None)
                out.append(sess.key)
        return out

    def find_wifi(self, lxmf):
        """The Wi-Fi entry of the Stump with this identity, found now if need be. -> key or None."""
        from . import discovery
        card = discovery.locate(self.http, lxmf, self.beacon_http.get(lxmf, ()))
        if not card or not card["base"]:
            return None
        keys = self.refresh_wifi([card])
        return keys[0] if keys else None

    def radio_online(self):
        try:
            return bool(self.radio) and self.radio.snapshot().get("state") == "online"
        except Exception:
            return False

    def can_reach(self, peer_hex):
        """Something can carry a message there: the radio, or a known path (Wi-Fi, TCP)."""
        if self.radio_online():
            return True
        try:
            return RNS.Transport.has_path(bytes.fromhex(peer_hex))
        except Exception:
            return False

    def stump_id(self, key):
        """Who a Stump is, whichever entry: its LXMF address, else the entry's key."""
        node = self.store.stump_node(key) or {}
        return (node.get("address") if node.get("transport") == "mesh" else node.get("lxmf")) or key

    def stump_blocks(self, key, nick):
        return str(nick or "").lstrip("~").lower() in self.store.stump_blocked(self.stump_id(key))

    MESH_ALIVE_S = 10 * 60          # a Stump announces its LXMF address every 2 min
    BEACON_ALIVE_S = 35 * 60        # and its stump.node beacon every 30 min

    def stump_reach(self, key, on_wifi=None, probe=False):
        """How this Stump can be reached now, all its sides together:
        {"state": "wifi" | "lora" | "no_wifi" | "offline", "web": the Wi-Fi key answering or None,
         "lora": bool, "name"}. on_wifi: is the phone on a Wi-Fi (the app knows; None: don't
        know). probe: ask the Wi-Fi side now (GET /node.json, 1.5 s) instead of trusting its
        last poll; one that doesn't answer is marked offline."""
        keys = self.same_stump(self.resolve(key))
        nodes = [n for n in (self.store.stump_node(k) for k in keys) if n]
        wifi = [n for n in nodes if n["transport"] == "wifi"]
        mesh = next((n for n in nodes if n["transport"] == "mesh"), None)
        web = None
        if on_wifi is not False:
            for n in sorted(wifi, key=lambda n: -(n.get("online") or 0)):
                if probe:
                    if self._web_answers(n["key"]):
                        web = n["key"]
                        break
                    sess = self.sessions.get(n["key"])
                    if sess is not None:
                        sess.set_online(False, "no answer")
                elif n.get("online"):
                    web = n["key"]
                    break
        lora = False
        if mesh:
            now = time.time()
            announced = self.announce_heard.get(mesh["address"], 0)       # heard by this run, itself
            beaconed = self.beacon_heard.get(mesh["address"], 0)
            recent = now - announced < self.MESH_ALIVE_S or now - beaconed < self.BEACON_ALIVE_S
            lora = bool(recent and self.can_reach(mesh["address"]))
        if web:
            state = "wifi"
        elif lora:
            state = "lora"
        elif on_wifi is False and not mesh:
            state = "no_wifi"               # a Wi-Fi Stump, and the phone isn't on a Wi-Fi
        else:
            state = "offline"
        name = next((n["name"] for n in nodes if n.get("name") and not str(n["name"]).startswith("Stump Wi-Fi (")),
                    None) or (nodes[0].get("name") if nodes else None)
        return {"state": state, "web": web, "lora": lora, "name": name}

    def _web_answers(self, key):
        try:
            r = self.http.request("GET", key.partition(":")[2] + "/node.json", "{}", None, 1500)
            from .stump_web import _attr
            return int(_attr(r, "status")) > 0         # any answer: it's there
        except Exception:
            return False

    def refresh_stumps(self, on_wifi=None):
        """Pull to refresh: ask every Stump we know whether it's there. The Wi-Fi ones are
        probed (and looked for on this Wi-Fi, STUMP?); the LoRa ones are asked for a path,
        which they answer with an announce (heard: alive). -> how many answered on Wi-Fi."""
        answered = 0
        if on_wifi is not False:
            try:
                self.refresh_wifi()
            except Exception as e:
                self.log(f"refresh: STUMP? failed: {e}")
            for n in self.store.stump_nodes():
                if n["transport"] != "wifi":
                    continue
                ok = self._web_answers(n["key"])
                answered += ok
                sess = self.sessions.get(n["key"])
                if sess is not None:
                    sess.set_online(ok, None if ok else "no answer")
                else:
                    self.store.upsert_stump_node(n["key"], "wifi", n["address"], online=1 if ok else 0)
        for n in self.store.stump_nodes():
            if n["transport"] == "mesh":
                try:
                    h = bytes.fromhex(n["address"])
                    RNS.Transport.request_path(h)       # its LXMF announce comes back: heard
                    ident = RNS.Identity.recall(h)
                    if ident:
                        RNS.Transport.request_path(stump.beacon_hash_for(ident))
                except Exception:
                    pass
        self.emit({"type": "changed"})
        return answered

    def same_stump(self, key):
        """Every entry of the Stump behind `key`: its LoRa entry and its Wi-Fi entries share
        its LXMF address. -> [key, ...], `key` first."""
        node = self.store.stump_node(key) or {}
        lx = node.get("address") if node.get("transport") == "mesh" else node.get("lxmf")
        keys = [key]
        if lx:
            for n in self.store.stump_nodes():
                if n["key"] != key and (n["key"] == "mesh:" + lx or (n["transport"] == "wifi" and (n.get("lxmf") or "") == lx)):
                    keys.append(n["key"])
        return keys

    def resolve(self, key):
        """A Wi-Fi key that moved to a new address -> the key it has now."""
        seen = set()
        while key in self.moved and key not in seen:
            seen.add(key)
            key = self.moved[key]
        return key

    def session(self, key):
        key = self.resolve(key)
        if key in self.sessions:
            return self.sessions[key]
        kind, _, address = key.partition(":")
        if kind == "mesh":
            return self.open_stump_mesh(address)
        if kind == "wifi":
            return self.open_stump_wifi(address)
        raise KeyError(key)

    def voice_mode_for(self, peer_hex):
        """The audio mode for a voice note to this peer. Automatic: Opus when our link
        toward them isn't LoRa, Codec 2 1200 when it is (or when we can't tell)."""
        chosen = self.settings["voice_mode"]
        if chosen != VOICE_AUTO:
            return chosen
        return OPUS_OGG if self._path_avoids_lora(peer_hex) else VOICE_DEFAULT_CODEC2

    def _path_avoids_lora(self, peer_hex):
        try:
            dest = bytes.fromhex(peer_hex)
        except ValueError:
            return False
        if not RNS.Transport.has_path(dest):
            # Offline: the note would be left at the propagation node, so it's that link that counts.
            pn = self.propagation_node()
            if not pn or not RNS.Transport.has_path(bytes.fromhex(pn)):
                return False
            dest = bytes.fromhex(pn)
        iface = RNS.Transport.next_hop_interface(dest)
        # Our first hop is what our airtime pays for. An RNode interface has radio
        # parameters (r_sf); TCP, local network and the rest don't.
        return iface is not None and not hasattr(iface, "r_sf")

    def stump_voice_mode(self, key=None):
        """Voice DMs through a Stump follow the same setting as any chat: Opus by default.
        Automatic judges the link to the node itself (LoRa: Codec 2; Wi-Fi: Opus)."""
        chosen = self.settings["voice_mode"]
        if chosen != VOICE_AUTO:
            return chosen
        if key and key.startswith("mesh:"):
            return self.voice_mode_for(key.partition(":")[2])
        return OPUS_OGG

    def block(self, peer_hex):
        """Delete everything with this person and ignore them from now on: announces and
        messages, also from a propagation node. Needs only the address."""
        peer_hex = _clean_address(peer_hex)
        name = (self.store.peer(peer_hex) or {}).get("name")
        self.delete_conversation(peer_hex)
        self.store.block(peer_hex, name)
        self.blocked.add(peer_hex)
        self.router.ignore_destination(bytes.fromhex(peer_hex))

    def unblock(self, peer_hex):
        """They come back only if they announce or write."""
        peer_hex = _clean_address(peer_hex)
        self.store.unblock(peer_hex)
        self.blocked.discard(peer_hex)
        self.router.unignore_destination(bytes.fromhex(peer_hex))

    def delete_conversation(self, peer_hex):
        """Cancel anything still being sent to this peer, then delete the conversation
        and the contact completely (also how a peer is removed from "heard")."""
        with self.lock:
            inflight = [(i, lxm) for i, lxm in self.outbound.items()
                        if lxm.destination_hash and lxm.destination_hash.hex() == peer_hex]
            for i, _ in inflight:
                self.outbound.pop(i, None)
        for _, lxm in inflight:
            try:
                self.router.cancel_outbound(lxm.message_id)
            except Exception as e:
                RNS.log(f"Could not cancel a message being sent: {e}", RNS.LOG_WARNING)
        self.store.delete_conversation(peer_hex)

    def hide_stump(self, key):
        """Off the list, history kept. Comes back on its own if it sends us something."""
        self.close_stump(key, forget=True)

    def remove_stump(self, key):
        """Delete everything about this Stump. If it's still on the air, its next beacon
        brings it back as a new, empty entry; a node that changed identity never does."""
        node = self.store.stump_node(key)
        self.close_stump(key)
        address = node["address"] if node else key.partition(":")[2]
        if key.startswith("mesh:"):
            self.delete_conversation(address)        # cancels anything still being sent to it
            self.stumps.pop(address, None)
            self._beacon_asked.discard(address)
        self.store.delete_stump(key, address)

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
        hex_addr = _clean_address(hex_addr)
        if hex_addr in self.blocked:
            raise ValueError("this address is blocked: unblock it in Settings first")
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
            if online - online_seen:
                # An interface that wasn't up for our last announce never carried it.
                if online - self.announced_on and now - self.last_announce > 5:
                    self.announce()
                for h in list(self.stumps):
                    threading.Thread(target=self._ask_stump_pn, args=(h,), daemon=True).start()
            online_seen = online
            if not self.propagation_node() and now - getattr(self, "_last_pn_sweep", 0) > PN_ASK_INTERVAL_S:
                self._last_pn_sweep = now
                for h in list(self.stumps):
                    threading.Thread(target=self._ask_stump_pn, args=(h,), daemon=True).start()
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


from .audio import CODEC2_FRAMES, MAX_VOICE_BYTES, OPUS_OGG

VOICE_AUTO, VOICE_DEFAULT_CODEC2 = 0, 4      # Automatic; Codec 2 1200


def audio_duration_ms(mode, length):
    """Codec 2 length from a byte count (Opus needs the bytes: audio.duration_ms)."""
    f = CODEC2_FRAMES.get(mode)
    return (length // f[0]) * f[1] if f and length else None


def _audio_field(fields):
    """(mode, bytes) from a received FIELD_AUDIO, or None if absent or malformed."""
    try:
        a = (fields or {}).get(LXMF.FIELD_AUDIO)
        if isinstance(a, (list, tuple)) and len(a) >= 2 and isinstance(a[1], (bytes, bytearray)):
            if 0 < len(a[1]) <= MAX_VOICE_BYTES:
                return int(a[0]), bytes(a[1])
    except Exception:
        pass
    return None


def _pn_info(peer_row):
    """Propagation node details stored with the peer ("on"/"off" in databases from 0.2.0)."""
    x = peer_row.get("extra")
    if x in ("on", "off"):
        return {"enabled": x == "on"}
    try:
        return json.loads(x) if x else {}
    except ValueError:
        return {}


def fast_stamp_job(stamp_cost, workblock, message_id):
    """LXMF's stamp search, with the same result and ~200x less work.

    A stamp is valid when SHA-256(workblock + stamp) is under the target.
    LXMF hashes the whole work block (250 KB for a propagation stamp, 750 KB
    for a delivery stamp) again for every attempt; SHA-256 reads its input
    in order, so the work block's part is computed once and each attempt
    only hashes its own 32 bytes on top of a copy of that state. Same
    contract as LXMF's job_simple: (stamp, rounds), stamp None if cancelled
    through LXStamper.cancel_work().
    """
    from LXMF import LXStamper
    target = 1 << (256 - stamp_cost)
    base = hashlib.sha256(workblock)
    LXStamper.active_jobs[message_id] = False
    rounds, stamp = 0, None
    try:
        while stamp is None and not LXStamper.active_jobs.get(message_id):
            pool = os.urandom(32 * 2048)            # 2048 candidates per cancellation check
            for i in range(0, len(pool), 32):
                candidate = pool[i:i + 32]
                h = base.copy()
                h.update(candidate)
                rounds += 1
                if int.from_bytes(h.digest(), "big") <= target:
                    stamp = candidate
                    break
    finally:
        if LXStamper.active_jobs.pop(message_id, False):
            stamp = None
    return stamp, rounds


def fast_rns_stamp_job(stamp_cost, workblock, message_id, size, mode=0):
    """Reticulum 1.6's stamp search (RNS.Stamps.job_android's contract), single process.

    From LXMF 1.3 every stamp is made by RNS.Stamps: message stamps (LXMF's 32 bytes, a 750 KB
    work block) and announce stamps (14 bytes). Its Android path forks worker processes with
    Python multiprocessing, which Chaquopy's Python doesn't have (and forking an app's process
    isn't safe); a stamp that can't be made cancels the announce. Same rules as Reticulum's
    job_simple (the fixed-point cost's target, `size`-byte candidates, cancelled through
    RNS.Stamps.active_jobs), with the work block's SHA-256 state computed once and each
    candidate hashed on a copy of it. Key stamps (MODE_KEYSTAMP) go to Reticulum's job_simple.
    -> (stamp or None, rounds)."""
    from RNS import Stamps
    if mode != getattr(Stamps, "MODE_STAMP", 0):
        return Stamps.job_simple(stamp_cost, workblock, message_id, size, mode)
    target = Stamps.target_from_fp(stamp_cost)
    base = hashlib.sha256(workblock or b"")
    Stamps.active_jobs[message_id] = False
    rounds, stamp = 0, None
    try:
        while stamp is None and not Stamps.active_jobs.get(message_id):
            pool = os.urandom(size * 2048)          # 2048 candidates per cancellation check
            for i in range(0, len(pool), size):
                candidate = pool[i:i + size]
                h = base.copy()
                h.update(candidate)
                rounds += 1
                if int.from_bytes(h.digest(), "big") <= target:
                    stamp = candidate
                    break
    finally:
        if Stamps.active_jobs.pop(message_id, False):
            stamp = None
    return stamp, rounds


def _install_fast_stamper():
    """On Android, make stamps without multiprocessing (Chaquopy's Python has none): from
    Reticulum 1.6 / LXMF 1.3 in RNS.Stamps (message and announce stamps), before that in
    LXMF's own stamper. Faster than their paths would be anyway. Returns True when installed."""
    import RNS.vendor.platformutils as pu
    if not pu.is_android():
        return False
    try:
        import RNS.Stamps as rns_stamps
        rns_stamps.job_android = fast_rns_stamp_job
    except ImportError:
        pass
    from LXMF import LXStamper
    if hasattr(LXStamper, "job_android"):           # LXMF before 1.3
        LXStamper.job_android = fast_stamp_job
    return True


def _clean_address(hex_addr):
    """An LXMF address as 32 lowercase hex characters, or ValueError."""
    h = str(hex_addr).strip().lower().replace("<", "").replace(">", "").replace(":", "")
    if len(h) != 32 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("an LXMF address is 32 hex characters")
    return h


def _bind_auto_sockets(http):
    """Each socket AutoInterface makes, bound to the Wi-Fi network (Android: the http bridge's
    bindSocketToWifi). Unbound, on a Wi-Fi with no internet Android sent the Local network
    link's packets over mobile data ("Network is unreachable"): the phone was heard on the
    Wi-Fi but couldn't answer there, and computer Stumps routed to it that way (Stump
    1.0.0-beta.80 recovers, after two lost messages each time). Only that module's sockets: the
    name `socket` in RNS.Interfaces.AutoInterface becomes a stand-in whose socket() binds what
    it makes; everything else is the real module. No-op without a bridge that binds (desktop,
    tests). Its listeners (socketserver) only receive, which needs no binding; the replies and
    delivery proofs go out through the sockets bound here."""
    binder = getattr(http, "bindSocketToWifi", None) if http is not None else None
    if binder is None:
        return False
    import RNS.Interfaces.AutoInterface as auto
    if getattr(auto.socket, "_firefly_wifi", False):
        auto.socket.binder = binder
        return True
    real = auto.socket

    class _WifiSockets:
        _firefly_wifi = True

        def __init__(self):
            self.binder, self.bound, self.refused = binder, 0, 0

        def __getattr__(self, name):
            return getattr(real, name)

        def socket(self, *args, **kwargs):
            s = real.socket(*args, **kwargs)
            try:
                if self.binder(s.fileno()):
                    self.bound += 1
                else:
                    self.refused += 1
            except Exception as e:
                self.refused += 1
                RNS.log(f"Local network socket not bound to the Wi-Fi: {e}", RNS.LOG_DEBUG)
            return s

    auto.socket = _WifiSockets()
    return True


def _auto_interface_class():
    """AutoInterface, able to run on Python builds without socket.if_nametoindex.

    Chaquopy's Python lacks it. Reticulum's netinfo is no way around that on
    Linux/Android: it gets its indexes from socket.if_nametoindex too, and gives
    None without it. So this asks the C library directly (Android's Bionic has
    if_nametoindex), then sysfs. A subclass, not a patch.
    """
    from RNS.Interfaces.AutoInterface import AutoInterface

    class FireFlyAutoInterface(AutoInterface):
        def interface_name_to_index(self, ifname):
            return _interface_index(ifname)

    return FireFlyAutoInterface


_libc_if_nametoindex = None


def _interface_index(ifname):
    """Interface index for ifname, with or without socket.if_nametoindex."""
    if hasattr(socket, "if_nametoindex"):
        return socket.if_nametoindex(ifname)
    global _libc_if_nametoindex
    try:
        if _libc_if_nametoindex is None:
            import ctypes, ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so", use_errno=True)
            fn = libc.if_nametoindex
            fn.argtypes, fn.restype = [ctypes.c_char_p], ctypes.c_uint
            _libc_if_nametoindex = fn
        index = _libc_if_nametoindex(ifname.encode())
        if index:
            return int(index)
    except (OSError, AttributeError):
        pass
    try:
        with open(f"/sys/class/net/{ifname}/ifindex") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        raise OSError(f"no interface index for {ifname}")


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

    ANNOUNCE_WINDOW_S = 10 * 60        # a Stump announces every 2 min (Core.MESH_ALIVE_S)

    def __init__(self, core):
        self.core = core
        self.seen = stump.Recent()

    def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash=None,
                          is_path_response=False):
        # Heard only if it came from its sender now: a relay's remembered copy (the answer to a
        # path request, days later) doesn't make an offline Stump look online.
        fresh = stump.fresh_announce(self.seen, destination_hash,
                                     stump.announce_blob(destination_hash, announce_packet_hash),
                                     is_path_response, self.ANNOUNCE_WINDOW_S)
        self.core._on_delivery_announce(destination_hash, announced_identity, app_data, fresh=fresh)


class _PropagationAnnounces:
    aspect_filter = "lxmf.propagation"
    receive_path_responses = True

    PN_WINDOW_S = 30 * 60

    def __init__(self, core):
        self.core = core
        self.seen = stump.Recent()

    def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash=None,
                          is_path_response=False):
        fresh = stump.fresh_announce(self.seen, destination_hash,
                                     stump.announce_blob(destination_hash, announce_packet_hash),
                                     is_path_response, self.PN_WINDOW_S)
        self.core._on_propagation_announce(destination_hash, app_data, announced_identity, fresh=fresh)
