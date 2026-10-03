"""Radio manager: finds and runs an RNode at runtime, outside start-up.

Reticulum starts without any radio. This manager, in its own thread, looks
for an RNode on every USB serial device Android has given us permission for,
on every Bluetooth LE RNode paired in the app, and on any Wi-Fi RNodes listed
in the settings. It attaches the first one that answers, retunes it live when
the radio settings change, and searches again if it disappears (cable pulled,
out of Bluetooth range, RNode switched off).

Same design as FireFly on the handheld; only the port list is Android's.
"""
import json
import threading
import time

import RNS
from RNS.Interfaces.RNodeInterface import KISS

from .androidlink import BLE_PREFIX, TCP_PREFIX, USB_PREFIX, FireFlyRNode

APPLY_DELAY_S = 1.5
IDLE_POLL_S = 3.0
RETRY_NOT_RNODE_S = 30.0
RETRY_BLE_S = 20.0          # a paired RNode that's off or out of range: keep looking, gently
RETRY_REFUSED_S = 60.0

PLATFORMS = {KISS.PLATFORM_ESP32: "ESP32", KISS.PLATFORM_NRF52: "nRF52", KISS.PLATFORM_AVR: "AVR"}


class RadioStatus:
    OFF, SEARCHING, CONNECTING, ONLINE, REFUSED, NO_DEVICES = (
        "off", "searching", "connecting", "online", "refused", "no devices")

    def __init__(self):
        self.state = self.OFF
        self.port = None
        self.detail = ""
        self.board = None
        self.since = time.time()

    def set(self, state, port=None, detail="", board=None):
        self.state, self.port, self.detail, self.board = state, port, detail, board
        self.since = time.time()


