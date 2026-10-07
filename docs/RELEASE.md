# Building a release APK

A release build is signed with **your own key**, is arm64 only (every
current phone), and is roughly half the size of the debug build, which also
carries x86_64 for the emulator. It's the one to share, and the one a Stump
node can hand out from its card (`tools/FireFly-Android-<version>.apk`).

## 1. Make a key, once

Keep the file and both passwords somewhere safe. **Every future update must be
signed with this same key**: Android refuses to update an app signed with a
different one.

    keytool -genkeypair -v -keystore firefly-release.jks -alias firefly \
            -keyalg RSA -keysize 4096 -validity 10000

`keytool` comes with the JDK (Android Studio ships one: on a Mac it's
`/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/keytool`).

## 2. Tell the build where it is

Create `keystore.properties` next to `settings.gradle.kts`. It's in
`.gitignore`, so it never gets committed:

    storeFile=/Users/you/keys/firefly-release.jks
    storePassword=…
    keyAlias=firefly
    keyPassword=…

Or set `FIREFLY_KEYSTORE`, `FIREFLY_KEYSTORE_PASSWORD`, `FIREFLY_KEY_ALIAS`
and `FIREFLY_KEY_PASSWORD` in the environment instead.

## 3. Build

    ./gradlew assembleRelease

The APK is `app/build/outputs/apk/release/app-release.apk`. Without a key
configured the build still succeeds but the APK is unsigned and won't
install. To check its signature:

    $ANDROID_HOME/build-tools/<version>/apksigner verify --print-certs app/build/outputs/apk/release/app-release.apk

To include the emulator ABI anyway: `./gradlew assembleRelease -Pfirefly.abis=arm64-v8a,x86_64`.

## 4. Moving a phone from the debug build to the release build

The debug build is signed with Android Studio's debug key, so a release
build **can't be installed over it**. Uninstalling deletes the app's data,
and that includes your identity, which is your address. So:

1. In the debug build: **Settings › Identity › Back up**. Save the file
   somewhere off the app (Downloads, Drive).
2. Uninstall the debug build: `adb uninstall io.github.ruderigo.firefly`.
3. Install the release build: `adb install app-release.apk`.
4. **Settings › Identity › Restore**, pick the file, then Quit and reopen.

You keep your address, and contacts still reach you. Messages and settings
stay behind with the old install. After this, every release update installs
over the previous one with nothing lost.
