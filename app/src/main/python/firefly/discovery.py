"""Finding Stumps over Wi-Fi, and how good a link to each one is.

Where a phone can find a Stump (CLIENT_QUICKSTART.md, Addresses and Chained
Stumps):

  * here     the gateway of the Wi-Fi it joined: 192.168.4.1 on a lone Stump,
             192.168.<n>.1 along a chain.
  * nearby   other Stumps' hotspots, from a Wi-Fi scan (done by the app);
             once one is joined, it's "here".
  * LAN      a Stump that joined the same router as the phone: found by
             asking every address of the local /24.

Probing must not change anything on the node. /rrc/poll would make the
phone a chat user (everyone sees "✓ guest-…"), so it's never used here.
Requests, in order:

  GET /node.json    name, lxmf, version, release, ap_ip, chain (null or the
                    /chain.json object), features. After 1.0.0-beta.2; an
                    older node answers it with its home page (HTML), so
                    anything not JSON falls through to the next request.
  GET /chain.json   any 1.0.0-beta.2 node; {"enabled": …} marks a Stump
  GET /files.json, /billboard.json   older nodes, when they offer them

Since Stump 1.0.0-beta.21 a Stump also answers a question on the local
network (CLIENT_QUICKSTART, Finding a Stump on a Wi-Fi): a UDP datagram
starting "STUMP?" to port 4244, broadcast or to one address, gets one JSON
card per Stump: {"stump": 1, "name", "lxmf", "release", "http": [...],
"here"}. "lxmf" never changes, so a Stump is kept by it and found again
when the router gives it a new address. GET /stumps.json on any Stump lists
the others it heard, with their addresses. The /24 sweep stays for older
Stumps, and for networks where broadcasts don't get through.

How good a link is ("bars", 0-3):
  signal     the hotspot's RSSI from the scan
  steadiness a few small round trips: median time and how many were lost
  LoRa       the SNR of the last packet heard from the node, and its hops
"""
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from .stump_web import _attr

PROBE_TIMEOUT_MS = 1500
PORTS = (80, 8080)        # a Stump on a computer may serve on 8080 (CLIENT_QUICKSTART, Stumps on a computer)
SWEEP_TIMEOUT_MS = 700
SWEEP_WORKERS = 48
STEADY_SAMPLES = 5


def _get(http, url, timeout_ms):
    """-> (status, parsed JSON dict or None, elapsed ms)"""
    t = time.monotonic()
    try:
        r = http.request("GET", url, "{}", None, timeout_ms)
        status = int(_attr(r, "status"))
        raw = _attr(r, "body")
        body = bytes(raw) if raw is not None else b""
    except Exception:
        return -1, None, (time.monotonic() - t) * 1000
    ms = (time.monotonic() - t) * 1000
    data = None
    if status == 200 and body[:1] in (b"{", b"["):
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = None
    return status, data, ms


def _hash(v):
    return v.lower() if isinstance(v, str) and len(v) == 32 and all(c in "0123456789abcdefABCDEF" for c in v) else None


def _chain_summary(c):
    if not isinstance(c, dict) or not c.get("enabled"):
        return {"enabled": False, "role": None, "below": 0}
    below = c.get("below")
    return {"enabled": True, "role": c.get("role") if c.get("role") in ("root", "member") else None,
            "below": len(below) if isinstance(below, list) else 0}


