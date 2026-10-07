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


def _call(http, method, url, body=None, content_type=None, timeout_ms=8000):
    headers = {}
    if body is not None:
        headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
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


def board(http, base, now=None):
    data = _get_json(http, base + "/billboard.json")
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
    return {"state": "ok", "posts": posts}


def board_post(http, base, title, body=""):
    """Post, then read the board back: /post answers 303 whatever happened, so the
    refetch is the only proof. -> {"ok": bool, "reason"?, "board": board()}"""
    title, body = clean_title(title), clean_body(body)
    if not title:
        return {"ok": False, "reason": "empty", "board": board(http, base)}
    before = board(http, base)
    if before["state"] == "off":
        return {"ok": False, "reason": "off", "board": before}
    seen = {(p["id"], p["title"]) for p in before.get("posts", [])}
    # Percent-encoded UTF-8 with %20 for spaces: every form decoder reads it.
    form = urllib.parse.urlencode({"title": title, "body": body}, quote_via=urllib.parse.quote).encode("ascii")
    try:
        status, _ = _call(http, "POST", base + "/post", form,
                          "application/x-www-form-urlencoded; charset=utf-8", timeout_ms=10000)
    except Exception as e:
        return {"ok": False, "reason": "unreachable", "detail": str(e), "board": before}
    if status == 404:
        return {"ok": False, "reason": "off", "board": {"state": "off"}}
    if status not in (200, 302, 303):
        return {"ok": False, "reason": "unreachable" if status < 0 else "error", "status": status, "board": before}
    after = board(http, base)
    fresh = [p for p in after.get("posts", []) if (p["id"], p["title"]) not in seen and p["title"] == title]
    return {"ok": bool(fresh), "reason": None if fresh else "dropped", "board": after}


# ------------------------------------------------------------------ files
def files(http, base):
    data = _get_json(http, base + "/files.json")
    if data["state"] != "ok":
        return data
    out = []
    for f in data.get("files") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        size = f.get("size")
        out.append({"name": str(f["name"]), "size": size if isinstance(size, int) else None,
                    "class": f.get("class") or file_class(str(f["name"])),
                    "cost": int(f.get("cost") or 0)})
    return {"state": "ok", "sd": bool(data.get("sd", True)), "credits": bool(data.get("credits", False)),
            "files": out}


def download_url(base, name):
    """Filename percent-encoded as UTF-8 bytes (é -> %C3%A9)."""
    return base + "/download?f=" + urllib.parse.quote(name, safe="")


def upload_cost(name, credits=True):
    return CLASS_WEIGHT[file_class(name)] if credits else 0
