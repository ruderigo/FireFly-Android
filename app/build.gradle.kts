plugins {
  alias(libs.plugins.android.application)
  alias(libs.plugins.kotlin.compose)
  alias(libs.plugins.chaquopy)
}

android {
  namespace = "io.github.ruderigo.firefly"
  compileSdk = 36

  defaultConfig {
    applicationId = "io.github.ruderigo.firefly"
    minSdk = 26
    targetSdk = 36
    versionCode = 3
    versionName = "0.1.2"
    ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
  }

  buildTypes {
    release {
      isMinifyEnabled = false   // Python calls Kotlin by reflection: keep names intact
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
    }
  }
  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
  }
  buildFeatures { compose = true }
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
