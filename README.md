# FireFly for Android [APK FILE](FireFly-Android-v0.1.2.apk)

A LoRa-first Reticulum messenger for Android, built for the Stump network.
It speaks standard **LXMF** (so it talks to Sideband, NomadNet, MeshChat and
FireFly handhelds) and understands **Stump nodes**: rooms, DMs, `/auth`,
over the mesh *and* over the node's Wi-Fi, with one identity.

The radio is an RNode on **Bluetooth LE or USB**, found and attached at
runtime. Both run through Reticulum's own RNode driver.

Status: **0.1.2, phase 1 complete.** Built and field-tested: BLE and USB
RNodes, LoRa messaging with the R36MAX handheld, Stump `/auth` over LoRa,
and delivery with the screen locked.

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
- **Look.** The palette and typography are the quickstart's Amber theme,
  verbatim, and the app ships in English, French and Spanish.

## Verified (desktop, `engine-tests/`) and on hardware

Run from the repository root with `pip install rns==1.5.5 lxmf==1.2.0`:

| Test | What it proves |
|---|---|
| `python3 engine-tests/run_radio.py` | Reticulum in **Android mode** drives simulated RNodes through the RadioLink contract. It covers USB online with Stump defaults, live retune, cable pull, **BLE** online over a chunked and laggy link, BLE out-of-range then automatic reconnect, unpairing detaches the radio, and old firmware reported without crashing. |
| `python3 engine-tests/run_air.py` | Two engines in two processes, **phone A on USB and phone B on BLE**, over a simulated LoRa channel: announces heard, delivery proofs, replies, RSSI/SNR, and a 700-character message over a Reticulum link through both radios. |
| `python3 engine-tests/run_autointerface.py` | Local-network discovery on a Python without `socket.if_nametoindex` (as on the phone), listed exactly once. |
| `python3 engine-tests/run_stump.py` | A fake Stump over the mesh (LXMF) and over Wi-Fi (HTTP), covering everything in the Stump section above. |

Kotlin was checked with `kotlinc` 2.2.10 for syntax only. An APK has not been
built yet: the build environment had no Android SDK.

## Build

Android Studio (Ladybug or newer), JDK 17, and Python 3.13 on the build
machine (Chaquopy needs it; set `firefly.buildPython` in `gradle.properties`
if it isn't on PATH).

    ./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk

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

Reticulum and LXMF by Mark Qvist (Reticulum License). The engine comes from
FireFly for RK3326 handhelds (MIT). usb-serial-for-android by mik3y (MIT).
The palette is LaBuche-Stump's Amber theme. FireFly for Android is under the
MIT licence.
