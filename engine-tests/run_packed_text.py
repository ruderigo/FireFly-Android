"""Packed text: more of a chat line in each LoRa packet (ztext.py, with Project Stump).

    python3 run_packed_text.py

One packet holds 295 bytes of LXMF content. To and from those whose LXMF announce says
they read it (a fifth item, {"z": 1}: a Stump after 1.0.0-beta.73, another FireFly), the
text goes packed, in about 60% of its bytes; to everyone else, as it always did.
  * the format is the Stump's, byte for byte (the same file, the same fixed messages);
  * FireFly's announce says so, and upstream LXMF still reads it as before;
  * a message to a peer that reads it goes packed, and one too long for a packet fits;
  * a packed message that comes in is stored as its text; plain ones as before;
  * content that isn't text (any LXMF app can send some) is kept, not lost.
"""
import hashlib, json, os, sys, tempfile, time, zlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app", "src", "main", "python"))
import RNS, LXMF
import RNS.vendor.umsgpack as msgpack
from LXMF.LXMessage import LXMessage
from firefly import api, stump, ztext

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:300]})" if detail and not ok else ""))
    fails += 0 if ok else 1

# --- the format: the Stump's (its tests/mp/ztext.py holds the same hash and messages)
h = hashlib.sha256(ztext._DICT + ztext._LL.encode() + ztext._DL.encode() + ztext._HEAD).hexdigest()
check("the format's tables are version 1's", h == "6ef2028f82238d884eae1b29f9fd270f3c2a036fae07421835d5427d4c120bd5", h)
VECTORS = [
    ('<alice> hey is anyone around the north trail tonight?',
     "ff09ab44ce53c2a5860a878def14caf7987e4fc38a763b75c301"),
    ("<Rod@LaBuche> on se rejoint au camp à 18h, apporte les piles et de l'eau\n<Luc@Camp2> oui bien reçu, 5 sur 5",
     "ffc958159b19bb69f3194fd11c79f0c605829b85a55cbc40abd76611b1a6f1380abd6d043e7665f00a67f6fb2699cd37b758dd0c525256047ef12813d1655fe15c76"),
    ('<maria> hola, ¿alguien me copia? estoy cerca del campamento',
     "ff49cdb460a987b4885efcfa69aac5f63682dde8a827906981e4734aa10e"),
    ('[NAMES] Rod alice bob guest-3f2a maria Luc@Camp2 jean-guy mesh-fd92',
     "ffcbc31fab825885459faa69f7abac950314645a30ca6cbeb9c5ea6640879cc9fe819d87dfb32cd6070e"),
    ('→ #lora-mesh',
     "fffee79d5e2007"),
]
for text, packed in VECTORS:
    z = ztext.pack(text.encode())
    check("packs as on a Stump: " + text[:24], z.hex() == packed, z.hex())
    check(f"...shorter ({len(text.encode())} -> {len(z)}), and read back", len(z) < len(text.encode()) and ztext.unpack(z) == text.encode())
n = len(ztext._DICT)
d = zlib.decompressobj(-15)
out = d.decompress(bytes((0, n & 255, n >> 8, ~n & 255, (~n >> 8) & 255)) + ztext._DICT + ztext._HEAD + bytes.fromhex(VECTORS[0][1])[1:])
check("...and it is what the docs say: one DEFLATE block after the dictionary", d.eof and out[n:] == VECTORS[0][0].encode())
check("a short line, or what doesn't pack: as it is", ztext.pack(b"ok") == b"ok" and ztext.pack(os.urandom(64)[:0] + bytes(range(32, 96))) == bytes(range(32, 96)))
bad = sum(1 for i in range(500) if (lambda r: not (r is None or len(r) <= ztext.MAX))(ztext.unpack(b"\xff" + os.urandom(i % 120))))
check("500 random packed contents: None or at most MAX bytes", bad == 0, bad)

# --- FireFly
class L:
    def onEvent(self, s): pass
api.start(tempfile.mkdtemp(prefix="ff-packed-"), None, None, L(), "0.6.3-beta", 74)
c = api._core
RNS.Transport.request_path = lambda *a, **k: None

data = c.router.get_announce_app_data(c.local.hash)
items = msgpack.unpackb(data)
check("the announce: the app tag, then {'z': 1}", len(items) == 5 and items[3] == c.app_tag and items[4] == {"z": 1}, items)
check("upstream LXMF reads it as before (name, stamp cost, compression)", LXMF.display_name_from_app_data(data) == c.settings["display_name"]
      and LXMF.stamp_cost_from_app_data(data) is None and LXMF.compression_support_from_app_data(data) is True)
check("...and FireFly reads its own as one that reads packed text", stump.reads_packed(data))

