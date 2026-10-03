"""The RNode on Android: Reticulum's own RNodeInterface, over USB or Bluetooth LE.

Why this file exists
--------------------
Reticulum ships two RNode drivers. The desktop one (RNS/Interfaces/RNodeInterface.py)
is the reference: detection, firmware check, radio configuration and its
verification, RSSI/SNR/channel-load reporting, flow control. The Android one
is written for Kivy (python-for-android) and needs `usb4a`, `usbserial4a`,
`jnius` and `able`, none of which exist under Chaquopy.

So instead of re-implementing the RNode protocol in Kotlin and feeding
Reticulum raw KISS over a loopback socket (the approach of the prototype),
FireFly keeps the desktop driver byte for byte and gives it a different
"serial port": a `RadioLink` object implemented in Kotlin (UsbRadioLink or
BleRadioLink). The driver only ever needs five things from a port:
`is_open`, `in_waiting`, `read(n)`, `write(data)` and `close()`.

The one compatibility shim
--------------------------
The desktop driver's constructor refuses to run when Reticulum detects
Android. `allow_desktop_rnode_driver()` makes exactly that one check answer
"not Android" when, and only when, it is asked from inside
RNodeInterface.__init__. Every other caller in Reticulum still gets the true
answer, so AutoInterface and friends keep their Android behaviour.
This is the only patch FireFly applies to Reticulum.
"""
import sys
import threading
import time

import RNS
import RNS.vendor.platformutils as platformutils
from RNS.Interfaces.RNodeInterface import KISS, RNodeInterface

USB_PREFIX, BLE_PREFIX, TCP_PREFIX = "usb:", "ble:", "tcp://"
READ_CHUNK = 4096


def allow_desktop_rnode_driver():
    real = platformutils.is_android
    if getattr(real, "_firefly_guard", False):
        return
    guarded = RNodeInterface.__init__.__code__

    def is_android():
        caller = sys._getframe(1).f_code
        if caller is guarded:
            return False
        return real()

    is_android._firefly_guard = True
    platformutils.is_android = is_android


class LinkPort:
    """The part of pyserial RNodeInterface uses, backed by a RadioLink.

    RadioLink (Kotlin, or a test double in Python) contract:
        isOpen() -> bool
        read(max_bytes) -> byte[] (empty when nothing is waiting; never blocks)
        write(bytes) -> int (bytes accepted)
        close()
    """

    def __init__(self, link):
        self.link = link
        self.buf = bytearray()
        self.lock = threading.Lock()
        self._closed = False

    @property
    def is_open(self):
        if self._closed:
            return False
        try:
            return bool(self.link.isOpen())
        except Exception:
            return False

    def _pull(self):
        try:
            chunk = self.link.read(READ_CHUNK)
        except Exception:
            self._closed = True
            return
        if chunk is not None and len(chunk):
            self.buf += bytes(chunk)

    @property
    def in_waiting(self):
        with self.lock:
            if not self.buf:
                self._pull()
            return len(self.buf)

    def read(self, n=1):
        with self.lock:
            if len(self.buf) < n:
                self._pull()
            out = bytes(self.buf[:n])
            del self.buf[:n]
            return out

    def write(self, data):
        if self._closed:
            raise IOError("radio link is closed")
        return int(self.link.write(bytes(data)))

    def close(self):
        # Idempotent: the driver's read thread closes its port again on exit,
        # possibly after a new link to the same radio has been opened.
        if self._closed:
            return
        self._closed = True
        try:
            self.link.close()
        except Exception:
            pass


class FireFlyRNode(RNodeInterface):
    """RNodeInterface on a RadioLink, reporting old firmware instead of shutting down."""
    firmware_too_old = False

    def __init__(self, owner, configuration, links):
        # Set before super().__init__(), which opens the port from inside.
        self._links = links
        self._link_port = None
        allow_desktop_rnode_driver()
        super().__init__(owner, configuration)

    @property
    def transport_kind(self):
        p = self.port or ""
        if p.startswith(BLE_PREFIX): return "ble"
        if p.startswith(USB_PREFIX): return "usb"
        return "tcp" if self.use_tcp else "serial"

    def open_port(self):
        port = self.port or ""
        if not (port.startswith(USB_PREFIX) or port.startswith(BLE_PREFIX)):
            return super().open_port()          # tcp:// Wi-Fi RNodes: upstream code as-is
        if self.detached:
            raise IOError("interface detached")
        self._close_link()
        is_ble = port.startswith(BLE_PREFIX)
        RNS.log(f"Opening {'Bluetooth LE' if is_ble else 'USB'} link {port} for {self}")
        link = self._links.open(port, 12000 if is_ble else 4000)
        if link is None:
            raise IOError(f"could not open {port}")
        self._link_port = LinkPort(link)
        self.serial = self._link_port
        if is_ble:
            # The same allowances upstream makes for its own BLE transport:
            # longer command timeouts, and a wait for detection. self.ble stays
            # None: that code path is upstream's bleak transport, not ours.
            self.use_ble = True
            self.timeout = 1250

    def _close_link(self):
        p, self._link_port = self._link_port, None
        if p is not None:
            p.close()

    def detach(self):
        if self._link_port is None:
            return super().detach()
        self.detached = True
        for step in (self.disable_external_framebuffer,
                     lambda: self.setRadioState(KISS.RADIO_STATE_OFF),
                     self.leave):
            try:
                step()
            except Exception:
                pass
        time.sleep(0.3)                         # let a BLE write queue drain
        self._close_link()

    def validate_firmware(self):
        ok = (self.maj_version > self.REQUIRED_FW_VER_MAJ or
              (self.maj_version == self.REQUIRED_FW_VER_MAJ and self.min_version >= self.REQUIRED_FW_VER_MIN))
        self.firmware_ok = ok
        self.firmware_too_old = not ok
