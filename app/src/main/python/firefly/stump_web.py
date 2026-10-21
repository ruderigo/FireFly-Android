"""A Stump node's billboard and file shelf, over Wi-Fi (HTTP only).

CLIENT_QUICKSTART.md, "Billboard" and "Files (fservbot)":

  GET  /billboard.json   {"posts": [{"id", "title", "body", "sig"}]}, newest first
  POST /post             form-urlencoded title= (80) body= (600); always 303,
                         even when an empty post was silently dropped
  GET  /files.json       {"sd", "credits", "files": [{"name", "size", "class", "cost"}]}
  POST /upload, GET /download?f=   streamed by the app itself (Kotlin), not here

A feature the node's technician switched off answers 404 "not offered on this
node": that's "this node doesn't do that", never an error. Small JSON goes
through the engine; file bodies never do.
"""
import json
import time
import urllib.parse

TITLE_MAX, BODY_MAX = 80, 600
POST_LIFETIME_S = 72 * 3600
CLASS_WEIGHT = {"video": 3, "music": 2, "document": 1, "other": 1}
EXT_CLASS = {**{e: "video" for e in ("mp4", "mkv", "avi", "mov")},
             **{e: "music" for e in ("mp3", "flac", "wav", "ogg", "m4a")},
             **{e: "document" for e in ("pdf", "txt", "doc", "docx")}}


def file_class(name):
    """The node's weight classes by extension (uploads credit it, downloads debit it)."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return EXT_CLASS.get(ext, "other")


def _attr(obj, name):
    v = getattr(obj, name, None)
    if v is None and hasattr(obj, "get" + name.capitalize()):
        v = getattr(obj, "get" + name.capitalize())()
    return v


def _call(http, method, url, body=None, content_type=None, timeout_ms=8000, extra=None):
    headers = dict(extra or {})
    if body is not None:
        headers.update({"Content-Type": content_type, "Content-Length": str(len(body))})
    r = http.request(method, url, json.dumps(headers), body, timeout_ms)
    raw = _attr(r, "body")
    return int(_attr(r, "status")), bytes(raw) if raw is not None else b""


def _get_json(http, url):
    """-> {"state": "ok" | "off" | "unreachable" | "error", ...the JSON on ok}"""
    try:
        status, body = _call(http, "GET", url)
    except Exception as e:
        return {"state": "unreachable", "detail": str(e)}
    if status == 404:
        return {"state": "off"}
    if status != 200:
        return {"state": "unreachable" if status < 0 else "error", "status": status,
                "detail": body[:200].decode("utf-8", "replace")}
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return {"state": "error", "status": status, "detail": "not JSON"}
    return {"state": "ok", **(data if isinstance(data, dict) else {})}


# ------------------------------------------------------------------ billboard
def clean_title(title):
    """What the node keeps: line breaks become spaces, 80 characters."""
    return " ".join((title or "").replace("\r", "\n").split("\n")).strip()[:TITLE_MAX]


def clean_body(body):
    return (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:BODY_MAX]


def _post_time(pid):
    """A post id is its posting time ("1790978231.612803836"), when the node's clock was set."""
    try:
        t = float(pid)
    except (TypeError, ValueError):
        return None
    return t if t > 1_600_000_000 else None


