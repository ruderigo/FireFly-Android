# FireFly client quickstart: interoperating with FireFly

FireFly for Android 0.4.0 · October 2026

A client interoperates with FireFly if it speaks standard LXMF and reads and writes LXMF's audio field the way this guide describes. Nothing here is FireFly-only: it's LXMF, Reticulum, and the Stump node's own protocol.

FireFly is a LoRa-first Reticulum messenger for Android (an RNode over Bluetooth LE or USB) and RK3326 handhelds. It runs the official `rns` 1.5.5 and `lxmf` 1.2.0, unmodified. This guide is for anyone building a client that should message FireFly users, play their voice notes, or send them notes they can play.

**Voice notes in one line:** FireFly sends Opus at 8 kbit/s by default, on every link (LoRa included) and in voice DMs through Stump nodes. Codec 2 is available as a user choice and always plays.

## Checklist

- [ ] Announce `lxmf.delivery` with a display name, at least every 60 minutes
- [ ] Send and accept plain LXMF text: UTF-8 content, empty title
- [ ] Play Opus voice notes, which FireFly sends by default: `FIELD_AUDIO` = `[16, Ogg Opus file]`
- [ ] Play Codec 2 voice notes: `FIELD_AUDIO` (7) = `[mode, raw frames]`, modes 3–9
- [ ] Show a note's length in seconds, computed as in "Seconds" below
- [ ] Send voice notes in one of those formats, 0.6 to 15 seconds
- [ ] For voice DMs through a Stump node: Opus or Codec 2, `/msg <nick>` with the audio field
- [ ] Check your encoder and decoder against the test vectors at the end

## Identity, announces and messages

FireFly is an ordinary LXMF peer: one Reticulum identity, one `lxmf.delivery` address, a display name in its announce, and no delivery stamp required from senders.

**When FireFly announces:**

- Every 30 minutes by default (the user can set 0 to 1,440). That stays under the one-hour window in which Stump nodes keep someone reachable by DM.
- Whenever a new interface comes online (radio attached, TCP connected, local peer found), at most once per 15 seconds.
- Just before its first message to someone in a session, if it hasn't announced in the last 30 seconds. The recipient then has its key and can verify the signature.

**Sending:** text goes opportunistically in one packet when it fits (295 bytes of content), and LXMF switches to a link when it doesn't. A recipient with no known path gets the message left at a propagation node straight away; others get direct attempts first, then the node if those fail.

**Receiving:** FireFly shows the content as UTF-8 and doesn't display titles, so put text in the content. A message whose signature can't be checked yet is shown, marked unverified. Fields other than audio (images, files, telemetry) are listed by name but not shown.

## Voice notes: overview

A voice note is an LXMF message whose fields hold `FIELD_AUDIO` (7) = `[mode, audio]`, with empty content and title. The mode is an integer from LXMF's `AM_*` constants; the audio is bytes.

```python
fields = {LXMF.FIELD_AUDIO: [LXMF.AM_OPUS_OGG, ogg_opus_file]}      # FireFly's default
# or: {LXMF.FIELD_AUDIO: [LXMF.AM_CODEC2_1200, codec2_frames]}
message = LXMF.LXMessage(destination, source, "", title="", fields=fields)
```

The mode decides what the bytes are: a complete Ogg Opus file for mode 16, raw Codec 2 frames back to back for modes 3 to 9. Sideband uses the same field and numbers, so the formats are shared.

