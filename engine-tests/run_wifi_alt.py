"""A Stump opened over LoRa gets its billboard and files when the phone joins its Wi-Fi (Oct 24).

    python3 run_wifi_alt.py

Opened over LoRa, a Stump's billboard and files were unavailable until the
person searched for Stumps again, even after the phone had joined the
Stump's Wi-Fi. Now the app looks for its Stumps when the Wi-Fi settles
(stump_refresh_wifi, forced), and the LoRa side's view says when the same
Stump answers on the Wi-Fi (wifi_alt, wifi_alt_online): the screen takes the
billboard and files from there.
"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
sys.path.insert(0, HERE)
os.environ["FIREFLY_BROADCAST"] = "127.0.0.13"
from fake_stump import FakeRRC, rns_verify, serve_http, serve_udp
from firefly import api

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1
def wait(cond, secs=20):
    t = time.time()
    while time.time() - t < secs:
        if cond(): return True
        time.sleep(0.3)
    return False

LX = "ab" * 16
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-wifialt-"), None, None, L())
c = api._core
c.store.upsert_stump_node("mesh:" + LX, "mesh", LX, name="LaBuche", last_seen=time.time())
v = lambda: json.loads(api.stump_view("mesh:" + LX))
check("over LoRa only: no Wi-Fi side", v()["wifi_alt"] is None and v()["wifi_alt_online"] is False)

# The phone joins the Stump's Wi-Fi: the Stump answers STUMP? and serves its pages.
rrc = FakeRRC(rns_verify); rrc.node_lxmf = LX
serve_http(47720, rrc, host="127.0.0.13")
serve_udp("127.0.0.13", {"stump": 1, "name": "LaBuche", "lxmf": LX, "release": "1.0.0-beta.33",
                         "http": ["http://127.0.0.13:47720/"], "here": "http://127.0.0.13:47720/"})
api.stump_refresh_wifi()                       # (the Stumps tab may have asked just before)
got = json.loads(api.stump_refresh_wifi(True))  # what the app does once the Wi-Fi has settled
check("the settled Wi-Fi is searched even right after another search (forced)",
      got == ["wifi:http://127.0.0.13:47720"], got)
check("the LoRa side's view now offers its Wi-Fi side, answering",
      wait(lambda: v()["wifi_alt"] == "wifi:http://127.0.0.13:47720" and v()["wifi_alt_online"] is True),
      (v()["wifi_alt"], v()["wifi_alt_online"]))
b = json.loads(api.stump_board(v()["wifi_alt"]))
check("...and its billboard can be read from there", b.get("state") == "ok", b)
check("the quick check before opening: true while it answers",
      api.stump_web_reachable(v()["wifi_alt"]) == "true")
check("...false for an address where nothing answers (the phone left that Wi-Fi)",
      api.stump_web_reachable("wifi:http://127.0.0.1:47729") == "false")
print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
