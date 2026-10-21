# FireFly for Android

A LoRa-first Reticulum messenger for Android, built for the Stump network.
It speaks standard **LXMF** (so it talks to Sideband, NomadNet, MeshChat and
FireFly handhelds) and understands **Stump nodes**: rooms, DMs, `/auth`,
over the mesh *and* over the node's Wi-Fi, with one identity.

The radio is an RNode on **Bluetooth LE or USB**, found and attached at
runtime. Both run through Reticulum's own RNode driver.

Status: **0.5.0: one person on a Stump, over LoRa and Wi-Fi** (for Stump 1.0.0-beta.33). Built and field-tested: BLE and USB
RNodes, LoRa messaging with the R36MAX handheld, Stump `/auth` over LoRa,
Opus voice notes everywhere, and delivery with the screen locked. Betas are
made in [Sandbox_FireFly_Android](https://github.com/ruderigo/Sandbox_FireFly_Android),
alongside the Stump beta ([SandBox_Stump_On](https://github.com/ruderigo/SandBox_Stump_On)).

### 0.5.0: one person on a Stump, over LoRa and Wi-Fi
Everything from the ten betas below, in short:
- **The greeting:** on a Stump's Wi-Fi FireFly says who it is by itself
  (`/hello`, both sides signing; `/auth` + `/nick` on older Stumps), so you
  are one person there, under your name, with your DMs reaching you over
  Wi-Fi or LoRa as you choose (Settings › Stump messages).
- **Stumps found by who they are, not where they are:** Find Stumps asks
  the Wi-Fi (`STUMP?`), a Stump the router moved is followed to its new
  address with its history, and the gateway of a home Wi-Fi is no longer
  taken for a Stump.
- **No radio, no dead ends:** a Stump reached over LoRa and Wi-Fi opens on
  the side that can reach it; real reasons instead of "-1" or a false
  "too fast"; Verify that can't succeed says why at once.
- **Private messages:** one conversation per person across LoRa and Wi-Fi,
  renames followed, Delete and Block.
- **Screens:** LoRa and Wi-Fi lights (green, red, grey; orange-green while
  connecting), a steady Stumps list, Stump over Wi-Fi in the Network tab,
  the RNode in use named, Use to switch RNodes, the version in Settings.
- **Stump 1.0.0-beta.19 to beta.33:** room renames, hybrid and closed
  access, busy Stumps (503), upload limits, rescue starts.
- From 0.4.1: local-network discovery on Android, message states that no
  longer get stuck.

### 0.5.0-beta.10: a steadier Stumps tab, two connection lights
- **The Stumps list keeps still:** it's sorted by name. It was sorted by
  "last heard", so Stumps close to each other kept swapping places.
- **"Stump over Wi-Fi"** (open a Stump by its address) moved from the
  Stumps tab to the Network tab.
- **Two lights at the top instead of the radio label:** **LoRa** and
  **Wi-Fi**, each green (working), red (needs attention) or grey (not
  connected). LoRa: green with an RNode online; red when one is refused or
  the paired ones don't answer; grey when there's none. Wi-Fi: grey off
  Wi-Fi; red when the Wi-Fi Stumps you have open all stopped answering;
  green otherwise. A tap still opens the Network tab.
- The Network tab's traffic counter goes up to GB.
- **Connecting shows:** after **Search now** or **Use**, the buttons wait
  ("Searching…", "Connecting…" on the RNode being connected) until the
  radio has done it, and the **LoRa** light flashes orange and green
  meanwhile (also while an RNode is being connected on its own).
- **Private messages on a Stump, whichever way they came** (field fix): a
  Stump reached over LoRa and Wi-Fi is one row but two entries, and a DM
  that arrived over LoRa (to the phone's old nick) was counted on the row
  but not shown, because the row opens the Wi-Fi side. A Stump's private
  messages are now one conversation per person across both; a reply from
  the LoRa side with no radio goes over Wi-Fi.
- **Delete and block in a Stump's private messages:** long-press a
  conversation (or use its ⋮ menu). Delete removes it from this phone;
  Block also drops that nick's private messages on this Stump from then
  on, without a notification (the Stump has no block of its own, so it
  still carries them). Blocked nicks are listed under the conversations,
  with Unblock.
- **One person on a Stump over Wi-Fi and LoRa** (Stump 1.0.0-beta.25):
  FireFly now verifies itself (`/auth`) as soon as it connects over Wi-Fi,
  without a tap, so the Stump can join your Wi-Fi and LoRa sides into one
  person (one nick, private messages reaching you either way). Quietly: no
  line in the room, and a Stump that doesn't answer leaves no "failed".
- **The greeting** (Stump 1.0.0-beta.31, "one person, one name"): on every
  Wi-Fi connection FireFly says who it is before anything else, `/auth`
  then `/nick <Your name>` (just `/auth` when the Stump already heard the
  name on LoRa), and again whenever the Stump asks (`"hello":
  "required"`). Until then a line you type waits and then goes out; the
  screen keeps what it showed. A new name in Settings reaches the Wi-Fi
  Stumps too (`/nick`). The phone is then one person on the Stump, listed
  once under your name, with your DMs reaching you over Wi-Fi.
- **`/hello`** (Stump 1.0.0-beta.32/33): the greeting is now one exchange
  where both sides sign. FireFly checks the Stump's signature (and that
  its key is the one behind its LXMF address, and the Stump it knows at
  that address) before it introduces itself with its own signature, your
  name and your route. An impostor gets nothing; a real but different
  Stump at that address sends FireFly looking for yours. A 429 is waited
  out. Older Stumps still get `/auth` + `/nick`.
- **Route toggle,** Settings › Stump messages: **Over Wi-Fi** (in the app
  while it's connected) or **Over LoRa** (always by radio). Changing it,
  or your name, says `/hello` again to the Stumps you're on.
- **Rescue start:** a Stump that says so (`/node.json` `"mode":
  "rescue"`) gets a line on its screen: no radio, chain or plugins for
  now, Wi-Fi chat works.
- **Renames followed:** when someone changes nick (the poll's new
  `"renamed"`), their private conversation, and a block, follow the new
  name.
- **A busy Stump** (`503`, Stump 1.0.0-beta.24's connection limits): FireFly
  waits 2 s and asks again, instead of calling it offline or losing a line.

### Checked against Stump 1.0.0-beta.22 and beta.23
Nothing a client reads changed (beta.22 and beta.23 are about a computer
Stump's storage and stopping). Computer Stumps now accept uploads, so
FireFly's Files tab can upload to a Stump on a Mac or PC:
`engine-tests/run_real_upload.py <SandBox_Stump_On>` sends FireFly's own
upload request to a real one (a name with accents included), finds the
file in FireFly's list, and checks that a too-big one gets the 507 answer
FireFly shows. The Network tab's traffic counter now goes up to GB.

### 0.5.0-beta.9: a Stump is kept by who it is, not where it is (Stump 1.0.0-beta.21)
A home router moves a laptop Stump from address to address; FireFly kept
the old one. Now a Stump is kept by its identity (its LXMF address) and
found on the Wi-Fi by asking:
- **Find Stumps asks the network** (`STUMP?` on UDP 4244, as soon as it
  opens): every Stump that answers is listed with its name, wherever the
  router put it. The ones those Stumps heard (`/stumps.json`) are listed
  too, and the old address sweep still finds older Stumps.
- **A Stump that stops answering is found again** by its identity and
  followed to its new address: same entry, same history, a line saying
  "found macOS at its new address", and the line that failed is sent
  there. An open chat screen follows it.
- **Wi-Fi when it answers, LoRa when there's a radio:** a Stump known only
  over LoRa that answers on the Wi-Fi gets a Wi-Fi entry (the Stumps tab
  asks when it opens), and with no radio its LoRa screen looks for it on
  the Wi-Fi by itself.
- The `stump.node` beacon's new fifth item (its web addresses) is read.
- Checked against a real computer Stump (1.0.0-beta.21, its own
  `stump_pc.py`): `engine-tests/run_real_discovery.py <SandBox_Stump_On>`.
  It found two things, both fixed: when a Stump's answer names no address
  on the phone's network, FireFly now tries the address it answered from
  (with its port) before the others, and keeps the first that answers as
  that Stump; and a saved Stump keeps its name ("macOS") when it is
  reopened or followed, instead of showing "Stump Wi-Fi (address)" until
  its first poll.
- **Hybrid, as Stump 1.0.0-beta.21 now means it** (everyone reads every
  room and writes in `#main`; writing elsewhere takes Verify): a line
  written elsewhere before Verify is kept, marked "not sent: only verified
  people write in this room", and a tap sends it again once verified; a
  private message fails with the same reason. In a closed (`mandatory`)
  Stump the room stays empty with the Stump's topic saying why, and fills
  in after Verify.
- **Version on screen:** Settings › About and the start screen show the
  version and build number ("0.5.0-beta.9 (build 25)").
- **Which RNode:** the Network tab's radio section names the RNode in use
  ("RNode EA69", its Bluetooth name; a USB device's name; a Wi-Fi host).
- **Use, under Paired RNodes:** switch to another paired RNode at once (it
  is then tried first from then on); the one in use says "in use".

### 0.5.0-beta.8: home Wi-Fi is not a Stump's hotspot (from the Stump team's notes)
On home Wi-Fi with no radio, the "Stump over Wi-Fi" box offered the
gateway (the router) as "the Stump you're next to"; sends went there and
came back as a bare "send failed (-1)".
- **An address is a Stump only once it says so.** The gateway is asked
  `/node.json` first: a Stump shows its name, anything else is called
  your router. **Connect** checks too (port 80, then 8080) and, when no
  Stump answers, says what's there instead ("connection refused", "a web
  page, but not a Stump") with a shortcut to Find Stumps.
- **The same Stump typed at a new address replaces its old entry** at
  once, without waiting for the first poll.
- **Real reasons instead of -1:** a line that never reached the node is
  drawn "not sent (the Stump didn't answer)" with the reason beside it;
  "too fast" now comes only from the node's own ⧗. An offline Wi-Fi Stump
  says why in its header.
- **Verify** ends at once when `/auth` can't be sent, with the reason.
- **No radio and no Wi-Fi entry yet:** the LoRa side offers **Find over
  Wi-Fi**.
- FireFly still keeps no cookies, so a Stump knows it by its address and
  the one-button `/auth` keeps working; the browsers' visitor ID
  (`stump_v`, Stump 1.0.0-beta.19) is left for later.

### 0.5.0-beta.7: a Stump that moved, a phone with no radio (field fix)
Reported with a Stump on a Mac and a phone without its RNode: the Stump
showed "LoRa + Wi-Fi", but `/join` did nothing, Verify never finished,
and every line came back "not sent (too fast)". The router had given the
Mac a new address, so the saved Wi-Fi entry was offline, and the entry
fell back to its LoRa side, which had no radio to send with.
- **A Stump found at a new address replaces its old entry** (matched by
  the LXMF address its poll gives).
- **The merged entry opens what can reach the Stump:** Wi-Fi while it
  answers, LoRa only when the radio (or another path) can carry it, else
  Wi-Fi.
- **A LoRa side with nothing to carry it says so** ("No radio: nothing
  can be sent over LoRa"), with **Open over Wi-Fi** when the Stump has a
  Wi-Fi entry.
- **Verify** fails at once over LoRa with no way to the Stump, and gives
  up after its window if nobody answers, instead of "verifying" forever.
- **Failed lines say why:** "not sent (too fast)" only when the node
  refused it (`⧗`); "not delivered" when LoRa couldn't deliver it.
- `/rooms`: `[LoRa]` read where `rrc.py` puts it, after the topic.

### 0.5.0-beta.6: following Stump 1.0.0-beta.19
Checked against the beta.19 CLIENT_QUICKSTART and `barkeep.py`.
- **Radio settings checked against the Stump's.** A Stump's `/node.json`
  now gives its radio (frequency, bandwidth, SF, CR). When FireFly's
  differ, the node screen says so in red, with the Stump's settings and
  **Use these** (TX power stays yours). A mismatched radio doesn't error:
  it just never hears anything, so this was the hardest thing to notice.
- **The node's radio state** (from the poll's `hw`) shows under its name:
  on, connecting, problem, or no radio.
- **`[LoRa]` rooms:** a Stump on a computer with its own Heltec may mark
  `#lora-mesh` `[LoRa]` in `/rooms`; it's read as the room's tier now,
  not glued to its topic.
- Already right, checked: `#lora-mesh` shared between Stumps (lines as
  `nick@thatstump`); FireFly never titles its messages `#lora-mesh` or
  `#wifi-mesh` (that would mark it a Stump); its HTTP client keeps no
  cookies, so a Stump still knows it by its address and `/auth` works as
  before; in `hybrid`, FireFly over LoRa now counts as verified by its
  signature alone, because it announces itself.

### 0.5.0-beta.5: following Stump 1.0.0-beta.4
Checked against the beta.4 CLIENT_QUICKSTART and `rrc.py`. Works with
older nodes too: the Stump tests run against both.
- **`#lxmf` is `#lora-mesh`.** FireFly keeps the name the node uses and
  treats `lxmf`, `lora` and `lora-mesh` as one room (and `chained`/`mesh`
  as `wifi-mesh`). It never renames a room itself: on an older node
  `#lxmf` is still the real room. The landing room is learned from the
  node's own `→ #room`, and history kept under `#lxmf` moves to
  `#lora-mesh` once the node uses the new name.
- **`⧗` for lines too fast** (3 at once, then one every 2 s): the line or
  DM it refused shows **not sent**, and a tap sends it again. Over Wi-Fi
  the reply names the exact line; over LoRa it's the last thing sent
  within a minute. Voice notes and commands as before.
- **Billboard:** `429 rate_limited` (3 posts a minute) says to wait a
  moment. A search box on the Board, and on Files a search box plus
  video / music / documents / pictures / other chips (`?q=`, `?cat=`;
  applied by FireFly too on older nodes, accents ignored).
- **Chains:** people on other Stumps (`rod@LaBuche`) are listed and
  reachable like anyone; the Stumps themselves are grouped last with 🪵.
- **Find Stumps** also tries port 8080, where a Stump on a computer may
  serve, at the gateway and in the network search.

### After 0.5.0-beta.4 (tests and docs)
- Stump's Tools page now has a folder per tool, and `/tools.json` gained
  `sections`; the FireFly apps live in `tools/firefly/` and their `url`
  names that folder. The update offer already downloads from the `url` as
  given, so nothing changed in the app. The test node serves the new shape
  (and the old flat one), and the docs point to `tools/firefly/`.

### 0.5.0-beta.4: `/node.json` as the Stump team shipped it
- Find Stumps reads `/node.json` exactly as it landed on Stump `main`
  (next release): name, LXMF address, `release` (firmware), hotspot
  address, `chain` (`null` unless chained), and `features`. The card now
  shows the release and what the node offers, and says so in red when a
  node has chat switched off.
- A `/node.json` whose name or LXMF address is `null` (no name set, LoRa
  off) is still read as one. A beta.2 node answers `/node.json` with its
  home page (HTML); that falls back to `/chain.json`, as before.

### 0.5.0-beta.3: Find Stumps
A **Find Stumps** button on the Stumps tab: every way to reach a Stump from
where you stand, best link first. Nothing in it changes anything on a node:
it never polls the chat, so no one sees a `✓ guest` join while you look.
- **This Wi-Fi:** what answers at the gateway of the Wi-Fi you're on (a
  lone Stump's `192.168.4.1`, or `192.168.n.1` along a chain), with its name
  and chain role, and how steady the link is (five small round trips:
  median time and how many were lost). A plain router is said to be one.
- **Stump hotspots nearby:** a Wi-Fi scan, strongest first, with bars from
  the signal. Hotspots you've used as Stumps, the firmware's default name,
  or names with "stump" in them are marked; others are "maybe". **Join**
  connects that hotspot for FireFly only (Android asks once), checks that
  a Stump answers, remembers the name, and opens it; **Leave** gives the
  phone's Wi-Fi back. In a chain, that's how you pick the closest Stump.
  Android 8–9: join in the system settings instead. Scanning asks for the
  Nearby devices permission (location before Android 13), only when you
  tap Scan.
- **On this network:** a Stump that joined the same router as you. FireFly
  asks each address of your /24, 32 at a time (a few seconds), and lists
  the Stumps that answer, steadiest first.
- **Heard on LoRa:** hops, and RSSI/SNR of the last message from each Stump,
  with bars from the SNR.
- **One entry per Stump.** A Stump you reach over LoRa and Wi-Fi is one row
  on the Stumps tab ("LoRa + Wi-Fi"), matched by the LXMF address its Wi-Fi
  poll gives; it opens over Wi-Fi while that answers. Hide and Remove act
  on both.
- Identification asks `/node.json` first (name, version, LXMF address,
  chain), requested from the Stump team for exactly this, then
  `/chain.json` (beta.2), then `/files.json` or `/billboard.json` (older).

### 0.5.0-beta.2: following Stump 1.0.0-beta.2 (chained Stumps)
- **The Stump's address is your Wi-Fi's gateway.** Chained Stumps each
  serve their own range (`192.168.5.1`, `192.168.6.1`…), so "Stump over
  Wi-Fi" now fills in the gateway of the Wi-Fi you joined, and says which
  it is. `192.168.4.1` (or your saved default) is only used off Wi-Fi.
- **Chain status:** a Stump that's part of a chain says so under its name
  ("first Stump of a chain" or "in a chain of Stumps"), from
  `GET /chain.json`.
- **Beacons with more fields** (later firmware may append them) are read
  by their first four, as before; now tested.
- Big transfers relayed onto LoRa stop at 48 KB per link every 10 minutes.
  Nothing to change: a voice note (≤16 KB) and a propagation sync fit.

### 0.5.0-beta.1: following Stump 1.0.0-beta.1
Checked against the Stump beta's CLIENT_QUICKSTART and `barkeep.py`. Every
change also works with an older node, and the tests run both.
- **Billboard posts answered as data.** FireFly asks for JSON
  (`Accept: application/json`): `201` with the post's id, `400
  title_required`, or `507 storage` ("the node couldn't save the post"). An
  older node still answers 303 whatever happened, and FireFly still proves
  the post by reading the board back.
- **Uploads answered as data**: the stored (sanitized) name, your credit
  balance, slot deliveries, and an error token (`no_card`, `full`,
  `interrupted`…) shown in the app's language instead of the node's sentence.
- **Filenames:** on the hotspot the name still goes as raw UTF-8, which
  every node reads. Over HTTPS it now goes percent-encoded with
  `X-Filename-Encoding: url` (accents were dropped before).
- **Plugins:** a node's plugin commands (`/plugins.json`) show as `!roll`,
  `!8ball`… chips above the room box; a tap puts the word in the box.
  stumpid and fservbot are left out, since their commands are slash only.
- **`⧗` now means "slow down" for plugin commands too.** It names neither,
  so it now fails a pending voice note only if the note was the last thing
  sent, within the last minute. After a burst of `!roll` it shows in the
  room instead of failing a note you sent earlier.
- **Update from a Stump:** when a node's `/tools.json` carries a newer
  FireFly for Android, the node screen offers it. **Get it** saves the APK
  where you choose, and **Install** hands it to Android's installer (Android
  asks once to let FireFly install apps). It installs over yours only if it's
  signed with the same key. Versions compare as the node compares them, so
  `0.5.0` beats `0.5.0-beta.1`.
- Tags with a suffix (`v0.5.0-beta.1`) publish as GitHub pre-releases.

### 0.4.1: local-network discovery and stuck message states fixed
- **Wi-Fi peers are found again.** "Local network" (Reticulum's AutoInterface)
  failed on any network with IPv6, which is nearly every Wi-Fi: Android's
  Python has no `socket.if_nametoindex`, and the fallback asked Reticulum's
  netinfo, which on Android needs that same function and returned nothing
  ("required argument is not an integer"). FireFly now asks the C library
  directly, then `/sys/class/net`. Found by the new CI;
  `engine-tests/run_autointerface.py` now checks the lookup on every interface.
- **Messages no longer get stuck at "sent" or "sending".** A message left at a
  propagation node could show "sent" forever although the node had it (and
  the friend received it), and a direct message could stay at "sending" the
  same way: the outbound monitor could write over the final state if it had
  listed the message just before delivery. It now only writes while the
  message is still in flight. Seen about once in 16 runs of
  `run_propagation.py`; `run_outbound_race.py` forces the timing.
- Engine tests and a debug build run on every push and pull request.

### 0.4.0: Board and Files, over a Stump's Wi-Fi
- **Board tab** on a Stump joined over Wi-Fi: the node's billboard from
  `GET /billboard.json`, newest first. Tap a post to open its details. Each
  shows its signature, when it was posted, and how long until it expires (72 h).
  Write one with a title (80 characters, required) and details (600,
  optional). `POST /post` answers 303 whatever happened, so FireFly reads the
  board back and only says "Posted" once the post is really there. A post
  the node quietly drops is reported as such.
- **Files tab**: the shelf from `GET /files.json` with size, kind and credit
  cost. Tap a file to save it where you choose (streamed straight to the
  file, any size, with progress). **Bring a file** uploads one with
  `POST /upload`, with the cost shown first and an optional slot code for a
  regulated upload. The node's own answer ("Uploaded. Your balance: 4") is
  shown as it gives it; 400/404/500/503/507 each get a plain reason. One
  transfer at a time, and it carries on if you leave the screen.
- **Accented filenames upload intact.** The node reads `X-Filename` as UTF-8
  and doesn't URL-decode it, but Android's HTTP stack refuses non-ASCII
  header values. So on the node's hotspot FireFly writes the upload request
  itself, on a socket of the Wi-Fi network, with the name as real UTF-8:
  `Fête de l'été.pdf` is stored as the node would store it from any
  correct client. (Over HTTPS, accents are dropped from the name instead.)
- Both tabs appear only when the node offers the feature (a 404 "not
  offered on this node" hides the tab) and only for Wi-Fi: neither rides
  the mesh.
- **Fix:** removing or hiding a Wi-Fi Stump while a poll was in flight could
  write the node straight back. A closed session now drops late answers.
  The test suite caught this intermittently.
- **Release build:** your own signing key from `keystore.properties` or
  environment variables, arm64 only (about half the size of the debug APK).
  See [docs/RELEASE.md](docs/RELEASE.md), including how to move a phone from
  the debug build without losing its identity.

### 0.2.8: deleting a conversation deletes all of it
- Deleting a conversation now removes the contact too (name, saved, last
  heard), not only the messages and voice notes; it no longer lingers under
  "Heard on the network". Anything still being sent is cancelled. They can
  still write (a new conversation), and reappear under "heard" if they
  announce again.
- Long-press someone under "Heard on the network" to remove them the same way.

### 0.2.11: aligned with the Stump node (checked against its CLIENT_QUICKSTART)
- **Tier refusals:** `⊘ #vip minted — …` is understood; FireFly stays put.
  Before, the room name in that line could be taken for a successful move.
- **Tokens:** `= #room` (already there) and `? /cmd` (no such command) are
  read as tokens; the old sentence-matching stays only for older nodes.
- **Room titles:** pushed batches name their room in the LXMF title; room
  lines are filed under that room, so a line sent just as you `/join`
  elsewhere stays in the room it was said in. A re-landing takes its room
  from the title too.
- **♪ labels** round tenths half up, as the node does (2.25 s is `♪ 2.3 s`);
  the length under each voice note uses the same rule.
- **Voice limits** exactly as the node's: Codec 2 up to 15,000 ms, Opus up
  to 15,100 ms, at most 16 KB through a Stump.
- **Refusals:** only a voice-note refusal ("Opus"/"Codec"/"0.6 to 15")
  fails a pending voice DM; other sentences (help, notices) don't.
- **Node name:** a Wi-Fi session takes the node's name from the poll's
  `node` field. (Merging the LoRa and Wi-Fi entries into one is next.)
- The fake Stump in engine-tests now follows the node's current protocol.

### 0.2.10: Opus in Stump voice DMs too
- Voice DMs through a Stump node follow the same voice setting as every
  chat: Opus at 8 kbit/s by default (Automatic judges the link to the node:
  LoRa gives Codec 2, Wi-Fi Opus). Over LXMF: `/msg <nick>` with the Ogg
  Opus file in the audio field; over HTTP: `POST /rrc/voice?mode=16`.
- The `♪ 5.0 s` label of an Opus note comes from its Ogg pages (last granule
  minus pre-skip), as a node relaying Opus would compute it.
- The node must relay Opus: accept mode 16, label it from the Ogg pages,
  allow an HTTP body of at least 14 KB, pass the field on unchanged. A node
  not yet updated answers with a sentence ("unreadable voice note (Codec 2
  expected)"); FireFly fails the note with those words instead of leaving it
  pending (engine-tests/run_stump_legacy.py).
- Tested against a fake Stump relaying Opus: sending and receiving Opus
  voice DMs over LoRa and Wi-Fi, labels, playback, byte for byte.

### 0.2.9: Opus by default on every link, at 8 kbit/s
- Voice notes default to Opus on every link, LoRa included: voice quality
  first. Automatic (Codec 2 over LoRa) and the Codec 2 modes stay available
  to save airtime. Installs on an earlier default move to Opus; a chosen
  mode is kept.
- Opus at 8 kbit/s, constrained VBR: a 15 s note is about 13.4 KB (two test
  voices: 13.4 and 12.9 KB), still inside a Stump's 15 KB propagation
  limit; 9 kbit/s would not be. On LoRa at SF8 a full 15 s note is about 47
  packets, roughly a minute of airtime; a 5 s note about 20 s.
- Stump voice DMs stayed Codec 2 here (the node refused Opus); see 0.2.10.

### 0.2.8: blocking
- **Block** (long-press in Chats or "Heard on the network", or ⋯ in a
  conversation, after a confirmation): deletes everything with that person,
  then ignores their announces and messages on this phone, through LXMF's
  own ignore list too (so also messages collected from a propagation node).
  Sending to them is refused; their address can't be added as a contact.
- Needs only the address: works for someone you'll never hear again, and
  Settings > Blocked people can block a pasted address. Unblock there;
  unblocked people come back only if they announce or write.
- Local only: they can still transmit, this phone ignores it. Stump room
  nicknames aren't covered (the node relays them).
- **Delete** stays as it was: everything, including the contact; they come
  back as new if they announce or write.
- Tested with the second phone announcing throughout: stays gone while
  blocked, returns with no history after unblocking.

### 0.2.7: natural voices with Opus
- Voice note quality now defaults to **Automatic**: Opus (natural voice)
  when the phone's first hop toward the person isn't LoRa (Wi-Fi, TCP, the
  local network), Codec 2 1200 when it is, or when the note would reach a
  propagation node over LoRa. Opus or any Codec 2 mode can be forced.
  Installs on the old 1200 default move to Automatic; other choices stay.
- Opus: 16 kHz, 6 kbit/s, 60 ms frames, as a standard Ogg Opus file in
  LXMF's `AM_OPUS_OGG` field (Sideband's format). About 680 B/s, so a 15 s
  note is ~10 KB: inside a Stump's 15 KB propagation limit. Opus notes from
  Sideband and others play, mono or stereo.
- Recording is now 16 kHz first; Codec 2 still gets 8 kHz from it.
- Stump voice DMs stay Codec 2 (the node refuses Opus): the chosen Codec 2
  mode, or 1200 under Automatic or Opus.
- libopus 1.5.2 (BSD) is vendored in `app/src/main/cpp/opus`, trimmed of its
  unused deep-learning model data, and built by the same CMake file.
- Tested: FireFly's files pass `opusinfo` without warnings and play in the
  reference `opusdec`; reference `opusenc` files (mono, stereo) play in
  FireFly; exact length round trips at 8 and 16 kHz; an Opus note across
  two radios byte for byte; Automatic picks Codec 2 over LoRa and Opus over
  TCP; Stump DMs stay Codec 2 even with Opus forced.

### 0.2.6: voice DMs through Stump
- Hold to talk in a Stump DM, over LoRa or Wi-Fi, as the Stump quickstart
  describes: over LXMF, `/msg <nick>` with the audio field; over HTTP,
  `POST /rrc/voice`, with incoming notes fetched from `/rrc/voice?id=`.
  Same quality setting as plain chats.
- A note you send shows pending until the node's `[DM] <you>: ♪ 5.0 s`;
  it never says who it went to, so it's matched to the pending note of that
  length. `⊖ nick` and the new `⧗` (too many notes too fast: one per 5 s,
  30 an hour) fail it with the reason. Notes under 0.6 s or over 15 s are
  refused before sending. Your own notes stay playable on your side.
- Voice DMs play in DM purple; rooms stay text, as on the node.
- Tested against a fake Stump implementing the quickstart's voice section
  on both transports. On LoRa a short one-packet note can overtake a long
  one; FireFly matches each confirmation to the right note regardless.

### 0.2.5: deleting
- **Conversations:** long-press in Chats, or ⋯ > Delete conversation. Every
  message and voice note with that person is deleted from the phone and
  anything still being sent is cancelled (including a message still
  waiting for a path, or one that would have fallen back to a
  propagation node). They stay known, so they can still write.
- **Stumps, hide:** long-press > Hide, or ⋯ > Hide. Off the list, history
  kept; stays hidden while it beacons; comes back if it messages you.
  "N hidden · show again" at the bottom of the list.
- **Stumps, remove:** long-press > Remove, or ⋯ > Remove, after a
  confirmation. Deletes its rooms, DMs, raw traffic and peer entry. A
  node still on the air appears again later as a new, empty entry; one
  that changed identity never comes back under its old address.

### 0.2.4: settings, one rule
- A section with a Save button changes nothing until Save (Discard puts
  back what's saved): Your name, Radio channel, Announcing, Other links.
  A section without one applies each tap at once: LoRa radio, Offline
  messages, Voice note quality.
- Every change is confirmed by the same short banner, shown only once the
  engine has taken it ("Saved · the radio retunes in a moment"), or saying
  it failed. Identity backup and restore use it too.
- Save is disabled until something changed; invalid values (frequency out
  of 137–1020 MHz, a TCP peer without a port…) say what's wrong.
- Fixed: typed but unsaved values were wiped whenever a message arrived.

### 0.2.3: voice note quality
- Settings > Voice note quality: Codec 2 700C, 1200 (default), 1600, 2400
  or 3200, with the speech per LoRa packet and a 15 s note's size shown
  for the choice (3200: 0.7 s per packet, 6 KB per 15 s note, well inside
  a Stump's 15 KB propagation limit). Received notes play in any Codec 2
  mode, whatever is chosen.
- Tested: every mode on recorded speech through the app's own Codec2.kt;
  a 3200 note across two radios byte for byte.

### 0.2.2: fast stamps
- Proof-of-work stamps (for messages left at a propagation node, and for
  recipients who ask for one) are computed ~200x faster with the same
  result: a stamp is valid when SHA-256(work block + stamp) is under the
  target, and SHA-256 reads its input in order, so the 250-750 KB work
  block is hashed once and each attempt only hashes its own 32 bytes on a
  copy of that state. LXMF re-hashes the whole block on every attempt. A
  cost-16 stamp now takes a few hundredths of a second on a desktop
  (several seconds before), so a fraction of a second on a phone, and
  correspondingly less battery. Stamps are checked by LXMF's own validator
  and accepted by an upstream propagation node in the tests.
- What's left of the wait before "✓ node" is LXMF's job loop (a few
  seconds) and the LoRa transfer itself.
- Replaces 0.2.1's multiprocessing probe and single-core fallback: this is
  faster than LXMF's multi-process path would be anyway.

### 0.2.1: propagation nodes (offline messages)
- **Finds a Stump's propagation node without waiting for its announce.** A
  propagation node announces rarely (once when switched on). Its address
  is on the same identity as the Stump's LXMF address, so FireFly derives
  it and asks the network for it as soon as it recognises a Stump, and
  every 5 minutes until found.
- **Chooses well:** automatic mode prefers a Stump's node over a nearer
  one (Stump nodes don't peer yet, so everyone around a Stump must use
  the same node), and can be pinned to a heard node or to an address
  pasted from a node's `/admin`, or switched off. Network > Offline
  messages > Choose…; a Stump's screen offers "use it".
- **Leaves messages at the node straight away when the recipient has no
  path** (they're offline), instead of burning airtime on direct attempts
  first; direct-then-node fallback stays for everyone else.
- **Respects the node's per-message limit** (15 KB on a Stump, read from its
  announce): an over-limit message is refused with the reason.
- **Stamps work on Android:** LXMF makes stamps on Android with Python
  multiprocessing, which Chaquopy's Python lacks (superseded in 0.2.2).
- **Collects until empty:** a Stump answers each sync with at most ~14 KB;
  FireFly syncs again while rounds bring messages (up to 5). One sync runs
  at a time: requests made meanwhile (radio up, app opened, the button)
  join it rather than starting a second one that could end the first
  early. A failed round (no path yet, link failed) is retried after 10 s,
  then 30 s.
- **Syncs when it matters:** when the radio comes online, when the app is
  opened (at most every 2 min), when a node is chosen, and every 30 min
  (installs that kept the old 60 min default are moved to 30).
- Stump chat (`/auth`, room lines, commands) never goes through a
  propagation node. Received messages that came from a node say "via node".

### 0.2.0: voice notes
- Hold to talk in any LXMF chat, release to send, slide left to cancel.
  Up to 15 s. Tap a voice note to play it.
- Codec 2 at 1200 bps: 150 bytes per second, so about 1.9 s of speech fits
  one LoRa packet (sent opportunistically) and a full 15 s note is 2,250 bytes
  (about 8 packets, over a link, automatically).
- Sent in the standard LXMF audio field (`FIELD_AUDIO`, mode
  `AM_CODEC2_1200`), the format Sideband uses, so notes should play across
  the two. Received notes in any Codec 2 mode play (700C to 3200); Opus notes
  show as "format not supported yet".
- Not in Stump rooms or Stump DMs: RRC is text. That needs a relay on the node.
- Codec 2 1.2.0 (LGPL-2.1) is vendored in `app/src/main/cpp/codec2` and built
  as its own shared library; see its README there. Building now needs the
  **NDK and CMake** (Android Studio > SDK Manager > SDK Tools).

### 0.1.2
- The room shown is the room the node actually has you in, after you go quiet
  on the mesh. The node drops a silent mesh peer from its room after
  `MESH_PEER_TIMEOUT` (5 min), and the next message lands them in `#lxmf`
  with only `✓ ~you`, no `→ #room`. FireFly now reads that: a `✓` of your own
  that isn't the one following a `/join` means you were re-landed. Your
  message sent at that moment is filed in the room it really went to.
- A node reply naming the room you just asked to join ("tu es déjà dans
  #lxmf") also corrects the room, in any language.

### 0.1.1
- Notifications open the right chat or Stump DM (and clear when you open it).
- Settings shows what the RNode reports it is using ("on air now"), and
  "applying…" while it retunes. The Stump defaults line no longer looks like
  the current state.
- FireFly announces just before the first message to someone in a session,
  so the recipient can verify its signature ("sender signature not verified"
  on the handheld came from the message beating the announce).
- Local-network discovery works on Chaquopy's Python (no `socket.if_nametoindex`):
  AutoInterface's own fallback is used through a subclass. An interface that
  fails after registering is removed, so it's no longer listed twice.

## How it's put together

```
Jetpack Compose UI (Amber theme)              Kotlin
        │  JSON over Chaquopy (engine/Engine.kt)
firefly/api.py ─ core.py ─ stump_session.py   Python: the FireFly handheld engine
        │           │            └ MeshPipe (LXMF) / WifiPipe (HTTP)
   official rns + lxmf, unmodified
        │
RNodeInterface (Reticulum's desktop driver, byte for byte)
        │  pyserial-shaped port: firefly/androidlink.py
RadioLink (Kotlin): BleRadioLink (Nordic UART, bonded)  |  UsbRadioLink (usb-serial-for-android)
```

**Why this shape.** Reticulum's Android RNode driver needs Kivy libraries
that don't exist under Chaquopy. The prototype worked around that by
configuring the RNode from Kotlin and feeding Reticulum raw KISS over a
loopback socket, with about 1,800 lines of patches to Python's socket, os,
subprocess, ctypes and signal modules. FireFly instead hands the reference
driver a Kotlin `RadioLink` as its serial port. Detection, the firmware check,
radio configuration and its verification, and RSSI/SNR/battery statistics
all stay Reticulum's own code.

**The one shim.** (Plus one subclass for local-network discovery; see 0.1.1.) Reticulum's desktop driver refuses to construct on Android.
`allow_desktop_rnode_driver()` (10 lines) makes that single check answer
"not Android" only when it is asked from inside that constructor. The rest
of Reticulum keeps its Android behaviour; a test asserts this. That is the
only patch FireFly applies to Reticulum.

**No startup crashes from optional parts.** Reticulum exits the process if an
interface in its config file fails. So the config has no interfaces;
AutoInterface, TCP peers and the radio are attached at runtime, each in its
own try/except.

**Signals without patches.** Python is started on a dedicated thread, and the
engine starts on that same thread, which makes it Python's main thread, where
Reticulum and LXMF are allowed to install their signal handlers.

**BLE done properly** (`radio/BleRadioLink.kt`): one GATT operation at a time,
with each write waiting for its callback; chunks sized from the negotiated
MTU (up to 517); notifications enabled before the link reports ready; bonded
devices only, failing fast with a readable reason; high connection priority
while attached. The prototype fired writes on a 10 ms timer.

## Stump, per CLIENT_QUICKSTART.md

- **One protocol, two pipes.** The same `StumpSession` runs over LXMF and over
  `/rrc/poll` + `/rrc/send`. `/auth`, rooms and DMs are one code path.
- **`/auth` identity.** Standard Reticulum identity (128-hex key). The
  signature is over the nonce's ASCII text. On the mesh the answer fits in one
  packet (263/295 bytes, empty title, opportunistic, never propagated), and an
  expired challenge restarts on its own.
- **Rooms.** Mesh users land in `#lxmf` and Wi-Fi users in `#main`. The room is
  tracked from the node's replies (`→ #room` or the `room` field), never
  assumed. Leaving the DM view is local only, with no `/join`.
- **DMs.** A DM shows as *pending* until the node's `[DM] <you>: text` echo
  arrives, or fails on `⊖ nick`. Incoming DMs are threaded by sender. Your
  exact nick is learned from the first confirmation.
- **Wi-Fi details.** Polling runs at 2 s, doubling on failure up to 30 s, with
  0–30 % jitter. A 404 means "not offered on this node". HTTP is bound to the
  Wi-Fi network, so a hotspot without internet still works while mobile data
  stays on.
- **Beacons.** Stumps are recognised from `stump.node` beacons, or asked on
  demand when an LXMF announce is heard.
- **Announces.** FireFly announces whenever a new interface comes online, so a
  node learns your nick from a real announce and not only a path response.
  This was a real bug that the tests caught.
- **Billboard and files** (Wi-Fi only): `/billboard.json` and `/post`, checked
  by reading the board back; `/files.json`, `/download?f=` (UTF-8
  percent-encoded) and `/upload` (raw body, UTF-8 `X-Filename`, optional
  `X-Hash`), streamed by the app, never through the engine.
- **Look.** The palette and typography are the quickstart's Amber theme,
  verbatim, and the app ships in English, French and Spanish.

## Verified (desktop, `engine-tests/`) and on hardware

Run from the repository root with `pip install rns==1.5.5 lxmf==1.2.0`:

| Test | What it proves |
|---|---|
| `python3 engine-tests/run_radio.py` | Reticulum in **Android mode** drives simulated RNodes through the RadioLink contract. It covers USB online with Stump defaults, live retune, cable pull, **BLE** online over a chunked and laggy link, BLE out-of-range then automatic reconnect, unpairing detaches the radio, and old firmware reported without crashing. |
| `engine-tests/voice/` (Kotlin + JNI) | The real `Codec2.kt` and native library on recorded speech: 150 B/s, 1.88 s per packet, 15 s = 2,250 B, speech contour preserved; the resampler suppresses 48/16 kHz aliasing by 60–68 dB. See its README. |
| `python3 engine-tests/run_air.py` | Two engines in two processes, **phone A on USB and phone B on BLE**, over a simulated LoRa channel: announces heard, delivery proofs, replies, RSSI/SNR, and a 700-character message over a Reticulum link through both radios. |
| `python3 engine-tests/run_autointerface.py` | Local-network discovery on a Python without `socket.if_nametoindex` (as on the phone), listed exactly once. |
| `python3 engine-tests/run_stamps.py` | FireFly's stamp search through LXMF's own Android entry point: valid propagation and delivery stamps at costs 8-16 by LXMF's validator, timing, and cancellation the way LXMF cancels. |
| `python3 engine-tests/run_propagation.py` | A real upstream LXMF propagation node on a Stump's identity (stamp cost 16, 2 KB limit). Found without its announce; stamps with multiprocessing unavailable; an offline friend and a never-online contact collect by sync; no-path messages go to the node at once; over-limit refused; multi-round sync with no duplicates; Stump chat and "off" never use the node; pin and automatic choice. |
| (also in run_stump / run_air) | Hide keeps history and stays hidden while beaconing; remove deletes everything and a node on the air returns empty; deleting a conversation removes messages and voice notes and a new one starts normally. |
| `python3 engine-tests/run_reach.py` | The Oct 18 field report: no radio, a Stump's saved Wi-Fi address gone stale. The merged entry doesn't open LoRa, the LoRa side offers Wi-Fi, Verify fails at once, and the Stump found at its new address replaces the old entry. |
| `python3 engine-tests/run_discovery.py` | Five fake Stumps and a plain web server on loopback addresses (port 80, run as root): a sweep of the /24 finds exactly the Stumps in about a second, reads `/node.json` in the shape barkeep.py builds (nulls included), falls back to `/chain.json` when it's an HTML home page and to `/files.json` on older nodes, ranks the slow one last, and never polls the chat. Plus subnet sizes, bars and hotspot names. |
| `python3 engine-tests/run_web.py` | Billboard, file shelf, plugins, apps and chain against the fake node, as a current node (JSON answers: 201/400/507 posts; upload name, balance and error tokens; percent-encoded and Latin-1 names; `/plugins.json`, `/tools.json` and version order; `/chain.json`; beacons with extra fields) and as an older one (`FAKE_STUMP_OLD=1`: posting proven by refetch, a dropped post detected, 80/600 limits, `+` `$` and line breaks intact, UTF-8 download names, a UTF-8 `X-Filename` written on a raw socket stored exactly (with the node's filename rules), the node's 400/503/507 answers, and features switched off reading as "off"). |
| `FAKE_STUMP_OLD=1 python3 engine-tests/run_stump.py` | The same against a node from before Stump 1.0.0-beta.4: `#lxmf`, no line limits. |
| `python3 engine-tests/run_stump.py` | A fake Stump over the mesh (LXMF) and over Wi-Fi (HTTP), covering everything in the Stump section above. |

Kotlin was checked with `kotlinc` 2.2.10 for syntax only. An APK has not been
built yet: the build environment had no Android SDK.

## Build

Android Studio (Ladybug or newer), JDK 17, and Python 3.13 on the build
machine (Chaquopy needs it; set `firefly.buildPython` in `gradle.properties`
if it isn't on PATH).

    ./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
    ./gradlew assembleRelease      # signed with your key: see docs/RELEASE.md

The toolchain versions (AGP 9.1.1, Gradle 9.3.1, Kotlin 2.2.10, Chaquopy 17,
Python 3.13) are the ones the prototype already built with.

## First run on hardware: what to check

Only a real phone and a real RNode can confirm these. They are listed in the
order most likely to need attention.

1. **Engine start.** Chaquopy starts Python on the `firefly-python` thread,
   and Reticulum's signal handlers install there. If start-up fails, the app
   shows the error text on screen.
2. **BLE pairing (Heltec V3).** Enable Bluetooth on the RNode
   (`rnodeconf <port> -b`), put it in pairing mode, pair from Network >
   Bluetooth RNode, and enter the PIN from its display.
3. **USB line control.** CP210x boards get DTR on, then RTS on (like pyserial).
   ESP32-S3 native USB gets DTR on and RTS off, as the prototype found. If a
   board resets or enters its bootloader on connect, change this in
   `UsbRadioLink.kt`.
4. **AutoInterface on Android.** It is attached at runtime and its failure is
   harmless; the Network tab shows why if it didn't start.
5. **Battery.** A partial wake lock is held only while a radio is attached; it
   can be switched off in Settings.

## Layout

    app/src/main/python/firefly/   engine: api, core, stump_session, stump, radio, androidlink, store, settings
    app/src/main/java/.../radio/   RadioLink, UsbRadioLink, BleRadioLink, RadioLinks, BlePairing
    app/src/main/java/.../engine/  Engine (Chaquopy bridge), EngineService (foreground service)
    app/src/main/java/.../net/     WifiHttp (Stump Wi-Fi, bound to the Wi-Fi network)
    app/src/main/java/.../ui/      Compose screens, Amber theme
    engine-tests/                  desktop tests with simulated RNodes and a fake Stump

## Phase 2

Billboard and files over Wi-Fi (waiting on JSON endpoints from Stump),
attachments, QR identity sharing, the Phosphor/OLED/Paper themes, and a
propagation node picker.

## Credits and licence

Reticulum and LXMF by Mark Qvist (Reticulum License). Codec 2 by David Rowe
and contributors (LGPL-2.1). libopus by Xiph.Org and contributors (BSD). The engine comes from
FireFly for RK3326 handhelds (MIT). usb-serial-for-android by mik3y (MIT).
The palette is LaBuche-Stump's Amber theme. FireFly for Android is under the
MIT licence.