def _fold(t):
    """Lowercase without accents: 'velo' finds 'Vélo', as the node searches."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


def _query(**params):
    q = {k: v for k, v in params.items() if v}
    return ("?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)) if q else ""


def board(http, base, now=None, q="", sort=""):
    """?q= (title or body holds one of the words) and ?sort=newest|oldest|title on current nodes;
    an older node ignores them, so the words are also applied here."""
    data = _get_json(http, base + "/billboard.json" + _query(q=q, sort=sort))
    if data["state"] != "ok":
        return data
    now = now or time.time()
    posts = []
    for p in data.get("posts") or []:
        if not isinstance(p, dict) or not p.get("title"):
            continue
        t = _post_time(p.get("id"))
        posts.append({"id": p.get("id"), "title": str(p.get("title")), "body": str(p.get("body") or ""),
                      "sig": str(p.get("sig") or ""), "posted": t,
                      "expires_in": max(0, int(t + POST_LIFETIME_S - now)) if t else None})
    words = _fold(q).split()
    if words:
        posts = [p for p in posts if any(w in _fold(p["title"] + " " + p["body"]) for w in words)]
    return {"state": "ok", "posts": posts}


def board_post(http, base, title, body=""):
    """Post to the billboard. -> {"ok": bool, "reason"?, "id"?, "message"?, "board": board()}

    A current node answers as data when asked (Accept: application/json):
    201 {"ok": true, "id"}, 400 {"error": "title_required"}, 507 {"error":
    "storage"}. An older one answers 303 whatever happened; then the board is
    read back, and a post with this title that wasn't there before is the proof."""
    title, body = clean_title(title), clean_body(body)
    if not title:
        return {"ok": False, "reason": "empty", "board": board(http, base)}
    before = board(http, base)
    if before["state"] == "off":
        return {"ok": False, "reason": "off", "board": before}
    form = urllib.parse.urlencode({"title": title, "body": body}, quote_via=urllib.parse.quote).encode("ascii")
    status, data = _json_answer(lambda: _call(
        http, "POST", base + "/post", form, "application/x-www-form-urlencoded; charset=utf-8",
        timeout_ms=10000, extra={"Accept": "application/json"}))
    if status == 404:
        return {"ok": False, "reason": "off", "board": {"state": "off"}}
    if status < 0:
        return {"ok": False, "reason": "unreachable", "message": data.get("message"), "board": before}
    after = board(http, base)
    if "ok" in data:                                    # a node that answers as data
        if data["ok"]:
            return {"ok": True, "id": data.get("id"), "board": after}
        reason = {"title_required": "empty", "storage": "storage", "rate_limited": "rate_limited"}.get(data.get("error"), "error")
        return {"ok": False, "reason": reason, "message": data.get("message"), "board": after}
    if status == 429:                                   # 3 posts a minute per visitor
        return {"ok": False, "reason": "rate_limited", "board": after}
    if status not in (200, 201, 302, 303):
        return {"ok": False, "reason": "error", "status": status, "board": after}
    seen = {(p["id"], p["title"]) for p in before.get("posts", [])}
    fresh = [p for p in after.get("posts", []) if (p["id"], p["title"]) not in seen and p["title"] == title]
    return {"ok": bool(fresh), "reason": None if fresh else "dropped", "board": after}


def _json_answer(send):
    """(status, dict) from a write that may answer as JSON; the dict is {} when it didn't."""
    try:
        status, raw = send()
    except Exception as e:
        return -1, {"message": str(e)}
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        data = {}
    return status, data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ files
def files(http, base, q="", cat="", sort=""):
    """?q= (in the name), ?cat=video|music|document|image|other, ?sort=newest|oldest|name|small|large
    on current nodes (rows carry cat and mtime); applied here too for older ones."""
    data = _get_json(http, base + "/files.json" + _query(q=q, cat=cat, sort=sort))
    if data["state"] != "ok":
        return data
    out = []
    for f in data.get("files") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        size = f.get("size")
        out.append({"name": str(f["name"]), "size": size if isinstance(size, int) else None,
                    "class": f.get("class") or file_class(str(f["name"])),
                    "cat": f.get("cat") or f.get("class") or file_class(str(f["name"])),
                    "mtime": f.get("mtime") if isinstance(f.get("mtime"), (int, float)) else None,
                    "cost": int(f.get("cost") or 0)})
    words = _fold(q).split()
    if words:
        out = [f for f in out if any(w in _fold(f["name"]) for w in words)]
    if cat:
        out = [f for f in out if f["cat"] == cat]
    return {"state": "ok", "sd": bool(data.get("sd", True)), "credits": bool(data.get("credits", False)),
            "files": out}


def download_url(base, name):
    """Filename percent-encoded as UTF-8 bytes (é -> %C3%A9)."""
    return base + "/download?f=" + urllib.parse.quote(name, safe="")


def upload_cost(name, credits=True):
    return CLASS_WEIGHT[file_class(name)] if credits else 0


# ------------------------------------------------------------------ plugins
SLASH_ONLY_PLUGINS = ("stumpid", "fservbot")       # /auth, /admin, /fs…: some take a password


