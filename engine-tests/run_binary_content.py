"""A message whose content isn't text is kept (Nov 2026 review).

    python3 run_binary_content.py

Any LXMF app can send bytes that aren't UTF-8 as a message's content. LXMF's
content_as_string() gives None for those, and the Store's content column can't be NULL:
the message was lost, with an exception in LXMF's delivery thread. Now it's kept, the
bytes that aren't text shown as replacement characters.
  * also: each engine keeps its own list of peers asked for a Stump beacon (it was one
    list shared by every Core in the process).
"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
import RNS
from LXMF.LXMessage import LXMessage
from firefly import api
from firefly.core import Core
from firefly import stump

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-binary-"), None, None, L())
c = api._core
RNS.Transport.request_path = lambda *a, **k: None

sender = RNS.Identity()
src = RNS.Destination(sender, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
RNS.Identity.remember(os.urandom(32), src.hash, sender.get_public_key())

def incoming(content):
    m = LXMessage(c.local, src, content, desired_method=LXMessage.OPPORTUNISTIC)
    m.pack()
    try:
        c._on_message(LXMessage.unpack_from_bytes(m.packed))
    except Exception as e:
        return {"error": repr(e)}
    rows = json.loads(api.messages(src.hash.hex()))
    return rows[-1] if rows else {}

row = incoming("plain text")
check("a text message: as before", row.get("content") == "plain text", row)
row = incoming(b"caf\xe9 in Latin-1")
check("content that isn't UTF-8: kept", row.get("content", "").startswith("caf") and row["content"].endswith("in Latin-1"), row)
row = incoming(bytes(range(200, 256)) + b" end")
check("...and plain bytes too", row.get("content", "").endswith(" end") and "error" not in row, row)
check("three messages in the conversation", len(json.loads(api.messages(src.hash.hex()))) == 3)

check("each engine has its own list of beacons asked for", "_beacon_asked" not in vars(Core)
      and isinstance(vars(c).get("_beacon_asked"), (set, stump.RecentSet)))   # (capped since Nov 17)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