def peer(app_data):
    i = RNS.Identity()
    dest = RNS.Destination(i, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    RNS.Identity.remember(os.urandom(32), dest.hash, i.get_public_key())
    c._on_delivery_announce(dest.hash, i, app_data)
    return i, dest

s_id, s_dest = peer(msgpack.packb([b"LaBuche", None, [], None, {"z": 1}]))        # a Stump
o_id, o_dest = peer(msgpack.packb([b"Alice", None, [LXMF.SF_COMPRESSION]]))       # Sideband
S, O = s_dest.hash.hex(), o_dest.hash.hex()
check("a Stump whose announce says z: packed for it", S in c.packs)
check("Sideband's announce: plain", O not in c.packs)
for odd in (b"Rod", msgpack.packb([b"Rod", None, [], "stump:x"]), msgpack.packb([b"Rod", None, [], None, "z"]),
            msgpack.packb([b"Rod", None, [], None, {"z": True}]), msgpack.packb([b"Rod", None, [], None, {"z": 0}]), b""):
    check(f"not a z announce: {odd[:14]!r}", not stump.reads_packed(odd))

sent = []
c.router.handle_outbound = lambda lxm: (lxm.pack(), sent.append(lxm))
c._introduced.update((S, O))
batch = "\n".join([
    "/msg alice Anyone know if the bridge on route 117 is open again? Heard it was closed after the storm last week,",
    "and the detour through Val-d'Or adds an hour. Est-ce que quelqu'un a des nouvelles de la tempête? On a perdu le",
    "courant ici depuis ce matin, mais tout le monde va bien et on a de l'eau et de la nourriture pour trois jours."])
check("(the test's text is too long for one packet as it is)", len(batch.encode()) > LXMessage.ENCRYPTED_PACKET_MAX_CONTENT)
def send(to, text):
    sent.clear()
    mid = c.send(to, text)
    t = time.time()
    while not sent and time.time() - t < 10:
        time.sleep(0.05)
    return mid, (sent[0] if sent else None)
mid, lxm = send(S, batch)
check("to the Stump: the content goes packed", lxm is not None and lxm.content[0] == ztext.PACKED and ztext.unpack(lxm.content) == batch.encode(),
      lxm and lxm.content[:8])
check("...in one packet, where it took a link", lxm is not None and lxm.method == LXMessage.OPPORTUNISTIC
      and len(lxm.content) <= LXMessage.ENCRYPTED_PACKET_MAX_CONTENT, lxm and (lxm.method, len(lxm.content)))
check("...the conversation keeps the text itself", (c.store.message(mid) or {}).get("content") == batch)
mid, lxm = send(O, batch)
check("to Sideband: as it always went (plain, over a link)", lxm is not None and lxm.content == batch.encode() and lxm.method == LXMessage.DIRECT,
      lxm and (lxm.content[:8], lxm.method))
mid, lxm = send(S, "/names")
check("a short line to the Stump: as it is", lxm is not None and lxm.content == b"/names")
c._on_delivery_announce(s_dest.hash, s_id, msgpack.packb([b"LaBuche", None, []]))
mid, lxm = send(S, batch)
check("the Stump's announce without z again (an older release put back): plain again", lxm is not None and lxm.content == batch.encode())
c._on_delivery_announce(s_dest.hash, s_id, msgpack.packb([b"LaBuche", None, [], None, {"z": 1}]))

# --- coming in
def incoming(src_dest, content, title=""):
    m = LXMessage(c.local, src_dest, content, title=title, desired_method=LXMessage.OPPORTUNISTIC)
    m.pack()
    got = LXMessage.unpack_from_bytes(m.packed)
    c._on_message(got)
    rows = json.loads(api.messages(src_dest.hash.hex()))
    return rows[-1] if rows else {}
# From a person (not a Stump session), so the text lands in a conversation as it is.
p_id, p_dest = peer(msgpack.packb([b"Bob", None, [], "stump:firefly-android/0.6.3.b.74", {"z": 1}]))
their = RNS.Destination(p_id, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
text = "<Rod@LaBuche> on se rejoint au camp à 18h, apporte les piles et de l'eau\n<Luc@Camp2> oui bien reçu, 5 sur 5"
row = incoming(their, ztext.pack(text.encode()))
check("a packed message in: stored as its text", row.get("content") == text, row.get("content"))
row = incoming(their, "plain as ever")
check("a plain one: as before", row.get("content") == "plain as ever", row)
row = incoming(their, b"\xff\x00\x01\x02 not packed, not text")
check("content that isn't text (and can't be unpacked): kept, not lost", row.get("content", "").endswith("not packed, not text"), row)
row = incoming(their, b"caf\xe9 latin-1")
check("...nor is any other content that isn't UTF-8", row.get("content", "").startswith("caf") and row["content"].endswith("latin-1"), row)

# --- switched off
c.settings.update({"packed_text": False})
check("off: the announce stops at the app tag", len(msgpack.unpackb(c.router.get_announce_app_data(c.local.hash))) == 4)
mid, lxm = send(S, batch)
check("off: nothing packed", lxm is not None and lxm.content == batch.encode())
row = incoming(their, ztext.pack(text.encode()))
check("off: nothing unpacked", row.get("content") != text)

print("ALL PASS" if not fails else f"{fails} FAILED")
os._exit(1 if fails else 0)
