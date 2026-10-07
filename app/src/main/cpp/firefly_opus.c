/*
 * Opus voice notes for FireFly: whole notes in, standard Ogg Opus files out
 * (RFC 7845), and back. The file is what goes in LXMF's audio field with
 * mode AM_OPUS_OGG (16), the format Sideband uses for its Opus notes.
 *
 * Encoding: mono speech at 8 or 16 kHz, 60 ms frames, VBR, VOIP tuning.
 * Decoding: any Ogg Opus voice file (mono or stereo, mixed down to mono),
 * pre-skip removed and the end trimmed to the last granule position.
 */
#include <jni.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "opus.h"

#define MAX_SECONDS 60
#define FRAME_MS 60
#define PACKETS_PER_PAGE 16            /* ~1 s per page: small container overhead */
#define MAX_PACKET 1500

/* ------------------------------------------------------------------ growable buffer */
typedef struct { unsigned char *d; size_t n, cap; int bad; } buf_t;

static void put(buf_t *b, const void *p, size_t n) {
    if (b->bad) return;
    if (b->n + n > b->cap) {
        size_t cap = b->cap ? b->cap * 2 : 8192;
        while (cap < b->n + n) cap *= 2;
        unsigned char *d = realloc(b->d, cap);
        if (!d) { b->bad = 1; return; }
        b->d = d; b->cap = cap;
    }
    memcpy(b->d + b->n, p, n); b->n += n;
}