class RadioManager:
    def __init__(self, core, links, on_change=None):
        self.core = core
        self.links = links                 # Kotlin RadioLinks (or a test double)
        self.on_change = on_change or (lambda: None)
        self.status = RadioStatus()
        self.iface = None
        self.active_params = None
        self.backoff = {}
        self.refusals = {}
        self.apply_at = 0.0
        self.generation = 0                # bumped by every user request; stale backoffs are dropped
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.running = False

    # ------------------------------------------------ public
    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True, name="firefly-radio").start()

    def stop(self):
        self.running = False
        self.wake.set()
        self._detach()

    def request_apply(self, delay=APPLY_DELAY_S):
        self.generation += 1
        self.apply_at = time.time() + delay
        self.backoff.clear()
        self.refusals.clear()
        self.wake.set()

    def search_now(self):
        self.generation += 1
        self.backoff.clear()
        self.refusals.clear()
        self.apply_at = 0.0
        self.wake.set()

    def snapshot(self):
        st = self.status
        out = {"state": st.state, "port": st.port, "detail": st.detail, "board": st.board,
               "kind": _kind(st.port), "since": st.since}
        i = self.iface
        if i is not None:
            out.update({
                "rssi": i.r_stat_rssi, "snr": i.r_stat_snr, "noise_floor": i.r_noise_floor,
                "channel_load": i.r_channel_load_short, "airtime": i.r_airtime_short,
                "battery": i.r_battery_percent if i.r_battery_state != 0 else None,
                "charging": i.r_battery_state in (0x02, 0x03),
                "frequency": i.r_frequency, "bandwidth": i.r_bandwidth, "sf": i.r_sf,
                "cr": i.r_cr, "txpower": i.r_txpower, "bitrate": i.bitrate,
                "rx": i.rxb, "tx": i.txb,
            })
        return out

    # ------------------------------------------------ loop
    def _run(self):
        while self.running:
            try:
                if time.time() >= self.apply_at:
                    before = (self.status.state, self.status.port, self.status.detail)
                    self._tick()
                    if before != (self.status.state, self.status.port, self.status.detail):
                        self.on_change()
            except Exception as e:           # the radio must never take the app down
                RNS.log(f"Radio manager error: {e}", RNS.LOG_ERROR)
            wait = IDLE_POLL_S
            if self.apply_at > time.time():
                wait = max(0.1, self.apply_at - time.time())
            self.wake.wait(wait)
            self.wake.clear()

    def _params(self):
        r = self.core.settings["radio"]
        return (int(r["frequency"]), int(r["bandwidth"]), int(r["tx_power"]),
                int(r["spreading_factor"]), int(r["coding_rate"]))

    def _usb_ports(self):
        try:
            return [p["id"] for p in json.loads(str(self.links.usbPortsJson()))]
        except Exception as e:
            RNS.log(f"Could not list USB devices: {e}", RNS.LOG_WARNING)
            return []

    def _tick(self):
        s = self.core.settings
        if not s["radio"]["enabled"]:
            if self.iface:
                self._detach()
            if self.status.state != RadioStatus.OFF:
                self.status.set(RadioStatus.OFF)
            return

        params = self._params()
        usb = self._usb_ports()
        if self.iface:
            port = self.status.port
            vanished = port.startswith(USB_PREFIX) and port not in usb
            unwanted = port not in self._candidates(usb)     # unpaired, or removed from settings
            if unwanted:
                self._detach()
                self.status.set(RadioStatus.SEARCHING, detail="radio removed; searching")
            elif vanished or not self.iface.online:
                self._detach()
                self.status.set(RadioStatus.SEARCHING, detail=f"lost the RNode on {port}; searching")
                self.backoff.pop(port, None)
            elif params != self.active_params:
                self.status.set(RadioStatus.CONNECTING, port=port, detail="retuning")
                self._detach()
                self._try(port, params)
                if self.iface:
                    return
            else:
                return

        now = time.time()
        candidates = self._candidates(usb)
        if not candidates:
            self.status.set(RadioStatus.NO_DEVICES,
                            detail="plug an RNode into USB, or pair a Bluetooth RNode")
            return
        for port in candidates:
            if self.backoff.get(port, 0) > now:
                continue
            if self._try(port, params):
                return
        refused = [(p, self.refusals[p]) for p in candidates if p in self.refusals]
        if refused:
            port, (detail, board) = refused[0]
            self.status.set(RadioStatus.REFUSED, port=port, board=board, detail=detail)
        else:
            self.status.set(RadioStatus.SEARCHING,
                            detail=f"{len(candidates)} device(s) tried, none answered as an RNode; retrying")

    def _candidates(self, usb):
        r = self.core.settings["radio"]
        preferred = r.get("port") or "auto"
        ble = [BLE_PREFIX + d["address"] for d in r.get("ble_devices", [])]
        tcp = [TCP_PREFIX + h.strip() for h in self.core.settings.get("rnode_hosts", []) if h.strip()]
        ports = []
        if preferred != "auto" and (preferred in usb or preferred in ble or preferred in tcp):
            ports.append(preferred)
        # A cable is an explicit act: USB first, then Bluetooth, then Wi-Fi.
        for p in usb + ble + tcp:
            if p not in ports:
                ports.append(p)
        return ports

    def _try(self, port, params):
        gen = self.generation
        freq, bw, txp, sf, cr = params
        self.status.set(RadioStatus.CONNECTING, port=port,
                        detail="connecting over Bluetooth" if port.startswith(BLE_PREFIX)
                        else "asking the device if it is an RNode")
        self.on_change()
        cfg = {"name": _iface_name(port), "port": port, "frequency": freq, "bandwidth": bw, "txpower": txp,
               "spreadingfactor": sf, "codingrate": cr}
        iface = None
        try:
            iface = FireFlyRNode(RNS.Transport, cfg, self.links)
        except Exception as e:
            RNS.log(f"RNode on {port} could not start: {e}", RNS.LOG_WARNING)
        board = _board(iface) if iface else None
        too_old = iface is not None and iface.detected and iface.firmware_too_old

        if iface is not None and iface.online and not too_old:
            try:
                self.core.reticulum._add_interface(iface)
            except Exception as e:
                RNS.log(f"Could not register RNode interface: {e}", RNS.LOG_ERROR)
                _quiet_detach(iface)
                return False
            with self.lock:
                self.iface = iface
                self.active_params = params
            self.backoff.pop(port, None)
            self.refusals.pop(port, None)
            self.status.set(RadioStatus.ONLINE, port=port, board=board)
            RNS.log(f"RNode online on {port} ({board})")
            self.core.on_radio_online()
            return True

        if gen != self.generation:
            # The user asked again while this attempt was running (plugged a
            # board in, paired one, changed settings): don't let this attempt's
            # failure delay the next one.
            if iface is not None:
                _quiet_detach(iface)
            self.wake.set()
            return False
        if iface is not None and iface.detected:
            if iface.firmware_too_old:
                why = (f"firmware {iface.maj_version}.{iface.min_version} is too old "
                       f"(needs {iface.REQUIRED_FW_VER_MAJ}.{iface.REQUIRED_FW_VER_MIN}+): update it with rnodeconf")
            else:
                why = ("it refused these radio settings. Check the frequency is in this board's band, "
                       "and the TX power is within its limit")
            self.status.set(RadioStatus.REFUSED, port=port, board=board, detail=why)
            self.refusals[port] = (why, board)
            self.backoff[port] = time.time() + RETRY_REFUSED_S
        else:
            self.refusals.pop(port, None)
            wait = RETRY_BLE_S if port.startswith(BLE_PREFIX) else RETRY_NOT_RNODE_S
            self.backoff[port] = time.time() + wait
        if iface is not None:
            _quiet_detach(iface)
        return False

    def _detach(self):
        with self.lock:
            iface, self.iface, self.active_params = self.iface, None, None
        if iface is None:
            return
        try:
            RNS.Transport.remove_interface(iface)
        except Exception:
            pass
        _quiet_detach(iface)


def _kind(port):
    if not port: return None
    if port.startswith(USB_PREFIX): return "usb"
    if port.startswith(BLE_PREFIX): return "ble"
    if port.startswith(TCP_PREFIX): return "wifi"
    return "serial"


def _iface_name(port):
    return {"usb": "RNode USB", "ble": "RNode Bluetooth", "wifi": "RNode Wi-Fi"}.get(_kind(port), "RNode")


def _board(iface):
    parts = [PLATFORMS.get(iface.platform, "unknown chip")]
    if iface.maj_version:
        parts.append(f"firmware {iface.maj_version}.{iface.min_version}")
    return ", ".join(parts)


def _quiet_detach(iface):
    try:
        iface.detached = True        # stops Reticulum's own reconnect loop
        iface.online = False
        if iface.serial is not None and getattr(iface.serial, "is_open", False):
            iface.detach()
        if iface.serial is not None:
            iface.serial.close()
    except Exception:
        pass
