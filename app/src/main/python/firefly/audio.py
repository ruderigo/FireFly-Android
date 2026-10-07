"""Voice note formats: what FireFly can play, and how long a note lasts.

LXMF audio modes (LXMF.AM_*): Codec 2 3-9 are raw frames back to back;
16 (AM_OPUS_OGG) is a complete Ogg Opus file (RFC 7845), as Sideband sends.
"""

# Codec 2: mode -> (bytes per frame, ms per frame). 1 and 2 (450) left Codec 2 in 1.2.
CODEC2_FRAMES = {3: (4, 40), 4: (6, 40), 5: (7, 40), 6: (7, 40), 7: (8, 40), 8: (6, 20), 9: (8, 20)}
OPUS_OGG = 16
PLAYABLE = set(CODEC2_FRAMES) | {OPUS_OGG}
MAX_VOICE_BYTES = 64 * 1024
VOICE_MIN_MS, VOICE_MAX_MS = 600, 15000
OPUS_MAX_MS = 15100            # 60 ms frames don't land exactly on 15 s
STUMP_MAX_VOICE_BYTES = 16000  # what a Stump node accepts per voice note


def duration_ms(mode, data):
    """Length of a voice note in milliseconds, or None if it can't be told."""
    if not data:
        return None
    f = CODEC2_FRAMES.get(mode)
    if f:
        return (len(data) // f[0]) * f[1]
    if mode == OPUS_OGG:
        return ogg_opus_duration_ms(data)
    return None


def ogg_opus_duration_ms(data):
    """From the Ogg pages: (last granule position - pre-skip) / 48 kHz."""
    data = bytes(data)
    pos, serial, pre_skip, last = 0, None, None, None
    while pos + 27 <= len(data):
        if data[pos:pos + 4] != b"OggS":
            nxt = data.find(b"OggS", pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue
        nseg = data[pos + 26]
        lacing = data[pos + 27:pos + 27 + nseg]
        body_at = pos + 27 + nseg
        body = sum(lacing)
        if body_at + body > len(data):
            break
        page_serial = int.from_bytes(data[pos + 14:pos + 18], "little")
        if serial is None:
            serial = page_serial
            if data[body_at:body_at + 8] != b"OpusHead" or body < 19:
                return None
            pre_skip = int.from_bytes(data[body_at + 10:body_at + 12], "little")
        elif page_serial == serial:
            granule = int.from_bytes(data[pos + 6:pos + 14], "little")
            if granule != 0xFFFFFFFFFFFFFFFF:
                last = granule
        pos = body_at + body
    if last is None or pre_skip is None or last <= pre_skip:
        return None
    return (last - pre_skip) * 1000 // 48000


def valid_voice(mode, data):
    """Something FireFly will send: a playable format, 0.6 to 15 s, not oversized."""
    if mode not in PLAYABLE or not data or len(data) > MAX_VOICE_BYTES:
        return False
    ms = duration_ms(mode, data)
    top = OPUS_MAX_MS if mode == OPUS_OGG else VOICE_MAX_MS
    return ms is not None and VOICE_MIN_MS <= ms <= top


def label(ms):
    """♪ 5.0 s: tenths rounded half up, a dot whatever the language (as the Stump node writes it)."""
    tenths = (int(ms) + 50) // 100
    return f"♪ {tenths // 10}.{tenths % 10} s"
