"""A FireFly engine over TCP for run_propagation.py. Prints what it receives.
    python3 pn_peer.py <home> <tcp port> <seconds to run> [sync]"""
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "app", "src", "main", "python"))
from firefly.core import Core

class L:
    def onEvent(self, s):
        e = json.loads(s)
        if e["type"] == "message":
            print("PEER got", e["text"] if e["text"] else "(voice note)" if e.get("audio") else "", flush=True)

core = Core(sys.argv[1], listener=L(), log=lambda *a: None)
core.settings.update({"auto_interface": False, "tcp_peers": [f"127.0.0.1:{sys.argv[2]}"], "display_name": "Offline Friend",
                      "log_level": 2, "radio": {"enabled": False}, "announce_interval_min": 30})
core.start()
if os.environ.get("FF_DOWNLOAD_LIMIT_KB"):
    # Stand-in for LaBuche's own ~14 KB cap on each sync reply: the phone asks for
    # at most this much per sync, so what's waiting takes several rounds to collect.
    core.router.delivery_per_transfer_limit = int(os.environ["FF_DOWNLOAD_LIMIT_KB"])
print("PEER ready", core.address, flush=True)
end = time.time() + float(sys.argv[3])
synced = False
rounds = 0
state = None
while time.time() < end:
    if core.sync_state != state:
        state = core.sync_state
        print("PEER state", state, flush=True)
    if core.sync_rounds != rounds:
        rounds = core.sync_rounds
        print("PEER rounds", rounds, flush=True)
    if len(sys.argv) > 4 and not synced and core.propagation_node():
        print("PEER pn", core.propagation_node(), flush=True)
        synced = core.sync()
        core.sync_soon(min_gap=0); core.sync()        # overlapping asks: must join, not restart
    time.sleep(0.5)
core.stop()
print("PEER bye", flush=True)
os._exit(0)