def identify(http, base, timeout_ms=PROBE_TIMEOUT_MS):
    """Is there a Stump at `base`? -> dict or None. Changes nothing on the node."""
    status, data, ms = _get(http, base + "/node.json", timeout_ms)
    if status < 0:
        return None                                  # nothing answered: no need to ask twice
    if isinstance(data, dict) and any(k in data for k in ("name", "lxmf", "release", "ap_ip", "features")):
        # Any of its keys may be null (a node without a name, or with LoRa off): still /node.json.
        feats = data.get("features")
        return {"base": base, "via": "/node.json", "name": str(data.get("name") or "")[:64] or None,
                "version": str(data["version"])[:32] if data.get("version") else None,
                "release": str(data["release"])[:32] if data.get("release") else None,
                "lxmf": _hash(data.get("lxmf")), "ap_ip": data.get("ap_ip") if isinstance(data.get("ap_ip"), str) else None,
                "chain": _chain_summary(data.get("chain")),        # null unless chained, else the /chain.json object
                "features": [str(f) for f in feats][:12] if isinstance(feats, list) else None,
                "radio": data.get("radio") if isinstance(data.get("radio"), dict) else None,
                "first_ms": ms}
    status, data, ms2 = _get(http, base + "/chain.json", timeout_ms)
    if isinstance(data, dict) and "enabled" in data:
        return {"base": base, "via": "/chain.json", "name": None, "version": None, "release": None, "features": None, "lxmf": None,
                "ap_ip": data.get("ap_ip") if isinstance(data.get("ap_ip"), str) else None,
                "chain": _chain_summary(data), "first_ms": ms2}
    for path, key in (("/files.json", "files"), ("/billboard.json", "posts")):      # older nodes
        status, data, ms3 = _get(http, base + path, timeout_ms)
        if isinstance(data, dict) and key in data:
            return {"base": base, "via": path, "name": None, "version": None, "release": None, "features": None, "lxmf": None, "ap_ip": None,
                    "chain": _chain_summary(None), "first_ms": ms3}
    return None


def steadiness(http, url, samples=STEADY_SAMPLES, timeout_ms=PROBE_TIMEOUT_MS):
    """A few small GETs, one after the other. -> {"median_ms", "lost", "samples"}"""
    times, lost = [], 0
    for _ in range(samples):
        status, _, ms = _get(http, url, timeout_ms)
        if status == 200:
            times.append(ms)
        else:
            lost += 1
    return {"median_ms": round(statistics.median(times)) if times else None, "lost": lost, "samples": samples}


def _has_port(base):
    host = base.split("://", 1)[-1].split("/", 1)[0]
    return ":" in host


def probe(http, base, samples=STEADY_SAMPLES):
    """identify() plus steadiness, with bars. -> dict, {"stump": False} when nothing Stump-like answers.
    An address without a port is tried on 80, then on 8080 (a Stump on a computer)."""
    info = find(http, base)
    if not info:
        return {"base": base, "stump": False}
    st = steadiness(http, info["base"] + info["via"], samples)     # the port that answered
    info.update(st)
    info["stump"] = True
    info["steady_bars"] = steady_bars(st["median_ms"], st["lost"], st["samples"])
    return info


def find(http, base):
    """identify() on `base`, then on :8080 when no port was given. -> dict or None."""
    info = identify(http, base)
    if not info and not _has_port(base):
        info = identify(http, base.rstrip("/") + ":8080")
    return info


def why_not(http, base, timeout_ms=PROBE_TIMEOUT_MS):
    """Nothing Stump-like at `base`: what is there instead, in words."""
    from .stump_session import net_reason
    try:
        r = http.request("GET", base + "/node.json", "{}", None, timeout_ms)
        status, raw = int(_attr(r, "status")), _attr(r, "body")
    except Exception as e:
        return net_reason(str(e))
    if status < 0:
        return net_reason(bytes(raw) if raw is not None else b"")
    return f"something answers there (HTTP {status}), but it isn't a Stump: a router, or another device's web page"


# ------------------------------------------------------------------ bars
def signal_bars(rssi):
    """Wi-Fi RSSI (dBm) -> 0..3"""
    if rssi is None:
        return None
    return 3 if rssi >= -60 else 2 if rssi >= -72 else 1 if rssi >= -85 else 0


def steady_bars(median_ms, lost, samples):
    if not samples or median_ms is None:
        return 0
    loss = lost / samples
    if loss == 0 and median_ms < 150:
        return 3
    if loss <= 0.2 and median_ms < 400:
        return 2
    if loss < 0.6:
        return 1
    return 0


def lora_bars(snr):
    """SNR (dB) of the last packet heard -> 0..3. SF8 decodes down to about -10 dB."""
    if snr is None:
        return None
    return 3 if snr >= 5 else 2 if snr >= -3 else 1 if snr >= -10 else 0


def overall(*bars):
    """The weakest known part of the link decides."""
    known = [b for b in bars if b is not None]
    return min(known) if known else None


