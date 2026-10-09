# FireFly for Android

A LoRa-first Reticulum messenger for Android, built for the Stump network.
It speaks standard **LXMF** (so it talks to Sideband, NomadNet, MeshChat and
FireFly handhelds) and understands **Stump nodes**: rooms, DMs, `/auth`,
over the mesh *and* over the node's Wi-Fi, with one identity.

The radio is an RNode on **Bluetooth LE or USB**, found and attached at
runtime. Both run through Reticulum's own RNode driver.

Status: **0.4.1: local-network discovery and stuck message states fixed.** Phase 1 complete. Built and field-tested: BLE and USB
RNodes, LoRa messaging with the R36MAX handheld, Stump `/auth` over LoRa,
Opus voice notes everywhere, and delivery with the screen locked.

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
| `python3 engine-tests/run_web.py` | Billboard and file shelf against the fake node: posting proven by refetch, a dropped post detected, 80/600 limits, `+` `$` and line breaks intact, UTF-8 download names, a UTF-8 `X-Filename` written on a raw socket stored exactly (with the node's filename rules), the node's 400/503/507 answers, and features switched off reading as "off". |
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
