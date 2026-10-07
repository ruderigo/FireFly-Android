# libopus (vendored)

Xiph.Org libopus 1.5.2, BSD licence (COPYING), from the official release
tarball, built with its own CMake as a static library and linked into
`libfirefly_opus.so` (see ../firefly_opus.c).

Trimmed for size: the deep-learning extras (DRED, OSCE) are off, so their
model weight files (`dnn/*_data.c` over 100 KB, ~17 MB) and the training
and documentation folders were removed. Everything else is unmodified.