# ------------------------------------------------------------------ the LAN
def lan_hosts(own_ip, prefix):
    """The addresses to ask: the phone's own /24 (or its smaller subnet), minus itself."""
    try:
        parts = [int(x) for x in own_ip.split(".")]
        assert len(parts) == 4 and all(0 <= x <= 255 for x in parts)
    except Exception:
        return []
    prefix = max(24, int(prefix or 24))
    size = 1 << (32 - prefix)
    me = parts[3]
    start = (me // size) * size
    base = ".".join(map(str, parts[:3]))
    return [f"{base}.{h}" for h in range(start + 1, start + size - 1) if h != me]


def sweep(http, own_ip, prefix, skip=(), timeout_ms=SWEEP_TIMEOUT_MS, workers=SWEEP_WORKERS, samples=3):
    """Every Stump on the local network. -> list of probe() results, steadiest first."""
    hosts = [h for h in lan_hosts(own_ip, prefix) if h not in set(skip)]
    bases = [f"http://{h}" if p == 80 else f"http://{h}:{p}" for h in hosts for p in PORTS]

    def ask(base):
        try:
            return identify(http, base, timeout_ms)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = [r for r in pool.map(ask, bases) if r]
    out = []
    for info in found:
        st = steadiness(http, info["base"] + info["via"], samples)
        info.update(st)
        info["stump"] = True
        info["steady_bars"] = steady_bars(st["median_ms"], st["lost"], st["samples"])
        out.append(info)
    out.sort(key=lambda i: (-i["steady_bars"], i["median_ms"] or 1e9))
    return out


# ------------------------------------------------------------------ Stumps that are just SSIDs
DEFAULT_SSID = "LaBuche-Stump.web.app"        # captive_portal.DEFAULT_SSID


def looks_like_stump(ssid, known=()):
    """From a scan: a hotspot we joined as a Stump before, the firmware's default name,
    or a name that says so. Anything else is only "maybe"."""
    s = (ssid or "").strip()
    if not s:
        return "no"
    if s in set(known) or s == DEFAULT_SSID:
        return "yes"
    return "likely" if "stump" in s.lower() else "maybe"


# ------------------------------------------------------------------ STUMP? (1.0.0-beta.21)
UDP_PORT = 4244
QUERY = b"STUMP? v1"
ASK_WAIT_MS = 1500


def _base_of(url):
    from .stump_session import normalize_base
    return normalize_base(url) if isinstance(url, str) and url.startswith("http") else None


def card_from(data, sender=None):
    """One answer to STUMP? -> {"name", "lxmf", "release", "http", "here", "base"} or None."""
    try:
        d = json.loads(data) if isinstance(data, str) else data
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("stump") != 1:
        return None
    urls = [u for u in (d.get("http") or []) if isinstance(u, str) and u.startswith("http")][:8]
    here = d.get("here") if isinstance(d.get("here"), str) and d.get("here", "").startswith("http") else ""
    # Where to reach it, best first: the address it names on our network; one of its
    # addresses on the host it answered from; that host with its port (a computer
    # whose route address isn't on our network); then the rest of its list.
    cands = []
    def add(b):
        if b and b not in cands:
            cands.append(b)
    add(_base_of(here))
    if sender:
        add(next((_base_of(u) for u in urls if "//" + sender + "/" in u or "//" + sender + ":" in u), None))
        if urls:
            host = urls[0].split("://", 1)[-1].split("/", 1)[0]
            port = host.rsplit(":", 1)[1] if ":" in host else ""
            add(_base_of("http://" + sender + (":" + port if port else "")))
    for u in urls:
        add(_base_of(u))
    if sender:
        add(_base_of("http://" + sender))         # it answered but named no address
    base = cands[0] if cands else None
    return {"name": str(d.get("name") or "")[:64] or None, "lxmf": _hash(d.get("lxmf")),
            "release": str(d.get("release") or "")[:32] or None, "http": urls, "here": here or None,
            "base": base, "candidates": cands, "from": sender}


def _reachable(http, card):
    """The first of a card's candidate addresses that answers as this Stump."""
    for b in card["candidates"][:4]:
        info = identify(http, b)
        if info and (not card["lxmf"] or info.get("lxmf") in (None, card["lxmf"])):
            return info["base"]
    return None


def ask(http, targets=("broadcast",), wait_ms=ASK_WAIT_MS):
    """Who's a Stump here? -> [card], one per Stump (by lxmf, else by address).
    "broadcast" asks the whole Wi-Fi (255.255.255.255 and the subnet's); an
    address asks just that one. Needs the app's udpAsk (Kotlin: bound to the
    Wi-Fi, with a MulticastLock); nothing comes back without it."""
    fn = getattr(http, "udpAsk", None)
    if fn is None:
        return []
    try:
        raw = fn(json.dumps(list(targets)), UDP_PORT, QUERY, int(wait_ms))
        answers = json.loads(str(raw) or "[]")
    except Exception:
        return []
    out, seen = [], set()
    for a in answers:
        c = card_from(a.get("data"), a.get("from"))
        if not c or not c["base"]:
            continue
        if not c["here"] and len(c["candidates"]) > 1:
            # None answering (a busy moment): its own first address, not a guess of ours.
            c["base"] = _reachable(http, c) or (_base_of(c["http"][0]) if c["http"] else c["base"])
        k = c["lxmf"] or c["base"]
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def directory(http, base, timeout_ms=PROBE_TIMEOUT_MS):
    """GET /stumps.json: the other Stumps this one heard -> [{"name", "lxmf", "http"}]."""
    status, data, _ = _get(http, base + "/stumps.json", timeout_ms)
    if not isinstance(data, dict) or not isinstance(data.get("stumps"), list):
        return []
    return [{"name": str(s.get("name") or "")[:64] or None, "lxmf": _hash(s.get("lxmf")),
             "http": [u for u in (s.get("http") or []) if isinstance(u, str) and u.startswith("http")][:6]}
            for s in data["stumps"] if isinstance(s, dict)]


def _same_net(url, own_ip):
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return bool(own_ip) and host.split(".")[:3] == own_ip.split(".")[:3]


def find_all(http, own_ip=None, prefix=24, sweep_too=True, samples=3):
    """Every Stump reachable from this Wi-Fi: STUMP? answers, the Stumps those
    know (/stumps.json) at an address on this network, then (for older Stumps)
    the /24 sweep. -> probe()-like dicts, steadiest first, one per Stump."""
    found = {}

    def add(info, how):
        if not info:
            return
        k = info.get("lxmf") or info["base"]
        if k in found:
            return
        info["found_by"] = how
        st = steadiness(http, info["base"] + info["via"], samples)
        info.update(st)
        info["stump"] = True
        info["steady_bars"] = steady_bars(st["median_ms"], st["lost"], st["samples"])
        found[k] = info

    cards = ask(http)
    for c in cards:
        info = identify(http, c["base"]) or {
            "base": c["base"], "via": "/", "name": c["name"], "version": None, "release": c["release"],
            "lxmf": c["lxmf"], "ap_ip": None, "chain": _chain_summary(None), "features": None, "radio": None,
            "first_ms": None}
        info["name"] = info.get("name") or c["name"]
        info["lxmf"] = info.get("lxmf") or c["lxmf"]
        info["release"] = info.get("release") or c["release"]
        info["http"] = c["http"]
        add(info, "answer")
    for info in list(found.values()):
        for other in directory(http, info["base"]):
            if other["lxmf"] and other["lxmf"] in found:
                continue
            for u in other["http"]:
                if own_ip is None or _same_net(u, own_ip):
                    add(identify(http, _base_of(u)), "heard")
                    break
    if sweep_too and own_ip:
        known = {f["base"].split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0] for f in found.values()}
        for info in sweep(http, own_ip, prefix, skip=known, samples=samples):
            k = info.get("lxmf") or info["base"]
            if k not in found:
                info["found_by"] = "sweep"
                found[k] = info
    out = list(found.values())
    out.sort(key=lambda i: (-i["steady_bars"], i.get("median_ms") or 1e9))
    return out


def locate(http, lxmf, hints=()):
    """Where is the Stump with this identity now? -> its card, or None.
    Asks the Wi-Fi, then checks the addresses its beacon gave (hints)."""
    lxmf = (lxmf or "").lower()
    if not lxmf:
        return None
    for c in ask(http):
        if c["lxmf"] == lxmf:
            return c
    for u in hints or ():
        b = _base_of(u)
        info = identify(http, b) if b else None
        if info and info.get("lxmf") == lxmf:
            return {"name": info.get("name"), "lxmf": lxmf, "release": info.get("release"), "http": [u],
                    "here": None, "base": info["base"], "from": None}
    return None
