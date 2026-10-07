"""A simulated RNode behind the RadioLink contract the Kotlin side implements.

    link = FakeRNodeLink(style="ble")     # or "usb"
    link.write(b"...")                    # what Reticulum's driver sends
    link.read(4096)                       # what the RNode answers

style="ble" behaves like Android BLE: notifications arrive in MTU-sized
pieces with a little latency, and writes are accepted immediately and
drained in chunks. With `air=(listen_port, peer_port)` two fake RNodes pass
LoRa frames to each other over UDP on localhost, so two FireFly engines in
two processes can exchange real LXMF messages through the real RNode driver.
"""
import socket
import threading
import time

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
CMD = dict(DATA=0x00, FREQ=0x01, BW=0x02, TXP=0x03, SF=0x04, CR=0x05, STATE=0x06, DETECT=0x08,
           LEAVE=0x0A, ST_ALOCK=0x0B, LT_ALOCK=0x0C, RSSI=0x23, SNR=0x24, PLATFORM=0x48, MCU=0x49, FW=0x50)


def esc(data):
    out = bytearray()
    for b in data:
        if b == FEND: out += bytes([FESC, TFEND])
        elif b == FESC: out += bytes([FESC, TFESC])
        else: out.append(b)
    return bytes(out)


class FakeRNodeLink:
    def __init__(self, style="usb", band=(137e6, 1020e6), fw=(1, 82), answers=True, max_txp=22,
                 air=None, mtu=180):
        self.style, self.band, self.fw, self.answers, self.max_txp = style, band, fw, answers, max_txp
        self.freq = int(band[0]); self.bw = 125000; self.txp = 0; self.sf = 7; self.cr = 5; self.state = 0
        self.mtu = mtu
        self.rx = bytearray()          # device -> host
        self.lock = threading.Lock()
        self.open_ = True              # the device is present (plugged in / in range)
        self.session = True            # a host connection is open on it
        self.set_log = []
        self.frames_on_air = 0
        self._inbuf, self._in_frame, self._escape = bytearray(), False, False
        self.air = None
        if air:
            listen, peer = air
            self.air = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.air.bind(("127.0.0.1", listen))
            self.air_peer = ("127.0.0.1", peer)
            threading.Thread(target=self._air_loop, daemon=True).start()

    # ---------------------------------------------------------------- RadioLink contract
    def isOpen(self):
        return self.open_ and self.session

    def reopen(self):
        self.session = True
        with self.lock:
            self.rx.clear()
        self._inbuf, self._in_frame, self._escape = bytearray(), False, False

    def read(self, n):
        if not self.isOpen():
            raise IOError("device gone")
        with self.lock:
            if self.style == "ble":
                n = min(n, self.mtu)   # BLE notifications arrive in MTU-sized pieces
            out = bytes(self.rx[:n])
            del self.rx[:n]
            return out

    def write(self, data):
        if not self.isOpen():
            raise IOError("device gone")
        data = bytes(data)
        if self.style == "ble":
            threading.Thread(target=self._ble_drain, args=(data,), daemon=True).start()
        else:
            self._feed(data)
        return len(data)

    def close(self):
        self.session = False

    # ---------------------------------------------------------------- helpers
    def unplug(self):
        self.open_ = False

    def _ble_drain(self, data):
        for i in range(0, len(data), self.mtu):
            time.sleep(0.004)
            self._feed(data[i:i + self.mtu])

    def _send(self, cmd, payload):
        frame = bytes([FEND, cmd]) + esc(payload) + bytes([FEND])
        if self.style == "ble":
            time.sleep(0.01)
        with self.lock:
            self.rx += frame

    def _feed(self, chunk):
        for b in chunk:
            if b == FEND:
                if self._in_frame and self._inbuf:
                    self._handle(self._inbuf[0], bytes(self._inbuf[1:]))
                self._inbuf, self._in_frame = bytearray(), True
            elif self._in_frame:
                if b == FESC:
                    self._escape = True
                    continue
                if self._escape:
                    b = FEND if b == TFEND else FESC if b == TFESC else b
                    self._escape = False
                self._inbuf.append(b)

    def _handle(self, cmd, data):
        if not self.answers:
            return
        if cmd == CMD["DETECT"] and data[:1] == b"\x73":
            self._send(CMD["DETECT"], b"\x46")
        elif cmd == CMD["FW"]:
            self._send(CMD["FW"], bytes(self.fw))
        elif cmd == CMD["PLATFORM"]:
            self._send(CMD["PLATFORM"], b"\x80")
        elif cmd == CMD["MCU"]:
            self._send(CMD["MCU"], b"\x81")
        elif cmd == CMD["FREQ"] and len(data) == 4:
            f = int.from_bytes(data, "big")
            if self.band[0] <= f <= self.band[1]:
                self.freq = f
            self.set_log.append(("freq", f))
            self._send(CMD["FREQ"], self.freq.to_bytes(4, "big"))
        elif cmd == CMD["BW"] and len(data) == 4:
            self.bw = int.from_bytes(data, "big"); self._send(CMD["BW"], data)
        elif cmd == CMD["TXP"] and len(data) == 1:
            self.txp = min(data[0], self.max_txp); self._send(CMD["TXP"], bytes([self.txp]))
        elif cmd == CMD["SF"] and len(data) == 1:
            self.sf = data[0]; self._send(CMD["SF"], data)
        elif cmd == CMD["CR"] and len(data) == 1:
            self.cr = data[0]; self._send(CMD["CR"], data)
        elif cmd == CMD["STATE"] and len(data) == 1:
            self.state = data[0]; self._send(CMD["STATE"], data)
        elif cmd == CMD["DATA"] and self.air is not None and self.state == 1:
            self.frames_on_air += 1
            self.air.sendto(data, self.air_peer)

    def _air_loop(self):
        while True:
            try:
                data, _ = self.air.recvfrom(1024)
            except OSError:
                return
            if self.isOpen() and self.state == 1:
                self._send(CMD["RSSI"], bytes([157 - 72]))     # -72 dBm
                self._send(CMD["SNR"], bytes([int(9.5 * 4)]))  # 9.5 dB
                self._send(CMD["DATA"], data)


class Session:
    """One open() of a device, like one Kotlin RadioLink object. close() ends only this one."""

    def __init__(self, dev):
        self.dev, self.closed = dev, False

    def isOpen(self): return not self.closed and self.dev.isOpen()
    def read(self, n):
        if self.closed: raise IOError("closed")
        return self.dev.read(n)
    def write(self, d):
        if self.closed: raise IOError("closed")
        return self.dev.write(d)
    def close(self):
        if not self.closed:
            self.closed = True
            self.dev.close()


class FakeLinks:
    """The RadioLinks registry the Kotlin side implements."""

    def __init__(self):
        self.usb = {}      # port id -> link
        self.ble = {}      # address -> link (None = paired but out of range)
        self.opened = []

    def usbPortsJson(self):
        import json
        return json.dumps([{"id": p, "label": "Fake USB RNode"} for p, l in self.usb.items() if l.open_])

    def open(self, port, timeout_ms):
        self.opened.append(port)
        if port.startswith("usb:"):
            link = self.usb.get(port)
        elif port.startswith("ble:"):
            link = self.ble.get(port[4:])
            if link is not None:
                time.sleep(0.3)        # connect + service discovery + MTU exchange
        else:
            link = None
        if link is None or not link.open_:
            return None
        link.reopen()
        return Session(link)
