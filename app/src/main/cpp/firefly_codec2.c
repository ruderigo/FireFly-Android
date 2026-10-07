/*
 * JNI bridge between FireFly (audio/Codec2.kt) and Codec 2.
 * Modes are LXMF's audio-mode numbers (LXMF.AM_CODEC2_*), so the number that
 * travels in the message field is the number the codec is asked for.
 * Whole clips in, whole clips out: one JNI call per voice note.
 */
#include <jni.h>
#include <stdlib.h>
#include <string.h>
#include "codec2/codec2.h"

#define MAX_SECONDS 60

static int c2_mode(jint lxmf_mode) {
    switch (lxmf_mode) {
        /* 1 and 2 (450PWB, 450) were experimental and removed from Codec 2 1.2. */
        case 3: return CODEC2_MODE_700C;
        case 4: return CODEC2_MODE_1200;
        case 5: return CODEC2_MODE_1300;
        case 6: return CODEC2_MODE_1400;
        case 7: return CODEC2_MODE_1600;
        case 8: return CODEC2_MODE_2400;
        case 9: return CODEC2_MODE_3200;
        default: return -1;
    }
}

static struct CODEC2 *open_codec(jint lxmf_mode) {
    int m = c2_mode(lxmf_mode);
    return m < 0 ? NULL : codec2_create(m);
}

JNIEXPORT jint JNICALL
Java_io_github_ruderigo_firefly_audio_Codec2_samplesPerFrame(JNIEnv *env, jclass cls, jint mode) {
    struct CODEC2 *c = open_codec(mode);
    if (!c) return -1;
    int n = codec2_samples_per_frame(c);
    codec2_destroy(c);
    return n;
}

JNIEXPORT jint JNICALL
Java_io_github_ruderigo_firefly_audio_Codec2_bytesPerFrame(JNIEnv *env, jclass cls, jint mode) {
    struct CODEC2 *c = open_codec(mode);
    if (!c) return -1;
    int n = codec2_bytes_per_frame(c);
    codec2_destroy(c);
    return n;
}

/* 8 kHz mono 16-bit PCM in, Codec 2 frames out (a trailing partial frame is zero-padded). */
JNIEXPORT jbyteArray JNICALL
Java_io_github_ruderigo_firefly_audio_Codec2_encode(JNIEnv *env, jclass cls, jint mode, jshortArray pcm) {
    struct CODEC2 *c = open_codec(mode);
    if (!c || pcm == NULL) { if (c) codec2_destroy(c); return NULL; }
    int spf = codec2_samples_per_frame(c), bpf = codec2_bytes_per_frame(c);
    jsize n = (*env)->GetArrayLength(env, pcm);
    if (n > 8000 * MAX_SECONDS) n = 8000 * MAX_SECONDS;
    int frames = (n + spf - 1) / spf;
    short *in = calloc((size_t)frames * spf, sizeof(short));
    unsigned char *out = malloc((size_t)frames * bpf);
    if (!in || !out) { free(in); free(out); codec2_destroy(c); return NULL; }
    (*env)->GetShortArrayRegion(env, pcm, 0, n, in);
    for (int f = 0; f < frames; f++) codec2_encode(c, out + f * bpf, in + f * spf);
    jbyteArray res = (*env)->NewByteArray(env, frames * bpf);
    if (res) (*env)->SetByteArrayRegion(env, res, 0, frames * bpf, (jbyte *)out);
    free(in); free(out); codec2_destroy(c);
    return res;
}

/* Codec 2 frames in, 8 kHz mono 16-bit PCM out. Bytes beyond the last whole frame are ignored. */
JNIEXPORT jshortArray JNICALL
Java_io_github_ruderigo_firefly_audio_Codec2_decode(JNIEnv *env, jclass cls, jint mode, jbyteArray bits) {
    struct CODEC2 *c = open_codec(mode);
    if (!c || bits == NULL) { if (c) codec2_destroy(c); return NULL; }
    int spf = codec2_samples_per_frame(c), bpf = codec2_bytes_per_frame(c);
    jsize n = (*env)->GetArrayLength(env, bits);
    int frames = n / bpf;
    int max_frames = 8000 * MAX_SECONDS / spf;
    if (frames > max_frames) frames = max_frames;
    unsigned char *in = malloc((size_t)(frames ? frames : 1) * bpf);
    short *out = malloc((size_t)(frames ? frames : 1) * spf * sizeof(short));
    if (!in || !out) { free(in); free(out); codec2_destroy(c); return NULL; }
    (*env)->GetByteArrayRegion(env, bits, 0, frames * bpf, (jbyte *)in);
    for (int f = 0; f < frames; f++) codec2_decode(c, out + f * spf, in + f * bpf);
    jshortArray res = (*env)->NewShortArray(env, frames * spf);
    if (res) (*env)->SetShortArrayRegion(env, res, 0, frames * spf, out);
    free(in); free(out); codec2_destroy(c);
    return res;
}
