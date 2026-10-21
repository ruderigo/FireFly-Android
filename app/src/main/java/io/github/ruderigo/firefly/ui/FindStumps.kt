package io.github.ruderigo.firefly.ui

import android.content.Intent
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.objects
import io.github.ruderigo.firefly.net.WifiDiscovery
import io.github.ruderigo.firefly.net.WifiHttp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * Find Stumps: the one behind this Wi-Fi, Stump hotspots nearby (strongest
 * first), Stumps on this LAN, and Stumps heard on LoRa, each with bars for
 * how good the link is. Nothing here changes anything on a node: discovery
 * never polls the chat.
 */
@Composable
fun FindStumpsScreen(back: () -> Unit, go: (Route) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val gateway by WifiHttp.gateway.collectAsState()
    val local by WifiHttp.local.collectAsState()
    val joined by WifiDiscovery.joined.collectAsState()

    var here by remember { mutableStateOf<JSONObject?>(null) }
    var scan by remember { mutableStateOf<List<JSONObject>?>(null) }
    var scanning by remember { mutableStateOf(false) }
    var lan by remember { mutableStateOf<List<JSONObject>?>(null) }
    var sweeping by remember { mutableStateOf(false) }
    var asking by remember { mutableStateOf<JSONObject?>(null) }       // a secured hotspot: its password
    var notStump by remember { mutableStateOf<String?>(null) }

    // Open a Stump at an address: its Wi-Fi session, then its screen.
    fun openStump(base: String) {
        scope.launch {
            val key = withContext(Dispatchers.IO) { Engine.call("stump_connect_wifi", base) }
            if (key.isNotEmpty()) go(Route.Node(key))
        }
    }

    // On this network: STUMP? answers (Stump 1.0.0-beta.21+), the Stumps they heard, then
    // the /24 sweep for older ones. Started on its own: nothing to type.
    fun searchLan() {
        val l = local ?: return
        if (sweeping) return
        sweeping = true
        scope.launch {
            val skip = JSONArray().apply { gateway?.let { put(it) } }.toString()
            lan = Engine.arr("discover_lan", l.substringBefore('/'), l.substringAfter('/').toIntOrNull() ?: 24, skip).objects()
            sweeping = false
        }
    }
    LaunchedEffect(local) { if (local != null && lan == null) searchLan() }

    // Here: whatever answers at this Wi-Fi's gateway, checked again whenever the gateway changes.
    LaunchedEffect(gateway) {
        here = null
        val gw = gateway ?: return@LaunchedEffect
        here = Engine.obj("discover_probe", "http://$gw")
    }

    // A hotspot just joined: once its gateway is known, is it a Stump? Then remember it and open it.
    LaunchedEffect(joined.state, gateway) {
        if (joined.state != WifiDiscovery.Join.JOINED) return@LaunchedEffect
        val gw = gateway ?: return@LaunchedEffect
        delay(300)
        val p = Engine.obj("discover_probe", "http://$gw")
        here = p
        if (p.optBoolean("stump")) {
            withContext(Dispatchers.IO) { Engine.call("remember_stump_ssid", joined.ssid) }
            openStump("http://$gw")
        } else {
            notStump = joined.ssid
        }
    }

    fun runScan() {
        scanning = true
        scope.launch {
            val raw = WifiDiscovery.scan(ctx)
            scan = Engine.arr("discover_ssids", raw).objects()
            scanning = false
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) runScan() else { scan = emptyList() }
    }

    fun join(ap: JSONObject, password: String?) {
        val ssid = ap.optString("ssid")
        if (Build.VERSION.SDK_INT < 29) {
            ctx.startActivity(Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        notStump = null
        WifiDiscovery.join(ctx, ssid, password)
    }

    asking?.let { ap ->
        PasswordDialog(ap.optString("ssid"), onDismiss = { asking = null }) { pw -> asking = null; join(ap, pw) }
    }

    val mesh = engineData(emptyList<JSONObject>()) {
        Engine.arr("stump_nodes").objects().filter { "mesh" in (it.optJSONArray("transports")?.let { a -> List(a.length()) { i -> a.getString(i) } } ?: emptyList()) }
    }

    Column(Modifier.fillMaxSize()) {
        TopBar(stringResource(R.string.find_stumps), stringResource(R.string.find_stumps_sub), back)
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 24.dp)) {

            // ---------------------------------------------------------------- here
            item {
                Section(stringResource(R.string.find_here)) {
                    val h = here
                    when {
                        gateway == null -> Text(stringResource(R.string.find_not_wifi), style = MaterialTheme.typography.bodySmall)
                        h == null -> Text(stringResource(R.string.find_checking, gateway ?: ""), style = MaterialTheme.typography.bodySmall)
                        !h.optBoolean("stump") -> Text(stringResource(R.string.find_here_none, gateway ?: ""),
                            style = MaterialTheme.typography.bodySmall)
                        else -> StumpFound(h, onOpen = { openStump(h.optString("base")) })
                    }
                    if (joined.state == WifiDiscovery.Join.JOINED) {
                        Spacer(Modifier.height(8.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(stringResource(R.string.find_joined, joined.ssid), style = MaterialTheme.typography.labelMedium,
                                modifier = Modifier.weight(1f))
                            Button(stringResource(R.string.find_leave), primary = false) { WifiDiscovery.leave(ctx) }
                        }
                    }
                }
            }

            // ---------------------------------------------------------------- nearby hotspots
            item {
                Section(stringResource(R.string.find_nearby)) {
                    Text(stringResource(R.string.find_nearby_help), style = MaterialTheme.typography.bodySmall)
                    Spacer(Modifier.height(8.dp))
                    Button(stringResource(if (scanning) R.string.find_scanning else R.string.find_scan), enabled = !scanning) {
                        if (WifiDiscovery.canScan(ctx)) runScan() else permission.launch(WifiDiscovery.scanPermission())
                    }
                    when (joined.state) {
                        WifiDiscovery.Join.ASKING -> Note(stringResource(R.string.find_asking, joined.ssid))
                        WifiDiscovery.Join.FAILED -> Note(stringResource(R.string.find_join_failed, joined.ssid), error = true)
                        else -> {}
                    }
                    notStump?.let { Note(stringResource(R.string.find_not_stump, it), error = true) }
                    if (Build.VERSION.SDK_INT < 29) Note(stringResource(R.string.find_old_android))
                    val list = scan
                    if (list != null && list.isEmpty()) Note(stringResource(
                        if (WifiDiscovery.canScan(ctx)) R.string.find_scan_none else R.string.find_scan_permission))
                }
            }
            items(scan ?: emptyList(), key = { "ap" + it.optString("ssid") }) { ap ->
                HotspotRow(ap, joinedNow = joined.state == WifiDiscovery.Join.JOINED && joined.ssid == ap.optString("ssid")) {
                    if (ap.optBoolean("secured")) asking = ap else join(ap, null)
                }
            }

            // ---------------------------------------------------------------- the LAN
            item {
                Section(stringResource(R.string.find_lan)) {
                    Text(stringResource(R.string.find_lan_help, local ?: "—"), style = MaterialTheme.typography.bodySmall)
                    Spacer(Modifier.height(8.dp))
                    Button(stringResource(if (sweeping) R.string.find_lan_searching else R.string.find_lan_search),
                        enabled = !sweeping && local != null) { searchLan() }
                    if (lan?.isEmpty() == true) Note(stringResource(R.string.find_lan_none))
                }
            }
            items(lan ?: emptyList(), key = { "lan" + it.optString("base") }) { s ->
                Box(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) { StumpFound(s, onOpen = { openStump(s.optString("base")) }) }
            }

            // ---------------------------------------------------------------- LoRa
            item {
                Section(stringResource(R.string.find_lora)) {
                    if (mesh.isEmpty()) Text(stringResource(R.string.find_lora_none), style = MaterialTheme.typography.bodySmall)
                    else Text(stringResource(R.string.find_lora_help), style = MaterialTheme.typography.bodySmall)
                }
            }
            items(mesh, key = { "lora" + it.optString("key") }) { n -> LoraRow(n) { go(Route.Node(meshKey(n))) } }
        }
    }
}

/** The mesh key of a (possibly merged) Stumps row. */
private fun meshKey(n: JSONObject): String =
    if (n.optString("transport") == "mesh") n.optString("key")
    else n.optJSONArray("others")?.objects()?.firstOrNull { it.optString("transport") == "mesh" }?.optString("key") ?: n.optString("key")

/** 0-3 bars, ember when good; dim outlines for the missing ones. null: unknown. */
@Composable
fun Bars(bars: Int?, modifier: Modifier = Modifier) {
    Row(modifier.height(14.dp), verticalAlignment = Alignment.Bottom) {
        for (i in 1..3) {
            val on = bars != null && bars >= i
            Box(Modifier.padding(end = 2.dp).width(4.dp).height((4 + 4 * i).dp).clip(Small)
                .background(if (on) (if ((bars ?: 0) == 1) Amber.Action else Amber.Ember) else Amber.Border))
        }
    }
}

@Composable
private fun StumpFound(s: JSONObject, onOpen: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(Large).background(Amber.Panel).clickable(onClick = onOpen).padding(10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            val addr = s.optString("base").removePrefix("http://").removePrefix("https://")
            Text(s.optString("name").takeIf { it.isNotBlank() && it != "null" } ?: addr,
                style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val chain = s.optJSONObject("chain")
            Text(listOfNotNull(
                addr.takeIf { s.optString("name").isNotBlank() && s.optString("name") != "null" },
                when (s.optString("found_by")) {
                    "heard" -> stringResource(R.string.find_by_heard)
                    "sweep" -> stringResource(R.string.find_by_sweep)
                    else -> null
                },
                chain?.takeIf { it.optBoolean("enabled") }?.let {
                    stringResource(if (it.optString("role") == "root") R.string.chain_root else R.string.chain_member)
                },
                if (s.isNull("median_ms")) null
                else stringResource(R.string.find_steady_detail, s.optInt("median_ms"), s.optInt("lost"), s.optInt("samples")),
            ).joinToString("  "), style = MaterialTheme.typography.labelSmall)
            // From /node.json: the firmware release, and what the node offers (chat may be switched off).
            val features = s.optJSONArray("features")?.let { a -> List(a.length()) { a.getString(it) } }
            val release = s.optString("release").takeIf { it.isNotBlank() && it != "null" }
            if (release != null || features != null) Text(listOfNotNull(
                release,
                features?.takeIf { it.isNotEmpty() }?.joinToString(" · "),
                if (features != null && "chat" !in features) stringResource(R.string.find_chat_off) else null,
            ).joinToString("  "), style = MaterialTheme.typography.labelSmall.copy(
                color = if (features != null && "chat" !in features) Amber.Error else Amber.Dim))
        }
        Bars(if (s.has("steady_bars")) s.optInt("steady_bars") else null, Modifier.padding(horizontal = 8.dp))
        Text(stringResource(R.string.find_open), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
    }
}

@Composable
private fun HotspotRow(ap: JSONObject, joinedNow: Boolean, onJoin: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(enabled = !joinedNow, onClick = onJoin).padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Bars(if (ap.isNull("bars")) null else ap.optInt("bars"), Modifier.padding(end = 10.dp))
        Column(Modifier.weight(1f)) {
            Text((if (ap.optBoolean("secured")) "🔒 " else "") + ap.optString("ssid"), style = MaterialTheme.typography.bodyLarge,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(
                when (ap.optString("stump")) {
                    "yes" -> stringResource(R.string.find_tag_yes)
                    "likely" -> stringResource(R.string.find_tag_likely)
                    else -> stringResource(R.string.find_tag_maybe)
                },
                "${ap.optInt("rssi")} dBm",
                if (ap.optInt("freq") >= 4900) "5 GHz" else "2.4 GHz",
            ).joinToString("  "), style = MaterialTheme.typography.labelSmall.copy(
                color = if (ap.optString("stump") == "maybe") Amber.Dim else Amber.Muted))
        }
        Text(stringResource(if (joinedNow) R.string.find_joined_short else R.string.find_join),
            style = MaterialTheme.typography.labelLarge.copy(color = if (joinedNow) Amber.Dim else Amber.Ember))
    }
}

@Composable
private fun LoraRow(n: JSONObject, onOpen: () -> Unit) {
    val key = meshKey(n)
    val link = engineData(JSONObject(), key) { Engine.obj("stump_lora_link", key) }
    Row(Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Bars(if (link.isNull("bars") || !link.has("bars")) null else link.optInt("bars"), Modifier.padding(end = 10.dp))
        Column(Modifier.weight(1f)) {
            Text(n.optString("name").ifBlank { key.removePrefix("mesh:").take(12) }, style = MaterialTheme.typography.bodyLarge,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(
                if (link.isNull("hops") || !link.has("hops")) null else stringResource(R.string.hops, link.optInt("hops")),
                if (link.isNull("snr") || !link.has("snr")) null else "SNR ${"%.1f".format(link.optDouble("snr"))} dB",
                if (link.isNull("rssi") || !link.has("rssi")) null else "${link.optInt("rssi")} dBm",
            ).joinToString("  ").ifBlank { stringResource(R.string.find_lora_unmeasured) },
                style = MaterialTheme.typography.labelSmall)
        }
        Text(stringResource(R.string.find_open), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
    }
}

@Composable
private fun Note(text: String, error: Boolean = false) {
    Text(text, style = MaterialTheme.typography.labelMedium.copy(color = if (error) Amber.Error else Amber.Muted),
        modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun PasswordDialog(ssid: String, onDismiss: () -> Unit, onJoin: (String) -> Unit) {
    var pw by remember { mutableStateOf("") }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = Amber.Panel, shape = Large,
        title = { Text(ssid, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column {
                Text(stringResource(R.string.find_password_help), style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(8.dp))
                Field(pw, { pw = it }, Modifier.fillMaxWidth(), hint = stringResource(R.string.find_password_hint))
            }
        },
        confirmButton = {
            Text(stringResource(R.string.find_join), modifier = Modifier.clip(Small).clickable(enabled = pw.length >= 8) { onJoin(pw) }
                .padding(10.dp), style = MaterialTheme.typography.labelLarge.copy(color = if (pw.length >= 8) Amber.Ember else Amber.Dim))
        },
        dismissButton = {
            Text(stringResource(R.string.cancel), modifier = Modifier.clip(Small).clickable { onDismiss() }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        })
}
