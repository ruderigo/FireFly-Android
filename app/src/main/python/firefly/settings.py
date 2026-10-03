"""Where FireFly keeps its data, and the user's settings (JSON).

Everything lives under the app's private files directory, which only this
app can read. Settings are written atomically: never half a file.
"""
import copy
import json
import os
import tempfile
from dataclasses import dataclass

# The provisioner defaults of the Stump network. Radio settings MUST match the
# other nodes exactly: a mismatched radio doesn't error, it just hears nothing.
STUMP_RADIO = {
    "frequency": 915_000_000,
    "bandwidth": 125_000,
    "spreading_factor": 8,
    "coding_rate": 5,
    "tx_power": 7,
}

BANDWIDTHS = [7_800, 10_400, 15_600, 20_800, 31_250, 41_700, 62_500, 125_000, 250_000, 500_000]

DEFAULTS = {
    "display_name": "FireFly",
    "announce_interval_min": 30,     # Stump needs < 60 to keep you DM-reachable
    "radio": dict(
        enabled=True,
        port="auto",                 # auto | usb:<device> | ble:<address> | tcp://<host>
        ble_devices=[],              # [{"address": "AA:BB:..", "name": "RNode 1A2B"}], paired in the app
        keep_awake=True,             # hold a partial wake lock while a radio is attached
        **STUMP_RADIO,
    ),
    "auto_interface": True,          # zero-conf discovery on Wi-Fi
    "tcp_peers": [],                 # ["host:port", ...] internet or LAN entrypoints
    "rnode_hosts": [],               # Wi-Fi RNodes by host/IP
    "transport": False,              # route traffic for others (costs airtime + battery)
    "propagation_mode": "auto",      # auto | manual | off
    "propagation_node": None,        # hex hash when manual
    "fallback_to_propagation": True,
    "sync_interval_min": 60,         # 0 = manual only
    "stump_wifi_default": "http://192.168.4.1",
    "log_level": 3,
}


@dataclass
class Paths:
    home: str

    @property
    def settings(self): return os.path.join(self.home, "settings.json")
    @property
    def identity(self): return os.path.join(self.home, "identity")
    @property
    def database(self): return os.path.join(self.home, "firefly.db")
    @property
    def rns_config_dir(self): return os.path.join(self.home, "reticulum")
    @property
    def lxmf_storage(self): return os.path.join(self.home, "lxmf")

    def ensure(self):
        for d in (self.home, self.rns_config_dir, self.lxmf_storage):
            os.makedirs(d, exist_ok=True)


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self, path):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = _merge(DEFAULTS, json.load(f))
        except FileNotFoundError:
            self.save()
        except (ValueError, OSError):
            try:
                os.replace(self.path, self.path + ".broken")
            except OSError:
                pass
            self.data = copy.deepcopy(DEFAULTS)
            self.save()
        try:
            self.validate()
        except (TypeError, ValueError, KeyError):
            self.data["radio"] = copy.deepcopy(DEFAULTS["radio"])
            self.validate()
            self.save()

    def validate(self):
        r = self.data["radio"]
        r["frequency"] = int(min(max(int(r["frequency"]), 137_000_000), 1_020_000_000))
        if int(r["bandwidth"]) not in BANDWIDTHS:
            r["bandwidth"] = 125_000
        r["bandwidth"] = int(r["bandwidth"])
        r["spreading_factor"] = int(min(max(int(r["spreading_factor"]), 5), 12))
        r["coding_rate"] = int(min(max(int(r["coding_rate"]), 5), 8))
        r["tx_power"] = int(min(max(int(r["tx_power"]), 0), 22))
        r["ble_devices"] = [d for d in r.get("ble_devices", []) if isinstance(d, dict) and d.get("address")]
        self.data["announce_interval_min"] = max(0, int(self.data["announce_interval_min"]))
        self.data["sync_interval_min"] = max(0, int(self.data["sync_interval_min"]))
        if self.data["propagation_mode"] not in ("auto", "manual", "off"):
            self.data["propagation_mode"] = "auto"
        name = str(self.data.get("display_name") or "").strip()
        self.data["display_name"] = name[:64] or "FireFly"

    def update(self, patch):
        """Merge a partial settings dict, validate, save. Returns the set of top-level keys changed."""
        before = copy.deepcopy(self.data)
        self.data = _merge(self.data, patch or {})
        self.validate()
        self.save()
        return {k for k in self.data if self.data.get(k) != before.get(k)}

    def save(self):
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def __getitem__(self, k): return self.data[k]
    def __setitem__(self, k, v): self.data[k] = v
    def get(self, k, default=None): return self.data.get(k, default)
