package io.github.ruderigo.firefly.ui

import android.content.Intent
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.nameText
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.objects
import io.github.ruderigo.firefly.net.WifiDiscovery
import io.github.ruderigo.firefly.net.WifiHttp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * The Stumps list's "+ 🪵" button, bottom right where the thumb is (Nov 2026). Tapped, it opens
 * three, icons only: search this Wi-Fi (FindStumpsPopup), add one by its QR code or address
 * (AddStumpPopup), the Stump hotspots nearby (HotspotsPopup). Tapped again, or outside, it closes.
 */
@Composable
fun StumpFab(modifier: Modifier = Modifier) {
    var open by remember { mutableStateOf(false) }
    var show by remember { mutableStateOf<String?>(null) }        // "search" | "add" | "hotspots"
    fun pick(which: String) { open = false; show = which }

    when (show) {
        "search" -> FindStumpsPopup(onDismiss = { show = null })
        "add" -> AddStumpPopup(onDismiss = { show = null })
        "hotspots" -> HotspotsPopup(onDismiss = { show = null }, onSearch = { show = "search" })
    }

    Box(Modifier.fillMaxSize()) {
        if (open) Box(Modifier.fillMaxSize().clickable(indication = null,
            interactionSource = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }) { open = false })
        Column(modifier.align(Alignment.BottomEnd).padding(end = 16.dp, bottom = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            AnimatedVisibility(open, enter = fadeIn() + scaleIn(), exit = fadeOut() + scaleOut()) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    SmallFab(R.drawable.ic_fab_hotspot, stringResource(R.string.fab_hotspots)) { pick("hotspots") }
                    SmallFab(R.drawable.ic_fab_plus, stringResource(R.string.fab_add)) { pick("add") }
                    SmallFab(R.drawable.ic_fab_scan, stringResource(R.string.fab_search)) { pick("search") }
                }
            }
            Row(Modifier.shadow(6.dp, CircleShape).clip(CircleShape).background(Amber.Ember)
                .clickable { open = !open }.size(60.dp), horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically) {
                Text(if (open) "×" else "+", style = MaterialTheme.typography.titleLarge.copy(color = Amber.Background))
                if (!open) Icon(painterResource(R.drawable.ic_stump), contentDescription = stringResource(R.string.fab_open),
                    tint = Amber.Background, modifier = Modifier.size(24.dp))
            }
        }
    }
}

@Composable
private fun SmallFab(icon: Int, label: String, size: Dp = 46.dp, onClick: () -> Unit) {
    Box(Modifier.shadow(4.dp, CircleShape).clip(CircleShape).background(Amber.Panel).clickable(onClick = onClick).size(size),
        contentAlignment = Alignment.Center) {
        Icon(painterResource(icon), contentDescription = label, tint = Amber.Ember, modifier = Modifier.size(24.dp))
    }
}

/**
 * The + button › add one: its QR code (a Stump's address page, Stump 1.0.0-beta.51+, or any LXMF
 * app's) or its address typed. AddressIntake decides where it goes (here, an address it can't
 * tell is taken for a Stump's) and the Stump joins the list at once.
 */
@Composable
fun AddStumpPopup(onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    var typed by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    fun submit(text: String) { AddressIntake.submit(text, prefer = "stump"); onDismiss() }
    val scanQr = rememberQrScan { contents ->
        if (contents == null) error = ctx.getString(R.string.scan_none) else submit(contents)
    }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = Amber.Panel, shape = Large,
        title = { Text(stringResource(R.string.add_stump_title), style = MaterialTheme.typography.titleMedium) },
        text = {
            Column {
                Text(stringResource(R.string.add_stump_help), style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(10.dp))
                Button(stringResource(R.string.scan_qr)) { error = null; scanQr() }
                Spacer(Modifier.height(10.dp))
                Text(stringResource(R.string.add_stump_or_type), style = MaterialTheme.typography.labelMedium)
                Spacer(Modifier.height(6.dp))
                Field(typed, { typed = it; error = null }, Modifier.fillMaxWidth(),
                    hint = stringResource(R.string.address_hint), mono = true,
                    onDone = { if (typed.isNotBlank()) submit(typed) })
                error?.let { Note(it, error = true) }
            }
        },
        confirmButton = {
            TextLink(stringResource(R.string.add_stump_add)) { if (typed.isNotBlank()) submit(typed) }
        },
        dismissButton = {
            Text(stringResource(R.string.cancel), modifier = Modifier.clip(Small).clickable { onDismiss() }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        })
}

/**
 * The + button › hotspots: the Stump hotspots nearby (names that say labuche, stump or tronco,
 * or joined as a Stump before), strongest first, one Join each. Joined: if a Stump answers
 * there, it's remembered and added to the list; Search then looks for more on that Wi-Fi.
 */
