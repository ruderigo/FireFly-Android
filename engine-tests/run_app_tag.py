"""FireFly says it's FireFly in its LXMF announce (asked by Project Stump, beta.53).

    python3 run_app_tag.py

The announce's app data gets a fourth item, "stump:firefly-android/<version>.b.<build>", so a
Stump knows FireFly from a plain LXMF app over LoRa too (and never rewords its lines).
  * in every announce FireFly sends, and in the ones answering a path request;
  * upstream LXMF (what Sideband, NomadNet, MeshChat, Columba run) still reads the name,
    the stamp cost and compression exactly as before;
  * FireFly still reads other apps' announces, with three items or four.
"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
import RNS, LXMF
import RNS.vendor.umsgpack as msgpack
from firefly import api, stump

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

check("the tag: version numbers, then b. and the build", stump.app_tag("0.6.0-beta", 53) == "stump:firefly-android/0.6.0.b.53",
      stump.app_tag("0.6.0-beta", 53))
check("...a short version gets its .0", stump.app_tag("0.6", 53) == "stump:firefly-android/0.6.0.b.53")
check("...a beta number isn't part of it", stump.app_tag("0.5.2-beta.3", 53) == "stump:firefly-android/0.5.2.b.53")
check("...without a version (tests, a desktop run): still starts with stump", stump.app_tag() == "stump:firefly-android")

class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-tag-"), None, None, L(), "0.6.0-beta", 53)
c = api._core
c.settings.update({"display_name": "Rod Bûche"})
c.local.display_name = "Rod Bûche"

data = c.router.get_announce_app_data(c.local.hash)
items = msgpack.unpackb(data)
check("the announce's app data: the tag fourth (then what FireFly can do: run_packed_text.py)", isinstance(items, list)
      and len(items) == 5 and items[3] == "stump:firefly-android/0.6.0.b.53", items)
check("upstream LXMF reads the name as before (Sideband, Columba…)", LXMF.display_name_from_app_data(data) == "Rod Bûche",
      LXMF.display_name_from_app_data(data))
check("...the stamp cost as before (none)", LXMF.stamp_cost_from_app_data(data) is None)
check("...and compression as before", LXMF.compression_support_from_app_data(data) is True)
check("...still one msgpack array (what LXMF checks first)", 0x90 <= data[0] <= 0x9f, hex(data[0]))

sent = []
orig = RNS.Destination.announce
def capture(self, app_data=None, path_response=False, *a, **k):
    if self.hash == c.local.hash:
        sent.append(app_data if app_data is not None else
                    (self.default_app_data() if callable(self.default_app_data) else self.default_app_data))
    return orig(self, app_data, path_response, *a, **k)
RNS.Destination.announce = capture
c.announce()
check("the announce FireFly sends carries it", sent and msgpack.unpackb(sent[-1])[3] == c.app_tag, sent[-1:] and msgpack.unpackb(sent[-1]))
c.local.announce(path_response=True)                # what answering a path request sends
check("...and so does the answer to a path request", msgpack.unpackb(sent[-1])[3] == c.app_tag, msgpack.unpackb(sent[-1]))
RNS.Destination.announce = orig

# Other apps' announces: three items (Sideband), four (a Stump app), FireFly reads the name.
for label, ad in [("three items (Sideband)", msgpack.packb([b"Alice", None, [LXMF.SF_COMPRESSION]])),
                  ("four items (a Stump app)", msgpack.packb([b"Bob", 8, [LXMF.SF_COMPRESSION], "stump:firefly-rk3326/0.9.3"]))]:
    i = RNS.Identity(); d = RNS.Destination.hash(i, "lxmf", "delivery")
    c._on_delivery_announce(d, i, ad)
    p = c.store.peer(d.hex()) or {}
    check(f"FireFly reads another's announce, {label}", p.get("name") in ("Alice", "Bob"), p)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