| Mode | Name | Bytes per second | Speech in one packet | 15 s note |
| --- | --- | --- | --- | --- |
| 16 | `AM_OPUS_OGG` (FireFly's default, 8 kbit/s) | about 890 | rarely fits | about 13,400 B |
| 3 | `AM_CODEC2_700C` | 100 | 2.8 s | 1,500 B |
| 4 | `AM_CODEC2_1200` | 150 | 1.9 s | 2,250 B |
| 5 | `AM_CODEC2_1300` | 175 | 1.6 s | 2,625 B |
| 6 | `AM_CODEC2_1400` | 175 | 1.6 s | 2,625 B |
| 7 | `AM_CODEC2_1600` | 200 | 1.4 s | 3,000 B |
| 8 | `AM_CODEC2_2400` | 300 | 0.9 s | 4,500 B |
| 9 | `AM_CODEC2_3200` | 400 | 0.7 s | 6,000 B |

"One packet" means LXMF's 295-byte content limit minus about 10 bytes for the field. Longer notes go over an LXMF link automatically; at SF8 and 125 kHz each packet costs about 1.4 s of airtime. A 15 s Opus note at 8 kbit/s is about 47 packets, roughly a minute; the same note in Codec 2 1200 is about 8. Modes 1 and 2 (the 450 modes) left Codec 2 in version 1.2: treat them as unsupported.

**What FireFly sends.** The user picks Opus (the default: every link, LoRa included), Automatic (Opus except over LoRa), or a fixed Codec 2 mode. Every FireFly plays all of them, whichever it sends.

| Setting | Recipient's link from the phone | FireFly sends |
| --- | --- | --- |
| Opus (the default) | Any, LoRa included | Opus (16) |
| Automatic | First hop isn't LoRa (Wi-Fi, TCP, local network) | Opus (16) |
| Automatic | First hop is LoRa; or offline with no propagation node reachable off LoRa | Codec 2 1200 (4) |
| Automatic | Offline, left at a propagation node reached over Wi-Fi or TCP | Opus (16) |
| A Codec 2 mode | Any | That mode |
| Any | A voice DM through a Stump node | The same setting: Opus by default (the node must relay Opus) |

## Opus notes

For mode 16 the audio bytes are a complete Ogg Opus file as RFC 7845 defines it: an `OpusHead` page, an `OpusTags` page, then audio pages. Any standard Ogg Opus file works, and FireFly's files are standard.

| What FireFly writes | Value |
| --- | --- |
| Channels | 1 (mono), mapping family 0 |
| Input rate | 16 kHz; 8 kHz if that's all the microphone gives (recorded in `OpusHead`) |
| Bitrate | 8 kbit/s, constrained VBR (keeps 15 s under 15 KB) |
| Frames | 60 ms (960 samples at 16 kHz) |
| Tuning | `OPUS_APPLICATION_VOIP`, `OPUS_SIGNAL_VOICE`, complexity 10 |
| Pages | About one second of audio per page; end-of-stream flag on the last |
| Pre-skip | The encoder's lookahead, in 48 kHz samples |
| End | The last page's granule position marks the true end: padding is trimmed within the last packet, as RFC 7845 asks |

**Decoding:**

1. Read pages from the first logical stream; ignore any other stream's pages.
2. Check the first packet is `OpusHead` with 1 or 2 channels and mapping family 0; skip `OpusTags`.
3. Decode every audio packet to mono at the rate you'll play (FireFly uses 16 kHz). A stereo file is mixed down by asking the decoder for one channel.
4. Drop the pre-skip from the start, converted to your output rate.
5. Cut the end at the last granule position: keep (last granule − pre-skip) samples at 48 kHz, converted to your output rate.

FireFly's files pass `opusinfo` with no warnings and play in the reference `opusdec`. Files from the reference `opusenc`, mono or stereo, play in FireFly.

## Codec 2 notes

The audio bytes are exactly what the Codec 2 encoder writes for each frame, concatenated in order: no header, no container, no length prefix. FireFly uses [Codec 2](https://github.com/drowe67/codec2) 1.2.0 on 8 kHz mono 16-bit signed PCM; Sideband's Codec 2 notes are built the same way.

| Mode | Codec 2 mode | Samples per frame | Frame length | Bytes per frame |
| --- | --- | --- | --- | --- |
| 3 | `CODEC2_MODE_700C` | 320 | 40 ms | 4 |
| 4 | `CODEC2_MODE_1200` | 320 | 40 ms | 6 |
| 5 | `CODEC2_MODE_1300` | 320 | 40 ms | 7 |
| 6 | `CODEC2_MODE_1400` | 320 | 40 ms | 7 |
| 7 | `CODEC2_MODE_1600` | 320 | 40 ms | 8 |
| 8 | `CODEC2_MODE_2400` | 160 | 20 ms | 6 |
| 9 | `CODEC2_MODE_3200` | 160 | 20 ms | 8 |

**Encoding:**

1. Record mono 16-bit PCM, ideally at 16 kHz (FireFly does, so the same recording serves Opus).
2. Bring it to 8 kHz: low-pass at about 3.6 kHz, then keep every 2nd (from 16 kHz) or 6th (from 48 kHz) sample. Without the filter, sound above 4 kHz folds back into the voice band as noise.
3. Remove DC with a high-pass around 60 Hz, then raise quiet recordings to a peak near 70 % of full scale, at most 4× gain.
4. Encode whole frames and concatenate them. Pad a final partial frame with silence.

**Decoding:** split the bytes into whole frames, decode each in order, ignore trailing bytes that don't make a whole frame, and play the result at 8 kHz mono.

To listen on a computer, save the bytes and use Codec 2's own decoder; name the file `.bit`, because recent Codec 2 tools expect a small header in `.c2` files.

```bash
c2dec 1200 note.bit note.raw
play -t raw -r 8000 -e signed -b 16 -c 1 note.raw
```

## Seconds: how long a note is

A note's length is computed from its bytes, never from a timer or metadata. Every client then gets the same number, the same limits and the same label. Compute in whole milliseconds; round only when displaying.

**Opus:** the last page's granule position minus the pre-skip from `OpusHead`, both in 48 kHz samples, rounded down to the millisecond.

```
t_ms = floor((last_granule - pre_skip) / 48)
```

**Codec 2:** whole frames times the frame length. Trailing bytes that don't make a whole frame count for nothing.

```
t_ms = floor(bytes / bytes_per_frame) * frame_length_ms
```

**Shown as** `♪ 5.0 s`: the eighth-note sign U+266A, a space, the seconds to one decimal, tenths rounded half up (2.25 s is 2.3), with a dot whatever the phone's language, a space, `s`. That's exactly how a Stump node writes it in a DM line (`[DM] <bob>: ♪ 5.0 s`). FireFly shows the same seconds, without the sign, under each note.

| Note | Bytes | Frames | Length | Label |
| --- | --- | --- | --- | --- |
| Opus (test vector) | 3,375 | granule − pre-skip = 240,000 | 5,000 ms | `♪ 5.0 s` |
| Codec 2 1200 | 750 | 125 × 40 ms | 5,000 ms | `♪ 5.0 s` |
| Codec 2 1200 | 751 | 125 (1 byte ignored) | 5,000 ms | `♪ 5.0 s` |
| Codec 2 1200 | 270 | 45 × 40 ms | 1,800 ms | `♪ 1.8 s` |
| Codec 2 3200 | 2,000 | 250 × 20 ms | 5,000 ms | `♪ 5.0 s` |
| Codec 2 700C | 31 | 7 × 40 ms | 280 ms | under 0.6 s: refused |

**Limits use the same number:** a note is accepted from 600 to 15,000 ms. FireFly allows Opus up to 15,100 ms, because 60 ms frames don't land exactly on 15 s.

**Matching a Stump confirmation by its seconds.** The node confirms a voice DM with `[DM] <you>: ♪ 5.0 s`. That line names the length but not the recipient, so a client has to match it to the note it sent:

1. Compute the label from your own bytes when you send, and keep the note pending.
2. When a `♪` confirmation arrives, take the pending voice note with exactly that label.
3. If none matches (your rounding differs from the node's), take the pending voice note whose length is closest.
4. Never match by order. Over LoRa, a short one-packet note can arrive at the node before a longer note sent just before it, which needed a link.

A `⧗` (too fast) names no note either; FireFly fails the most recent pending voice note, since that's the one the rate limit stopped. Over HTTP, the poll's `voice.secs` is the same length as a number.

## Limits and receiving

Keep notes between 0.6 and 15 seconds and under the size limits below; receivers should accept anything well-formed and never crash on the rest.

| Limit | Value | Where it applies |
| --- | --- | --- |
| Length | 0.6 to 15 s (Opus to 15.1 s) | FireFly sends and Stump relays only these |
| Size accepted | 64 KiB | FireFly ignores a larger audio field |
| Per message at a Stump propagation node | 15 KB | Every voice format fits; Opus 15 s is about 13.4 to 14.2 KB, depending on the voice |
| Stump voice DM rate | 1 per 5 s, 30 per hour per sender | Over the limit: `⧗` |
| Stump voice DM storage | 10 most recent per inbox | On the node |

**When a message arrives:**

- Accept the field only as a list of at least two items: an integer mode, then bytes of 1 byte to 64 KiB. Otherwise ignore the field and keep the message.
- A mode you can't play (another Opus profile, the removed 450 modes) still shows as a voice note, marked as unsupported. Never drop the message or crash.
- Compute the length as in "Seconds" before decoding, so a conversation lists quickly.
- Keep the bytes and decode only when the note is played.
- Over LXMF a note from a Stump node arrives as its own message, text `[DM] <sender>: ♪ 5.0 s`, with the audio field: treat it as a voice DM from that nick.

## Voice DMs through a Stump node

A Stump node relays voice notes as DMs; rooms stay text. FireFly sends them in Opus by default, like every note, so the node must relay Opus as well as Codec 2: accept mode 16, label it `♪` from its Ogg pages (last granule minus pre-skip), allow an HTTP body of at least 14 KB, and pass the field on unchanged. The node's own Stump client quickstart is the reference; this is the client's side of it.

| Step | Over LoRa (LXMF) | Over Wi-Fi (HTTP) |
| --- | --- | --- |
| Send | An LXMF message to the node, text `/msg <nick>`, with the audio field | `POST /rrc/voice?to=<nick>&mode=<n>`, body = the note (an Ogg Opus file or Codec 2 frames) |
| Confirmation | `[DM] <you>: ♪ 5.0 s` in a reply | The same line in `replies` |
| Failures | `⊖ nick` (no such person), `⧗ — …` (too fast) | The same lines |
| Receive | Its own message: text `[DM] <sender>: ♪ 5.0 s` plus the audio field | In the poll, a DM with `voice: {mode, bytes, secs}`; fetch the note with `GET /rrc/voice?id=<dm id>` |

What a client must do:

- **Send Opus or Codec 2.** FireFly sends Opus by default, or the user's Codec 2 choice. A node not yet updated answers an Opus note with a sentence ("unreadable voice note (Codec 2 expected)"); FireFly then fails the note with those words rather than leaving it pending.
- **Never route it through a propagation node.** Messages left for the node are delivered to its chat hours later; voice DMs, like all Stump chat, go directly.
- **Keep your own note.** The node never sends your note back, so keep the bytes and show it once the `♪` confirmation arrives.
- **Match confirmations by length,** as "Seconds" explains: the confirmation doesn't name the recipient, and over LoRa notes can arrive out of order.
- **Check before sending:** 0.6 to 15 s and a supported mode. The node's refusals of those are sentences in its own language, which are hard to act on.

## Propagation nodes

FireFly leaves messages for offline people at a propagation node and collects its own there. Stump propagation nodes don't peer yet, so people near a Stump should all use that Stump's node, and FireFly picks it by default.

**Finding a Stump's node.** A propagation node announces rarely. Its address is derived from the same identity as the Stump's LXMF address, so FireFly computes it and asks for its path as soon as it recognises a Stump:

```python
pn_hash = RNS.Destination.hash(stump_identity, "lxmf", "propagation")
RNS.Transport.request_path(pn_hash)
```

**Choosing:** a Stump's node first, otherwise the nearest one heard, or a node the user pinned by address.

**Leaving a message:** at once if the recipient has no path; after failed direct attempts otherwise. A message over the node's per-transfer limit (`data[3]` of its announce, 15 KB on a Stump) is refused with a clear reason, not sent. A 15 s Opus note (about 13.4 KB) fits.

**Stamps:** a message left at a node needs a proof-of-work stamp (cost 16 on a Stump). A stamp is valid when SHA-256(work block + stamp) is under the target. LXMF hashes the whole 250 KB work block again for every attempt. FireFly hashes it once and copies that state for each attempt, about 200× less work with identical, valid stamps:

```python
base = hashlib.sha256(workblock)
h = base.copy(); h.update(candidate)        # per attempt: 32 bytes hashed, not 250 KB
valid = int.from_bytes(h.digest(), "big") <= (1 << (256 - cost))
```

**Collecting:**

- **When:** when the radio comes online, when the app is opened (at most every 2 minutes), when a node is chosen, and every 30 minutes.
- **Until empty:** a Stump answers each sync with at most about 16 KB (always room for one full-size message), so FireFly syncs again while rounds bring messages, up to 5 rounds.
- **One at a time:** requests made during a sync join it rather than start a second.
- **Retries:** a failed round is retried after 10 s, then 30 s.

## A Stump's billboard and files (Wi-Fi only)

FireFly 0.4.0 shows a node's billboard and file shelf from the node's JSON endpoints (the Stump quickstart's reference has the formats). Neither rides the mesh. A few things the endpoints don't make obvious:

- **A 404 means the feature is off.** FireFly hides that tab rather than showing an error.
- **`POST /post` always answers 303**, even when the node silently drops an empty post. FireFly reads `/billboard.json` back and only reports success when a new post with that title is there.
- **Form fields:** UTF-8, percent-encoded, spaces as `%20` (a `+` in the text is sent as `%2B`). The title's line breaks become spaces (80 characters); the body keeps them (600).
- **`X-Filename` is read as UTF-8 and not URL-decoded.** Send the filename's UTF-8 bytes as they are: a percent-encoded name is stored with its `%` escapes. Android's `HttpURLConnection` (and OkHttp) refuse non-ASCII header values, and browsers and Python's `urllib` send `é` as one Latin-1 byte, which the node can't decode: the request then ends with **no response at all**. FireFly writes the upload on a plain socket to get UTF-8 through.
- **Downloads:** `/download?f=` with the name percent-encoded as UTF-8. Stream the body to storage; files can be large.
- **Credits** are per client address on the node's network, so they follow your phone's IP on that network, not your identity.

## Blocking and deleting

Both are local to the phone and send nothing, so other clients see no change in the protocol. If a FireFly user blocks you, your messages are delivered over the air but discarded on their phone, and your announces are ignored. Nothing tells your client this has happened.

- **Delete** removes the conversation and the contact. The person comes back as new if they announce or write.
- **Block** deletes the same, then ignores the address's announces and messages, including through LXMF's own ignore list. It needs only the address, so it works for someone never heard again.

## Test vectors and reference files

Four known notes let you check your decoder, your duration code and your labels before any radio is involved. All four are the same 5 seconds of speech: `raw/kristoff.raw` from the Codec 2 1.2.0 repository (8 kHz, 16-bit, SHA-256 `d1a955308fd4fc08157e19a20322cd51074c74542b9184c6f49394d5e8bc87d7`).

| File in FireFly's `engine-tests/` | Mode | Bytes | Length | SHA-256 |
| --- | --- | --- | --- | --- |
| `kristoff_opus_8k.ogg` (what FireFly sends today: 8 kbit/s, constrained VBR) | 16 | 4,528 | 5,000 ms | `6592373b0a5664719cd9c5f66ca925da9548849a810583a8c279291a2105343f` |
| `kristoff_opus.ogg` (FireFly's encoder at 6 kbit/s) | 16 | 3,375 | 5,000 ms | `2e80c527382d71d6b966e19b581fa42497097163f5a8a0c96f872dc152e33b87` |
| `kristoff_1200.c2` | 4 | 750 | 5,000 ms | `7ba18f754933bab5201157faa0ffb6edf2d7cb4859418e2231f2ce30be56bd14` |
| `kristoff_3200.c2` | 9 | 2,000 | 5,000 ms | `a075571f1942c071bceb6c94a15001282271761a9a976a27673fa3717fbedb86` |

Each should decode to 5.00 s of clear speech and be labelled `♪ 5.0 s`. Both Opus files come from FireFly's own encoder, from that recording at 16 kHz; both have a pre-skip of 312 and pass `opusinfo` with no warnings. The `.c2` files are raw frames despite the name; copy them to `.bit` for `c2dec`. Encoding the source again should reproduce the Codec 2 files; a few bits may differ across CPUs, because the encoder uses floating point, so the decode check is the one that matters.

| FireFly file | What it shows |
| --- | --- |
| `app/src/main/cpp/firefly_opus.c` | Ogg Opus writer and reader (RFC 7845) around libopus |
| `app/src/main/cpp/firefly_codec2.c` | Codec 2 encode and decode, LXMF modes mapped to Codec 2 modes |
| `app/src/main/python/firefly/audio.py` | Durations for every mode, including Opus from the Ogg pages |
| `app/src/main/python/firefly/stump_session.py` | Voice DMs through a Stump, and matching `♪` confirmations |
| `app/src/main/python/firefly/core.py` | The audio field, the codec choice, propagation and stamps |
