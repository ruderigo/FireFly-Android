"""A demo FireFly for the README screenshots: a few friends and a neighbourhood Stump.

    python3 tools/screenshots/seed.py <out dir>

Writes <out dir>/firefly.db and settings.json, made with the app's own store (store.py),
to be copied into a fresh install's files/firefly/ before its first start. The people,
addresses and the Stump are made up; the voice note is the engine tests' sample.
"""
import hashlib, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "app", "src", "main", "python"))
from firefly.store import Store          # noqa: E402

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
db = os.path.join(out, "firefly.db")
for f in (db, db + "-wal", db + "-shm"):
    if os.path.exists(f):
        os.remove(f)
s = Store(db)
now = time.time()
ago = lambda minutes: now - minutes * 60
addr = lambda name: hashlib.sha256(b"firefly-demo:" + name.encode()).hexdigest()[:32]
voice = open(os.path.join(ROOT, "engine-tests", "kristoff_opus.ogg"), "rb").read()

with open(os.path.join(out, "settings.json"), "w") as f:
    json.dump({"display_name": "Robin", "settings_version": 4}, f)

# ---------------------------------------------------------------- friends (plain LXMF)
sam, juniper, ocean, theo = addr("sam"), addr("juniper"), addr("ocean"), addr("theo")
s.upsert_peer(sam, "lxmf", name="Sam", hops=1)
s.upsert_peer(juniper, "lxmf", name="Juniper", hops=2)
s.upsert_peer(ocean, "lxmf", name="Harbour Watch", hops=3)
s.upsert_peer(theo, "lxmf", name="Theo", hops=1)
for p in (sam, juniper, ocean, theo):
    s.upsert_peer(p, "lxmf", saved=1)

s.add_message(ocean, False, "Tide's low at 4:12 pm, the north beach path is clear.", "received",
              ts=ago(300), rssi=-104, snr=-3.5)
s.add_message(theo, True, "Thanks for the antenna tips, the mast is up!", "delivered", ts=ago(190))
s.add_message(theo, False, "Looks great from here 👋", "received", ts=ago(185), rssi=-91, snr=2.0)
s.add_message(juniper, False, "", "received", ts=ago(70), rssi=-88, snr=6.5, audio=(16, voice))

chat = [
    (False, "Are you coming to the trail cleanup on Saturday?", -79, 9.5, 52),
    (True, "Yes! I'll bring gloves and a few bags.", None, None, 50),
    (False, "Perfect. We meet at the north trailhead at 9.", -81, 8.75, 49),
    (True, "", None, None, 47),                       # a voice note
    (False, "Ha, got it. No cell signal up there, so LoRa it is 📡", -84, 7.0, 45),
    (True, "I'll keep FireFly on. See you there!", None, None, 44),
]
for out_, text, rssi, snr, m in chat:
    if out_ and not text:
        s.add_message(sam, True, "", "delivered", ts=ago(m), audio=(16, voice))
    elif out_:
        s.add_message(sam, True, text, "delivered", ts=ago(m))
    else:
        s.add_message(sam, False, text, "received", ts=ago(m), rssi=rssi, snr=snr)
s.add_message(sam, False, "🌲👍", "received", ts=ago(12), rssi=-80, snr=9.0, unread=True)

# ---------------------------------------------------------------- a neighbourhood Stump
stump = addr("cedar-grove")
key = "mesh:" + stump
s.upsert_peer(stump, "lxmf", name="Cedar Grove", hops=1, stump="Cedar Grove", stump_ver="1.0.0-beta.88")
s.upsert_stump_node(key, "mesh", stump, name="Cedar Grove", room="main", nick="robin",
                    topic="Neighbourhood mesh. Be kind.", auth="ok", online=1, last_seen=ago(1),
                    lxmf=stump, release="1.0.0-beta.88",
                    rooms=[{"name": "main", "count": 6, "topic": "Neighbourhood mesh. Be kind."},
                           {"name": "lora-mesh", "count": 3, "topic": "LoRa (radio)"},
                           {"name": "garden", "count": 2, "topic": "Seeds, tools, harvest swaps"}],
                    users=["robin", "maya", "theo", "priya", "sol", "ines"])
room = [
    ("system", None, "→ #main", 95),
    ("msg", "maya", "Morning all! The farmers' market is on until 1 today 🍓", 90),
    ("msg", "theo", "Anyone have a spare 18650 battery? My node is running low.", 64),
    ("msg", "priya", "I do, I'll leave one in the garden shed at noon.", 61),
    ("mine", "robin", "Thanks for the new antenna on the library roof, the range is so much better.", 40),
    ("msg", "sol", "Heard you from 3 km away this morning 📡", 37),
    ("msg", "ines", "Potluck at the park pavilion on Sunday, bring a dish!", 20),
    ("msg", "maya", "I'll make lemonade 🍋", 18),
]
for kind, nick, body, m in room:
    if kind == "mine":
        s.add_line(key, "main", "msg", body, nick=nick, mine=True, ts=ago(m))
    else:
        s.add_line(key, "main", kind, body, nick=nick, ts=ago(m))
s.add_line(key, "garden", "msg", "Tomato seedlings up for grabs by the gate.", nick="priya", ts=ago(200))

s.add_dm(key, "priya", False, "The battery is in the shed, top shelf by the watering cans.", "received", ts=ago(30))
s.add_dm(key, "priya", True, "Perfect, thank you! I'll bring you some basil.", "delivered", ts=ago(28))
s.add_dm(key, "priya", False, "Deal 🌿", "received", ts=ago(27), unread=True)

s.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
s.db.close()
print(json.dumps({"db": db, "sam": sam, "stump_key": key}))
