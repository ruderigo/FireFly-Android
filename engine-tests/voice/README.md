# Voice tests (desktop)

Runs the app's own `audio/Codec2.kt` and `audio/Resample.kt` on a desktop JVM,
against the vendored Codec 2 built as a host library. Needs a JDK and `kotlinc`.

    A=../../app/src/main; J=$(dirname $(dirname $(readlink -f $(which javac))))
    gcc -O2 -shared -fPIC -I$J/include -I$J/include/linux -I$A/cpp/codec2 -I$A/cpp \
        $A/cpp/firefly_codec2.c $A/cpp/codec2/*.c -o libfirefly_codec2.so -lm
    kotlinc $A/java/io/github/ruderigo/firefly/audio/Codec2.kt VoiceTest.kt -include-runtime -d vt.jar
    java -Djava.library.path=. -cp vt.jar VoiceTestKt <8 kHz 16-bit .raw speech files>
    kotlinc $A/java/io/github/ruderigo/firefly/audio/Resample.kt ResampleTest.kt -include-runtime -d rs.jar
    java -cp rs.jar ResampleTestKt

Speech samples: `raw/hts1a.raw` and `raw/kristoff.raw` from the Codec 2 repository.
`../kristoff_1200.c2` is kristoff.raw encoded at 1200 bps, used by run_air.py.
