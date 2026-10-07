# Codec 2 (vendored)

Speech codec by David Rowe and contributors, https://github.com/drowe67/codec2,
tag 1.2.0, LGPL-2.1 (COPYING). Built as its own shared library
(`libfirefly_codec2.so`), unmodified.

Only the speech-codec sources are included (no modems or FreeDV). The
`codebook*.c` files are normally generated at build time by Codec 2's
`generate_codebook` tool, which can't run when cross-compiling for Android;
they were generated once from the `src/codebook/*.txt` tables of the same tag,
exactly as Codec 2's CMakeLists does. `codec2/version.h` is its version.h.in
filled in for 1.2.0.
