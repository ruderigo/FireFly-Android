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
        # A request the person made (Search now, Use) is being carried out: from the press until
        # a whole pass of the radio loop that started after it has finished. For the screen:
        # the buttons wait, and the LoRa light flashes.
        self.busy = False
        self.confirmed = None              # what the RNode itself reported when it came online
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
        self.busy = True
        self.generation += 1
        self.backoff.clear()
        self.refusals.clear()
        self.apply_at = 0.0
        self.wake.set()

    def snapshot(self):
        st = self.status
        out = {"state": st.state, "port": st.port, "detail": st.detail, "board": st.board,
               "kind": _kind(st.port), "since": st.since, "device": self.device_name(st.port),
               "busy": bool(self.busy)}
        i = self.iface
        # Confirmed: the RNode's own report of its frequency, now, equals what it was set to.
        conf, active = self.confirmed, self.active_params
        out["confirmed"] = bool(i is not None and conf and active and i.r_frequency is not None
                                and abs(int(i.r_frequency) - active[0]) <= 100)
        out["confirmed_at"] = conf["at"] if conf else None
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
                    before = (self.status.state, self.status.port, self.status.detail, self.busy)
                    gen = self.generation
                    self._tick()
                    if self.busy and self.generation == gen:
                        self.busy = False           # the person's request has been carried out
                    if before != (self.status.state, self.status.port, self.status.detail, self.busy):
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

    def device_name(self, port):
        """Which RNode this is, as people know it: "RNode EA69" (its Bluetooth name, saved
        when it was paired), the USB device's name, or the host of a Wi-Fi RNode."""
        if not port:
            return None
        if port.startswith(BLE_PREFIX):
            addr = port[len(BLE_PREFIX):].upper()
            for d in self.core.settings["radio"].get("ble_devices", []):
                if d.get("address", "").upper() == addr:
                    return d.get("name") or addr
            return addr
        if port.startswith(USB_PREFIX):
            return getattr(self, "_usb_labels", {}).get(port) or "USB"
        if port.startswith(TCP_PREFIX):
            return port[len(TCP_PREFIX):]
        return port

    def use(self, port):
        """Switch to this RNode now (it's also first from then on): let go of the one in use
        and connect to it; if it doesn't answer, the others are tried as usual."""
        self.core.settings.update({"radio": {"port": port, "enabled": True}})
        self.switch_to = port               # done by the radio's own loop (_tick), not from here
        self.search_now()

    def _usb_ports(self):
        try:
            ports = json.loads(str(self.links.usbPortsJson()))
            self._usb_labels = {p["id"]: p.get("label") for p in ports}
            return [p["id"] for p in ports]
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
        switch, self.switch_to = getattr(self, "switch_to", None), None
        if self.iface and switch and self.status.port != switch:
            self._detach()                  # "Use" on another RNode: let this one go
            self.status.set(RadioStatus.SEARCHING, detail=f"switching to {self.device_name(switch)}")
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
            # Reticulum's driver calls the radio set up without the RNode confirming the
            # frequency (it skips that check while no report has come back). Here it must:
            # the RNode's own report of each setting, equal to what we asked, or no "online".
            problem = self._confirm(iface, params)
            if problem:
                RNS.log(f"RNode on {port} not confirmed: {problem}", RNS.LOG_ERROR)
                self.status.set(RadioStatus.REFUSED, port=port, board=board, detail=problem)
                self.refusals[port] = (problem, board)
                self.backoff[port] = time.time() + RETRY_REFUSED_S
                _quiet_detach(iface)
                return False
            try:
                self.core.reticulum._add_interface(iface)
            except Exception as e:
                RNS.log(f"Could not register RNode interface: {e}", RNS.LOG_ERROR)
                _quiet_detach(iface)
                return False
            with self.lock:
                self.iface = iface
                self.active_params = params
                self.confirmed = {"frequency": int(iface.r_frequency), "bandwidth": int(iface.r_bandwidth),
                                  "sf": int(iface.r_sf), "txpower": int(iface.r_txpower), "at": time.time()}
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
                # What the RNode itself reports, when that's what's wrong (it stayed on another
                # frequency): said as such. Otherwise the settings were out of its range.
                rf, freq = iface.r_frequency, params[0]
                if rf is not None and abs(int(rf) - freq) > 100:
                    why = (f"the RNode reports {int(rf) / 1e6:.3f} MHz, not the {freq / 1e6:.3f} MHz asked: "
                           "it didn't change. Check the frequency is in this board's band, or restart the RNode")
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

    CONFIRM_WAIT_S = {"ble": 4.0, "usb": 2.0}

    def _confirm(self, iface, params):
        """Wait for the RNode to report back the settings it now runs with, and compare them
        with what we asked. -> None when they match, else why not (in words)."""
        freq, bw, txp, sf, cr = params
        wait = self.CONFIRM_WAIT_S["ble" if (iface.port or "").startswith(BLE_PREFIX) else "usb"]
        asked_again = False
        end = time.time() + wait
        while True:
            rf = iface.r_frequency
            if rf is not None and abs(int(rf) - freq) <= 100 and iface.r_bandwidth == bw and iface.r_sf == sf:
                break
            if time.time() >= end:
                if rf is None and not asked_again:
                    # No report yet: set it once more (the RNode answers a setting with its value).
                    asked_again = True
                    try:
                        iface.setFrequency()
                    except Exception:
                        pass
                    end = time.time() + wait
                    continue
                break
            time.sleep(0.1)
        rf = iface.r_frequency
        if rf is None:
            return (f"the RNode never confirmed the frequency ({freq / 1e6:.3f} MHz was asked): "
                    "it may not have changed. Search again, or restart the RNode")
        if abs(int(rf) - freq) > 100:
            return f"the RNode reports {int(rf) / 1e6:.3f} MHz, not the {freq / 1e6:.3f} MHz asked: it didn't change"
        if iface.r_bandwidth != bw:
            return f"the RNode reports {iface.r_bandwidth} Hz bandwidth, not {bw}"
        if iface.r_sf != sf:
            return f"the RNode reports SF{iface.r_sf}, not SF{sf}"
        return None

    def _detach(self):
        with self.lock:
            iface, self.iface, self.active_params = self.iface, None, None
            self.confirmed = None
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
