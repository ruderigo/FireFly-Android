"""A Stump's ts before 2020 is "time unknown" (Nov 22; Stump 1.0.0-beta.88).

    python3 run_stump_ts.py

Before beta.88 an ESP32 Stump stamped room lines and DMs with MicroPython's clock, which
counts from 2000: read as Unix time, a DM relayed by LaBuche showed 1996-11-21 and sorted
before older lines. From beta.88 its ts is Unix time, but a board that hasn't learned the time
from the network still reads early 2000. So a Stump's ts before 2020 means "time unknown":
the line is filed by when it arrived. A real time is kept (a DM the Stump held while we were
away). Lines already stored with such a time are put back in the order they arrived, once.
"""
import json, os, sqlite3, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
from firefly import api, stump
from firefly.store import Store

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

now = time.time()
in_1996 = now - 946684800                     # an ESP32 Stump's clock before beta.88, read as Unix time
early_2000 = 946684800 + 3600                 # a board that hasn't learned the time yet
check("stump_ts: the 1996 reading is time unknown", stump.stump_ts(in_1996) is None)
check("...early 2000 too", stump.stump_ts(early_2000) is None)
check("...nothing or junk too", stump.stump_ts(None) is None and stump.stump_ts("soon") is None)
check("...a real time is kept", stump.stump_ts(now - 3600) == now - 3600)

class L:
    def onEvent(self, s): pass
home = tempfile.mkdtemp(prefix="ff-stumpts-")
api.start(home, None, None, L())
c = api._core
LX = "c6" * 16; K = "mesh:" + LX
c.store.upsert_stump_node(K, "mesh", LX, name="LaBuche", last_seen=now)
sess = c.session(K)
sess.pipe.send = lambda line, control=False: None
sess.pipe.kind = "wifi"
sess.hello = "done"
sess.room = "lora-mesh"
sess.post("what")                                         # ours, now
time.sleep(0.05)
sess.on_http_poll({"room": "lora-mesh", "users": [], "messages": [],
                   "dms": [{"id": 101, "nick": "R36-Operator", "body": "what your name", "ts": in_1996},
                           {"id": 102, "nick": "R36-Operator", "body": "held for you", "ts": now - 7200}]})
sess.on_http_poll({"room": "lora-mesh", "users": [], "dms": [],
                   "messages": [{"id": 1, "nick": "bob", "body": "from a board", "ts": early_2000, "kind": "msg"}]})
rows = c.store.db.execute("SELECT body, ts FROM stump_dms WHERE node=? ORDER BY ts", (K,)).fetchall()
got = {b: t for b, t in rows}
check("a DM with the 1996 time: filed when it arrived (today, not 1996)",
      abs(got.get("what your name", 0) - time.time()) < 60, got)
check("...a DM the Stump held with a real time keeps it", abs(got.get("held for you", 0) - (now - 7200)) < 1, got)
line = c.store.db.execute("SELECT ts FROM stump_lines WHERE node=? AND body='from a board'", (K,)).fetchone()
check("a room line with an early-2000 time: filed when it arrived", line and abs(line[0] - time.time()) < 60, line)

# Already stored by an earlier build: put back in the order they arrived
db = os.path.join(tempfile.mkdtemp(prefix="ff-stumpts-db-"), "f.db")
s = Store(db)
s.add_dm("wifi:x", "R36", True, "fuck youuuu", "delivered", ts=now - 300)
s.add_dm("wifi:x", "R36", False, "what your name", "received", ts=in_1996 - 200)
s.add_dm("wifi:x", "R36", True, "what", "delivered", ts=now - 100)
s.add_dm("wifi:x", "R36", False, "i said what your name", "received", ts=in_1996 - 50)
s.add_line("wifi:x", "lora-mesh", "msg", "hi", nick="carol", ts=now - 500)
s.add_line("wifi:x", "lora-mesh", "msg", "later", nick="carol", ts=in_1996)
n = s.repair_unknown_stump_times()
order = [r[0] for r in s.db.execute("SELECT body FROM stump_dms ORDER BY ts, id")]
check("old misdated DMs: back in the order they arrived", order == ["fuck youuuu", "what your name", "what", "i said what your name"], order)
check("...no time before 2020 left", s.db.execute("SELECT COUNT(*) FROM stump_dms WHERE ts < 1577836800").fetchone()[0] == 0
      and s.db.execute("SELECT COUNT(*) FROM stump_lines WHERE ts < 1577836800").fetchone()[0] == 0)
check("...room lines too", [r[0] for r in s.db.execute("SELECT body FROM stump_lines ORDER BY ts")] == ["hi", "later"])
check("...done once: nothing to do at the next start", n == 3 and s.repair_unknown_stump_times() == 0, n)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
