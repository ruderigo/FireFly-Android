import java.util.Properties

plugins {
  alias(libs.plugins.android.application)
  alias(libs.plugins.kotlin.compose)
  alias(libs.plugins.chaquopy)
}

// Release signing: your own key, never committed. Either a keystore.properties file
// next to settings.gradle.kts (see docs/RELEASE.md), or FIREFLY_* environment variables.
val signing = Properties().apply {
  rootProject.file("keystore.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun signingValue(key: String, env: String): String? =
  signing.getProperty(key) ?: System.getenv(env)
val releaseStore = signingValue("storeFile", "FIREFLY_KEYSTORE")

// Phones are arm64. x86_64 is only for the emulator, so a release build leaves it out
// (about half the size). -Pfirefly.abis=arm64-v8a,x86_64 overrides either way.
val releaseBuild = gradle.startParameter.taskNames.any { it.contains("Release", ignoreCase = true) }
val abis = (findProperty("firefly.abis") as String?)?.split(",")?.map { it.trim() }?.filter { it.isNotEmpty() }
  ?: if (releaseBuild) listOf("arm64-v8a") else listOf("arm64-v8a", "x86_64")

android {
  namespace = "io.github.ruderigo.firefly"
  compileSdk = 36

  defaultConfig {
    applicationId = "io.github.ruderigo.firefly"
    minSdk = 26
    targetSdk = 36
    versionCode = 16
    versionName = "0.4.0"
    ndk { abiFilters += abis }
  }

  signingConfigs {
    if (releaseStore != null) {
      create("release") {
        storeFile = rootProject.file(releaseStore)
        storePassword = signingValue("storePassword", "FIREFLY_KEYSTORE_PASSWORD")
        keyAlias = signingValue("keyAlias", "FIREFLY_KEY_ALIAS")
        keyPassword = signingValue("keyPassword", "FIREFLY_KEY_PASSWORD")
      }
    }
  }

  buildTypes {
    release {
      isMinifyEnabled = false   // Python calls Kotlin by reflection: keep names intact
      isDebuggable = false
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
      // Without a key configured the release APK comes out unsigned (and won't install).
      signingConfigs.findByName("release")?.let { signingConfig = it }
    }
  }
  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
  }
  buildFeatures { compose = true }
  // Codec 2 for voice notes (app/src/main/cpp). Needs the NDK and CMake from
  // Android Studio's SDK Manager > SDK Tools.
  externalNativeBuild {
    cmake {
      path = file("src/main/cpp/CMakeLists.txt")
      version = "3.22.1"
    }
  }
}

chaquopy {
  defaultConfig {
    version = "3.13"
    (findProperty("firefly.buildPython") as String?)?.let { buildPython(it) }
    pip {
      // Pinned to the versions the engine tests (engine-tests/) run against.
      install("rns==1.5.5")
      install("lxmf==1.2.0")
      install("pyserial")      // imported by Reticulum's RNode driver; the port itself is ours
    }
    pyc { src = true }
  }
  sourceSets {
    getByName("main") { srcDir("src/main/python") }
  }
}

dependencies {
  implementation(libs.androidx.core.ktx)
  implementation(libs.androidx.activity.compose)
  implementation(libs.androidx.lifecycle.runtime.compose)
  implementation(libs.androidx.lifecycle.service)
  implementation(platform(libs.androidx.compose.bom))
  implementation(libs.androidx.compose.ui)
  implementation(libs.androidx.compose.foundation)
  implementation(libs.androidx.compose.material3)
  debugImplementation(libs.androidx.compose.ui.tooling)
  implementation(libs.kotlinx.coroutines.android)
  implementation(libs.usb.serial)
  testImplementation(libs.junit)
}