@Composable
fun HotspotsPopup(onDismiss: () -> Unit, onSearch: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val gateway by WifiHttp.gateway.collectAsState()
    val joined by WifiDiscovery.joined.collectAsState()
    var list by remember { mutableStateOf<List<JSONObject>?>(null) }
    var scanning by remember { mutableStateOf(false) }
    var asking by remember { mutableStateOf<JSONObject?>(null) }
    var added by remember { mutableStateOf<String?>(null) }       // the hotspot whose Stump was added
    var notStump by remember { mutableStateOf<String?>(null) }

    fun runScan() {
        if (scanning) return
        scanning = true
        scope.launch {
            val raw = WifiDiscovery.scan(ctx)
            list = runCatching { Engine.arr("discover_ssids", raw).objects() }.getOrDefault(emptyList())
            scanning = false
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (WifiDiscovery.canScan(ctx)) runScan() else list = emptyList()
    }
    // Location off: Android gives no scan results at all. Looked at again while the popup is
    // open (the user switches it on in the system's settings and comes back): then it scans.
    var locationOn by remember { mutableStateOf(WifiDiscovery.locationOn(ctx)) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1500)
            val now = WifiDiscovery.locationOn(ctx)
            if (now && !locationOn && WifiDiscovery.canScan(ctx)) runScan()
            locationOn = now
        }
    }
    LaunchedEffect(Unit) { if (WifiDiscovery.canScan(ctx) && locationOn) runScan() }

    // Joined: once its gateway is known, is a Stump there? Then remember the hotspot and add it.
    LaunchedEffect(joined.state, gateway) {
        if (joined.state != WifiDiscovery.Join.JOINED) return@LaunchedEffect
        val gw = gateway ?: return@LaunchedEffect
        delay(300)
        val p = Engine.obj("discover_probe", "http://$gw")
        if (p.optBoolean("stump")) {
            Engine.call("remember_stump_ssid", joined.ssid)
            val found = JSONObject().put("base", p.optString("base").ifBlank { "http://$gw" })
                .put("name", p.nameText()).put("lxmf", p.optString("lxmf"))
            Engine.obj("stumps_add_found", JSONArray().put(found).toString())
            added = joined.ssid
            Toast.makeText(ctx, ctx.getString(R.string.find_popup_added, 1), Toast.LENGTH_SHORT).show()
        } else notStump = joined.ssid
    }

    fun join(ap: JSONObject, password: String?) {
        if (Build.VERSION.SDK_INT < 29) {
            ctx.startActivity(Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        notStump = null; added = null
        WifiDiscovery.join(ctx, ap.optString("ssid"), password)
    }
    asking?.let { ap -> PasswordDialog(ap.optString("ssid"), onDismiss = { asking = null }) { pw -> asking = null; join(ap, pw) } }

    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = Amber.Panel, shape = Large,
        title = { Text(stringResource(R.string.find_popup_hotspots), style = MaterialTheme.typography.titleMedium) },
        text = {
            LazyColumn(Modifier.heightIn(max = 460.dp)) {
                item {
                    val l = list
                    when {
                        !WifiDiscovery.canScan(ctx) -> Row(verticalAlignment = Alignment.CenterVertically) {
                            Hint(stringResource(R.string.find_popup_hotspots_permission), Modifier.weight(1f))
                            TextLink(stringResource(R.string.find_popup_allow)) { permission.launch(WifiDiscovery.scanPermissions()) }
                        }
                        !locationOn -> Row(verticalAlignment = Alignment.CenterVertically) {
                            Hint(stringResource(R.string.hotspots_location_off), Modifier.weight(1f))
                            TextLink(stringResource(R.string.hotspots_location_on)) {
                                ctx.startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                            }
                        }
                        scanning || l == null -> Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 4.dp)) {
                            androidx.compose.material3.CircularProgressIndicator(Modifier.size(18.dp), color = Amber.Ember, strokeWidth = 2.dp)
                            Spacer(Modifier.width(10.dp))
                            Text(stringResource(R.string.find_popup_scanning), style = MaterialTheme.typography.bodySmall)
                        }
                        l.isEmpty() -> Hint(stringResource(R.string.find_popup_hotspots_none))
                    }
                }
                items(list ?: emptyList(), key = { it.optString("ssid") }) { ap ->
                    val ssid = ap.optString("ssid")
                    HotspotRow(ap, joinedNow = joined.ssid == ssid && joined.state == WifiDiscovery.Join.JOINED) {
                        if (ap.optBoolean("secured")) asking = ap else join(ap, null)
                    }
                }
                item {
                    when {
                        joined.state == WifiDiscovery.Join.ASKING -> Hint(stringResource(R.string.find_popup_joining))
                        added != null -> Row(verticalAlignment = Alignment.CenterVertically) {
                            Hint(stringResource(R.string.hotspot_added, added ?: ""), Modifier.weight(1f))
                            TextLink(stringResource(R.string.fab_search)) { onSearch() }
                        }
                        notStump != null -> Note(stringResource(R.string.hotspot_not_stump, notStump ?: ""), error = true)
                    }
                }
                if (list != null && !scanning && locationOn && WifiDiscovery.canScan(ctx)) item {
                    TextLink(stringResource(R.string.hotspot_rescan)) { runScan() }
                }
            }
        },
        confirmButton = {
            Text(stringResource(R.string.close), modifier = Modifier.clip(Small).clickable { onDismiss() }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
        })
}