def plugins(http, base):
    """GET /plugins.json: the node's plugin commands, as `!word` suggestions.
    -> {"state", "trigger", "commands": [{"word", "plugin"}]}

    stumpid and fservbot are listed by the node too, but their commands are slash
    only (some carry a password), and FireFly already handles /auth itself."""
    data = _get_json(http, base + "/plugins.json")
    if data["state"] != "ok":
        return data
    trigger = str(data.get("trigger") or "!")[:2]
    out, seen = [], set()
    for p in data.get("plugins") or []:
        if not isinstance(p, dict) or p.get("name") in SLASH_ONLY_PLUGINS:
            continue
        for c in p.get("commands") or []:
            word = str(c).strip().lstrip("/!")
            if word and word not in seen and len(word) <= 20 and " " not in word:
                seen.add(word)
                out.append({"word": trigger + word, "plugin": str(p.get("name") or "")})
    return {"state": "ok", "trigger": trigger, "commands": out}


# ------------------------------------------------------------------ apps on the node
def _version_key(v):
    """The node's own rule (fserv._version_key): dot-separated, numbers compared as
    numbers, anything else (0-beta) below any number. 0.2.11 > 0.2.9;
    0.5.0-beta.1 < 0.5.0."""
    out = []
    for part in str(v).split("."):
        out.append(int(part) if part.isdigit() else -1)
    return out


def newer(candidate, mine):
    a, b = _version_key(candidate), _version_key(mine)
    n = max(len(a), len(b))
    return a + [0] * (n - len(a)) > b + [0] * (n - len(b))


def tools(http, base, my_version):
    """GET /tools.json: the FireFly apps the node carries. -> {"state", "update": {...} | None}
    `update` is the node's Android app when it's newer than ours."""
    data = _get_json(http, base + "/tools.json")
    if data["state"] != "ok":
        return data
    update = None
    for a in data.get("apps") or []:
        if isinstance(a, dict) and a.get("app") == "android" and a.get("version") and a.get("url"):
            if newer(str(a["version"]), my_version):
                url = str(a["url"])
                update = {"version": str(a["version"]), "file": str(a.get("file") or ""),
                          "size": a.get("size") if isinstance(a.get("size"), int) else None,
                          "url": url if "://" in url else base + "/" + url.lstrip("/")}
    return {"state": "ok", "update": update}


# ------------------------------------------------------------------ chained Stumps
def chain(http, base):
    """GET /chain.json (Stump 1.0.0-beta.2): this Stump's place in a chain over Wi-Fi.
    -> {"state", "enabled", "role": "root" | "member" | None, "below": n, "parent": ip | None}
    Read defensively: the client spec names the endpoint but not its fields; these
    are chain.status()'s."""
    data = _get_json(http, base + "/chain.json")
    if data["state"] != "ok":
        return data
    if not data.get("enabled"):
        return {"state": "ok", "enabled": False, "role": None, "below": 0, "parent": None}
    up = data.get("uplink") if isinstance(data.get("uplink"), dict) else {}
    below = data.get("below") if isinstance(data.get("below"), list) else []
    role = data.get("role") if data.get("role") in ("root", "member") else None
    return {"state": "ok", "enabled": True, "role": role, "below": len(below),
            "parent": up.get("parent") if isinstance(up.get("parent"), str) else None}


# ------------------------------------------------------------------ the node's radio
RADIO_KEYS = (("frequency", "frequency"), ("bandwidth", "bandwidth"), ("sf", "spreading_factor"), ("cr", "coding_rate"))


def node_radio(http, base):
    """/node.json's radio (Stump 1.0.0-beta.5+): {"frequency" Hz, "bandwidth" Hz, "sf", "cr"}, or None
    (no radio on the node, or an older node)."""
    data = _get_json(http, base + "/node.json")
    r = data.get("radio") if data.get("state") == "ok" else None
    if not isinstance(r, dict):
        return None
    try:
        return {k: int(r[k]) for k, _ in RADIO_KEYS}
    except (KeyError, TypeError, ValueError):
        return None


def node_mode(http, base):
    """/node.json's "mode" (Stump 1.0.0-beta.26+): "rescue" after failed starts (no radio,
    chain or plugins for now; it comes back by itself), else "normal" or None."""
    data = _get_json(http, base + "/node.json")
    m = data.get("mode") if data.get("state") == "ok" else None
    return m if isinstance(m, str) else None


def radio_differences(node, mine):
    """Which of the node's settings differ from ours (FireFly's names). A mismatched radio
    doesn't error; it just never hears anything."""
    return [ours for theirs, ours in RADIO_KEYS if int(mine.get(ours) or 0) != int(node[theirs])]


def radio_patch(node):
    """FireFly settings for the node's radio (TX power stays ours: it's about range, not matching)."""
    return {ours: int(node[theirs]) for theirs, ours in RADIO_KEYS}