/* ------------------------------------------------------------------ Ogg pages */
static uint32_t crc_table[256];
static void crc_init(void) {
    static int done = 0;
    if (done) return;
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t r = i << 24;
        for (int k = 0; k < 8; k++) r = (r & 0x80000000u) ? (r << 1) ^ 0x04c11db7u : (r << 1);
        crc_table[i] = r;
    }
    done = 1;
}
static uint32_t ogg_crc(const unsigned char *p, size_t n) {
    uint32_t c = 0;
    for (size_t i = 0; i < n; i++) c = (c << 8) ^ crc_table[((c >> 24) ^ p[i]) & 0xff];
    return c;
}
static void le32(unsigned char *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void le64(unsigned char *p, uint64_t v) { le32(p, (uint32_t)v); le32(p + 4, (uint32_t)(v >> 32)); }
static uint32_t rd32(const unsigned char *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint64_t rd64(const unsigned char *p) { return rd32(p) | ((uint64_t)rd32(p + 4) << 32); }

/* One page holding whole packets (each < 255*255 bytes, always true here). */
static void write_page(buf_t *out, uint32_t serial, uint32_t seq, int flags, uint64_t granule,
                       const unsigned char **pkts, const int *lens, int count) {
    unsigned char seg[255]; int nseg = 0; size_t body = 0;
    for (int i = 0; i < count; i++) {
        int l = lens[i];
        while (l >= 255) { seg[nseg++] = 255; l -= 255; }
        seg[nseg++] = (unsigned char)l;
        body += lens[i];
    }
    size_t hlen = 27 + nseg, total = hlen + body;
    unsigned char *page = malloc(total);
    if (!page) { out->bad = 1; return; }
    memcpy(page, "OggS", 4); page[4] = 0; page[5] = (unsigned char)flags;
    le64(page + 6, granule); le32(page + 14, serial); le32(page + 18, seq); le32(page + 22, 0);
    page[26] = (unsigned char)nseg; memcpy(page + 27, seg, nseg);
    size_t off = hlen;
    for (int i = 0; i < count; i++) { memcpy(page + off, pkts[i], lens[i]); off += lens[i]; }
    le32(page + 22, ogg_crc(page, total));
    put(out, page, total);
    free(page);
}

/* ------------------------------------------------------------------ encode */
JNIEXPORT jbyteArray JNICALL
Java_io_github_ruderigo_firefly_audio_Opus_encodeOgg(JNIEnv *env, jclass cls, jshortArray pcm, jint rate, jint bitrate) {
    if (pcm == NULL || (rate != 8000 && rate != 16000)) return NULL;
    crc_init();
    int err = 0;
    OpusEncoder *enc = opus_encoder_create(rate, 1, OPUS_APPLICATION_VOIP, &err);
    if (!enc || err != OPUS_OK) return NULL;
    opus_encoder_ctl(enc, OPUS_SET_BITRATE(bitrate));
    opus_encoder_ctl(enc, OPUS_SET_VBR(1));
    opus_encoder_ctl(enc, OPUS_SET_VBR_CONSTRAINT(1));     /* size stays near the target: 15 s at 8 kbit/s < 15 KB */
    opus_encoder_ctl(enc, OPUS_SET_COMPLEXITY(10));
    opus_encoder_ctl(enc, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE));
    opus_int32 lookahead = 0;
    opus_encoder_ctl(enc, OPUS_GET_LOOKAHEAD(&lookahead));
    const int scale = 48000 / rate;                       /* granule positions count 48 kHz samples */
    const int pre_skip = lookahead * scale;

    jsize n = (*env)->GetArrayLength(env, pcm);
    if (n > rate * MAX_SECONDS) n = rate * MAX_SECONDS;
    const int fs = rate * FRAME_MS / 1000;
    /* Enough frames to cover the audio plus the encoder's lookahead, and no more:
       the end trimming then stays within the last packet, as RFC 7845 asks. */
    int total = (int)(((long)n + lookahead + fs - 1) / fs);
    short *in = calloc((size_t)total * fs, sizeof(short));
    if (!in) { opus_encoder_destroy(enc); return NULL; }
    (*env)->GetShortArrayRegion(env, pcm, 0, n, in);

    buf_t out = {0}; uint32_t serial = (uint32_t)rand() ^ 0x46467a31u, seq = 0;
    unsigned char head[19];
    memcpy(head, "OpusHead", 8); head[8] = 1; head[9] = 1;
    head[10] = pre_skip & 0xff; head[11] = pre_skip >> 8; le32(head + 12, (uint32_t)rate);
    head[16] = 0; head[17] = 0; head[18] = 0;
    const unsigned char *p1[1] = {head}; int l1[1] = {19};
    write_page(&out, serial, seq++, 0x02, 0, p1, l1, 1);
    const char vendor[] = "FireFly";
    unsigned char tags[8 + 4 + sizeof(vendor) - 1 + 4];
    memcpy(tags, "OpusTags", 8); le32(tags + 8, sizeof(vendor) - 1);
    memcpy(tags + 12, vendor, sizeof(vendor) - 1); le32(tags + 12 + sizeof(vendor) - 1, 0);
    const unsigned char *p2[1] = {tags}; int l2[1] = {(int)sizeof(tags)};
    write_page(&out, serial, seq++, 0, 0, p2, l2, 1);

    unsigned char *pk = malloc((size_t)PACKETS_PER_PAGE * MAX_PACKET);
    const unsigned char *pp[PACKETS_PER_PAGE]; int pl[PACKETS_PER_PAGE]; int inpage = 0;
    uint64_t encoded48 = 0, end48 = (uint64_t)pre_skip + (uint64_t)n * scale;
    for (int f = 0; f < total && pk; f++) {
        unsigned char *slot = pk + (size_t)inpage * MAX_PACKET;
        int len = opus_encode(enc, in + (size_t)f * fs, fs, slot, MAX_PACKET);
        if (len < 0) { out.bad = 1; break; }
        pp[inpage] = slot; pl[inpage] = len; inpage++;
        encoded48 += (uint64_t)fs * scale;
        int last = (f == total - 1);
        if (inpage == PACKETS_PER_PAGE || last) {
            uint64_t g = encoded48 < end48 ? encoded48 : end48;    /* the last page trims padding */
            write_page(&out, serial, seq++, last ? 0x04 : 0, last ? end48 : g, pp, pl, inpage);
            inpage = 0;
        }
    }
    free(pk); free(in); opus_encoder_destroy(enc);
    if (out.bad || !out.d) { free(out.d); return NULL; }
    jbyteArray res = (*env)->NewByteArray(env, (jsize)out.n);
    if (res) (*env)->SetByteArrayRegion(env, res, 0, (jsize)out.n, (jbyte *)out.d);
    free(out.d);
    return res;
}

/* ------------------------------------------------------------------ decode */
JNIEXPORT jshortArray JNICALL
Java_io_github_ruderigo_firefly_audio_Opus_decodeOgg(JNIEnv *env, jclass cls, jbyteArray ogg, jint rate) {
    if (ogg == NULL || (rate != 8000 && rate != 16000 && rate != 24000 && rate != 48000)) return NULL;
    jsize n = (*env)->GetArrayLength(env, ogg);
    if (n < 47) return NULL;
    unsigned char *d = malloc(n);
    if (!d) return NULL;
    (*env)->GetByteArrayRegion(env, ogg, 0, n, (jbyte *)d);

    OpusDecoder *dec = NULL;
    int pre_skip = 0, packet_no = 0, channels = 0;
    uint32_t serial = 0; int have_serial = 0;
    uint64_t last_granule = 0;
    buf_t pcm = {0}, packet = {0};
    const int scale = 48000 / rate;
    short *frame = malloc(sizeof(short) * 5760 / scale * 2);
    size_t pos = 0;
    while (frame && pos + 27 <= (size_t)n) {
        if (memcmp(d + pos, "OggS", 4) != 0) { pos++; continue; }        /* resync */
        const unsigned char *h = d + pos;
        int nseg = h[26];
        if (pos + 27 + nseg > (size_t)n) break;
        size_t body = 0;
        for (int i = 0; i < nseg; i++) body += h[27 + i];
        if (pos + 27 + nseg + body > (size_t)n) break;
        uint32_t s = rd32(h + 14);
        if (!have_serial) { serial = s; have_serial = 1; }
        if (s != serial) { pos += 27 + nseg + body; continue; }          /* other streams */
        uint64_t granule = rd64(h + 6);
        if (granule != (uint64_t)-1 && packet_no >= 2) last_granule = granule;
        const unsigned char *bp = h + 27 + nseg;
        for (int i = 0; i < nseg; i++) {
            put(&packet, bp, h[27 + i]); bp += h[27 + i];
            if (h[27 + i] == 255) continue;                               /* continues in the next segment */
            if (packet_no == 0) {
                if (packet.n < 19 || memcmp(packet.d, "OpusHead", 8) != 0) goto fail;
                channels = packet.d[9];
                pre_skip = packet.d[10] | (packet.d[11] << 8);
                if (channels < 1 || channels > 2 || packet.d[18] != 0) goto fail;   /* mono/stereo, family 0 */
                int e = 0;
                dec = opus_decoder_create(rate, 1, &e);                  /* stereo is mixed down to mono */
                if (!dec || e != OPUS_OK) goto fail;
            } else if (packet_no >= 2 && dec) {
                int got = opus_decode(dec, packet.d, (opus_int32)packet.n, frame, 5760 / scale, 0);
                if (got > 0) put(&pcm, frame, (size_t)got * sizeof(short));
            }
            packet_no++;
            packet.n = 0;
        }
        if (granule != (uint64_t)-1 && packet_no > 2) last_granule = granule;
        pos += 27 + nseg + body;
    }
    if (!dec || pcm.bad) goto fail;
    size_t samples = pcm.n / sizeof(short);
    size_t skip = (size_t)pre_skip / scale;
    size_t keep = samples > skip ? samples - skip : 0;
    if (last_granule > (uint64_t)pre_skip) {
        size_t end = (size_t)((last_granule - pre_skip) / scale);
        if (end < keep) keep = end;
    }
    if (keep > (size_t)rate * MAX_SECONDS) keep = (size_t)rate * MAX_SECONDS;
    jshortArray res = (*env)->NewShortArray(env, (jsize)keep);
    if (res && keep) (*env)->SetShortArrayRegion(env, res, 0, (jsize)keep, (jshort *)pcm.d + skip);
    opus_decoder_destroy(dec); free(d); free(frame); free(pcm.d); free(packet.d);
    return res;
fail:
    if (dec) opus_decoder_destroy(dec);
    free(d); free(frame); free(pcm.d); free(packet.d);
    return NULL;
}
