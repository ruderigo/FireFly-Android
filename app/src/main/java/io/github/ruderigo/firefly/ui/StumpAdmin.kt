package io.github.ruderigo.firefly.ui

import android.annotation.SuppressLint
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.webkit.ProxyConfig
import androidx.webkit.ProxyController
import androidx.webkit.WebViewFeature
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.net.WifiHttp
import io.github.ruderigo.firefly.net.WifiProxy

/**
 * A Stump's /admin, inside FireFly (Nov 18). The Stump's own page, unchanged: its password
 * screen, its phone layout, its own switch to HTTPS when it has a certificate; logged in, the
 * session cookie keeps it so for that Stump. FireFly never keeps the password (the web view
 * saves no form data or passwords).
 *
 * Over the Wi-Fi whatever the connection: the web view goes through WifiProxy (a proxy inside
 * FireFly that sends over the Wi-Fi network), so a Stump's hotspot with no internet works
 * beside mobile data, and nothing else changes: the rest of FireFly, and any other app (a
 * show streaming beside it, split screen), keep their own way out. The proxy is set for
 * FireFly's web views only while this screen is open.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun StumpAdminScreen(base: String, name: String, back: () -> Unit) {
    val url = base.trimEnd('/') + "/admin"
    val onWifi = WifiHttp.local.collectAsState().value != null
    var ready by remember { mutableStateOf(false) }
    var web by remember { mutableStateOf<WebView?>(null) }

    DisposableEffect(Unit) {
        val port = WifiProxy.start()
        val proxied = WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)
        if (proxied) {
            ProxyController.getInstance().setProxyOverride(
                ProxyConfig.Builder().addProxyRule("127.0.0.1:$port").build(), { it.run() }) { ready = true }
        } else ready = true            // a very old web view: straight out (fine when the Wi-Fi is the default)
        onDispose {
            if (proxied) ProxyController.getInstance().clearProxyOverride({ it.run() }) { }
            WifiProxy.stop()
        }
    }
    BackHandler { web?.takeIf { it.canGoBack() }?.goBack() ?: back() }

    Column(Modifier.fillMaxSize()) {
        TopBar(stringResource(R.string.admin_title, name), base.substringAfter("://").trimEnd('/'), back) {
            Text(stringResource(R.string.admin_reload), modifier = Modifier.clip(Small).clickable { web?.reload() }
                .padding(horizontal = 12.dp, vertical = 6.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
        }
        when {
            !onWifi -> Hint(stringResource(R.string.admin_no_wifi), Modifier.padding(16.dp))
            !ready -> Unit
            else -> AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
                WebView(ctx).apply {
                    settings.javaScriptEnabled = true          // the page's own confirm() before a restart, its forms
                    settings.domStorageEnabled = true
                    @Suppress("DEPRECATION") settings.saveFormData = false
                    settings.setSupportZoom(true)
                    settings.builtInZoomControls = true
                    settings.displayZoomControls = false
                    CookieManager.getInstance().setAcceptCookie(true)
                    webViewClient = WebViewClient()             // links stay in here, on the Stump
                    webChromeClient = WebChromeClient()         // its alert() / confirm() as dialogs
                    loadUrl(url)
                    web = this
                }
            })
        }
    }
}
