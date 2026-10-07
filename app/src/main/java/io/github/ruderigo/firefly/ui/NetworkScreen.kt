package io.github.ruderigo.firefly.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.ui.unit.dp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.objects
import io.github.ruderigo.firefly.radio.BlePairing
import io.github.ruderigo.firefly.radio.RadioLinks
import org.json.JSONObject

@Composable
fun NetworkScreen(status: JSONObject, go: (Route) -> Unit) {
    val ctx = LocalContext.current
    val settings = engineData(JSONObject()) { Engine.obj("settings_get") }
    val r = status.optJSONObject("radio") ?: JSONObject()
    val state = r.optString("state", "off")
    val paired = settings.optJSONObject("radio")?.optJSONArray("ble_devices")?.objects() ?: emptyList()
    var usbWaiting by remember { mutableStateOf(emptyList<String>()) }
    LaunchedEffect(status) {
        usbWaiting = try { Engine.links.usbNeedingPermission().map { RadioLinks.label(it) } } catch (_: Exception) { emptyList() }
    }

    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Section(stringResource(R.string.radio)) {
                val stateText = when (state) {
                    "online" -> stringResource(R.string.radio_state_online)
                    "connecting" -> stringResource(R.string.radio_state_connecting)
                    "searching" -> stringResource(R.string.radio_state_searching)
                    "refused" -> stringResource(R.string.radio_state_refused)
                    "no devices" -> stringResource(R.string.radio_state_none)
                    else -> stringResource(R.string.radio_state_off)
                }
                Text(stateText, style = MaterialTheme.typography.titleMedium.copy(
                    color = when (state) { "online" -> Amber.Ember; "refused" -> Amber.Error; else -> Amber.Text }))
                r.optString("detail").takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 2.dp))
                }
                Spacer(Modifier.height(8.dp))
                if (state == "online") {
                    KeyValue(stringResource(R.string.link), when (r.optString("kind")) {
                        "ble" -> "Bluetooth LE"; "usb" -> "USB"; else -> "Wi-Fi" })
                    KeyValue(stringResource(R.string.board), r.optString("board"))
                    KeyValue(stringResource(R.string.channel), "%.3f MHz  %d kHz  SF%d  4/%d  %d dBm".format(
                        r.optDouble("frequency", 0.0) / 1e6, r.optInt("bandwidth") / 1000, r.optInt("sf"), r.optInt("cr"), r.optInt("txpower")))
                    if (!r.isNull("rssi")) KeyValue(stringResource(R.string.last_packet), "%d dBm  SNR %.1f dB".format(r.optInt("rssi"), r.optDouble("snr")))
                    if (!r.isNull("noise_floor")) KeyValue(stringResource(R.string.noise_floor), "${r.optInt("noise_floor")} dBm")
                    KeyValue(stringResource(R.string.channel_load), "%.1f %%  airtime %.1f %%".format(r.optDouble("channel_load", 0.0), r.optDouble("airtime", 0.0)))
                    if (!r.isNull("battery")) KeyValue(stringResource(R.string.battery), "${r.optInt("battery")} %" + if (r.optBoolean("charging")) " ⚡" else "")
                    KeyValue(stringResource(R.string.traffic), "↓ %s  ↑ %s".format(bytes(r.optLong("rx")), bytes(r.optLong("tx"))))
                }
                Row(Modifier.padding(top = 10.dp)) {
                    Button(stringResource(R.string.search_now)) { Engine.searchRadio() }
                    Spacer(Modifier.width(8.dp))
                    Button(stringResource(R.string.pair_ble), primary = false) { go(Route.Pair) }
                }
            }
        }
        if (usbWaiting.isNotEmpty()) item {
            Section(stringResource(R.string.usb_permission)) {
                usbWaiting.forEach { Text(it, style = MaterialTheme.typography.bodyMedium) }
                Spacer(Modifier.height(6.dp))
                Button(stringResource(R.string.allow_usb)) {
                    Engine.links.usbNeedingPermission().forEach { Engine.links.requestUsbPermission(it) }
                }
            }
        }
        if (paired.isNotEmpty()) item {
            Section(stringResource(R.string.paired_rnodes)) {
                paired.forEach { d ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(d.optString("name"), style = MaterialTheme.typography.bodyLarge)
                            Text(d.optString("address"), style = MaterialTheme.typography.labelSmall)
                        }
                        Button(stringResource(R.string.forget), primary = false) { Engine.fire("remove_ble_device", d.optString("address")) }
                    }
                }
            }
        }
        item {
            Section(stringResource(R.string.you_on_network)) {
                val addr = status.optString("address")
                KeyValue(stringResource(R.string.lxmf_address), shortHash(addr))
                KeyValue(stringResource(R.string.stump_nick), status.optString("stump_nick"))
                status.optString("note").takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error), modifier = Modifier.padding(vertical = 4.dp))
                }
                Row(Modifier.padding(top = 8.dp)) {
                    Button(stringResource(R.string.copy_address), primary = false) {
                        ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("LXMF", addr))
                    }
                    Spacer(Modifier.width(8.dp))
                    Button(stringResource(R.string.announce_now)) { Engine.fire("announce") }
                }
            }
        }
        item {
            val p = status.optJSONObject("propagation") ?: JSONObject()
            Section(stringResource(R.string.propagation)) { PropagationSummary(p, go) }
        }
        item { Text(stringResource(R.string.interfaces), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(12.dp)) }
        items((status.optJSONArray("interfaces")?.objects() ?: emptyList())) { i ->
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).clip(Small).background(if (i.optBoolean("online")) Amber.Ember else Amber.Dim))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(i.optString("name"), style = MaterialTheme.typography.labelLarge.copy(fontWeight = null))
                    i.optString("error").takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error)) }
                }
                Text("↓ %s  ↑ %s".format(bytes(i.optLong("rx")), bytes(i.optLong("tx"))), style = MaterialTheme.typography.labelSmall)
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

private fun bytes(n: Long) = when {
    n < 1024 -> "$n B"; n < 1024 * 1024 -> "%.1f KB".format(n / 1024.0); else -> "%.1f MB".format(n / 1048576.0)
}

/** Find an RNode advertising over Bluetooth LE and pair with it. */
@Composable
fun PairScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val pairing = remember { BlePairing(ctx) }
    val found by pairing.found.collectAsState()
    val state by pairing.state.collectAsState()
    DisposableEffect(Unit) {
        if (pairing.available()) pairing.startScan()
        onDispose { pairing.stopScan() }
    }
    Column(Modifier.fillMaxSize()) {
        TopBar(stringResource(R.string.pair_ble), null, back) {
            if (state != "scanning" && pairing.available()) Button(stringResource(R.string.scan), primary = false) { pairing.startScan() }
        }
        LazyColumn(Modifier.weight(1f)) {
            item {
                Section(stringResource(R.string.before_pairing)) {
                    Text(stringResource(R.string.pairing_help), style = MaterialTheme.typography.bodyMedium)
                }
            }
            if (!pairing.available()) item { Empty(stringResource(R.string.bt_unavailable)) }
            item {
                val msg = when {
                    state == "scanning" -> stringResource(R.string.scanning)
                    state.startsWith("pairing:") -> stringResource(R.string.pairing_now)
                    state.startsWith("paired:") -> stringResource(R.string.paired_ok)
                    state.startsWith("failed:") -> state.removePrefix("failed:")
                    else -> ""
                }
                if (msg.isNotEmpty()) Text(msg, modifier = Modifier.padding(12.dp), style = MaterialTheme.typography.bodyMedium.copy(
                    color = if (state.startsWith("failed")) Amber.Error else if (state.startsWith("paired")) Amber.Ember else Amber.Muted))
            }
            items(found, key = { it.address }) { d ->
                Row(Modifier.fillMaxWidth().rowClick {
                    pairing.pair(d.address) { addr, name -> Engine.fire("add_ble_device", addr, name) }
                }, verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(d.name, style = MaterialTheme.typography.titleMedium)
                        Text("${d.address}  ${d.rssi} dBm", style = MaterialTheme.typography.labelSmall)
                    }
                    Text(if (d.bonded) stringResource(R.string.use_it) else stringResource(R.string.pair),
                        modifier = Modifier.clip(Medium).border(1.dp, Amber.Ember, Medium).padding(horizontal = 10.dp, vertical = 6.dp),
                        style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
                }
            }
        }
    }
}


/** The propagation node in use: who, how it was chosen, its limits, the last sync. */
@Composable
private fun PropagationSummary(p: JSONObject, go: (Route) -> Unit) {
    val node = p.optString("node").takeIf { it.isNotBlank() && it != "null" }
    val name = p.optString("name").takeIf { it.isNotBlank() && it != "null" }
    val stump = p.optString("stump").takeIf { it.isNotBlank() && it != "null" }
    KeyValue(stringResource(R.string.node), when {
        node == null -> stringResource(if (p.optString("mode") == "off") R.string.prop_mode_off else R.string.none_heard)
        else -> (name ?: shortHash(node)) + if (stump != null) "  · Stump" else ""
    })
    KeyValue(stringResource(R.string.prop_choice), stringResource(when (p.optString("mode")) {
        "manual" -> R.string.prop_mode_manual; "off" -> R.string.prop_mode_off; else -> R.string.prop_mode_auto }))
    if (node != null) {
        if (!p.isNull("transfer_limit_kb")) KeyValue(stringResource(R.string.prop_limit), "${p.optInt("transfer_limit_kb")} KB")
        val last = p.optDouble("last_sync", 0.0)
        KeyValue(stringResource(R.string.sync), p.optString("state") +
            (if (last > 0) "  · " + clock(last) else "") +
            (if (!p.isNull("last_result") && p.optInt("last_result") > 0)
                "  · " + stringResource(R.string.prop_received, p.optInt("last_result")) else ""))
    }
    Text(stringResource(R.string.prop_help), style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
    Row(Modifier.padding(top = 10.dp)) {
        Button(stringResource(R.string.sync_now), enabled = node != null) { Engine.fire("sync") }
        Spacer(Modifier.width(8.dp))
        Button(stringResource(R.string.prop_choose), primary = false) { go(Route.Propagation) }
    }
}

/**
 * Choose where messages for offline people are left, and where FireFly
 * collects its own: automatic (a Stump's node first, since Stump nodes don't
 * peer yet and everyone around one should use the same), a heard node, an
 * address pasted from a node's /admin, or off.
 */
@Composable
fun PropagationScreen(back: () -> Unit) {
    val nodes = engineData(emptyList<JSONObject>()) { Engine.arr("propagation_nodes").objects() }
    val status by Engine.status.collectAsState()
    LaunchedEffect(Unit) { while (true) { Engine.refreshStatus(); kotlinx.coroutines.delay(2000) } }
    val mode = status.optJSONObject("propagation")?.optString("mode") ?: "auto"
    var addr by remember { mutableStateOf("") }
    var error by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        TopBar(stringResource(R.string.prop_screen_title), null, back)
        LazyColumn(Modifier.weight(1f)) {
            item {
                Text(stringResource(R.string.prop_screen_help), style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(12.dp))
            }
            item { ChoiceRow(stringResource(R.string.prop_mode_auto), stringResource(R.string.prop_auto_help), mode == "auto") {
                Engine.fire("use_propagation_node", "auto") } }
            item { ChoiceRow(stringResource(R.string.prop_mode_off), stringResource(R.string.prop_off_help), mode == "off") {
                Engine.fire("use_propagation_node", "off") } }
            item { Text(stringResource(R.string.prop_heard), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(12.dp)) }
            if (nodes.isEmpty()) item { Empty(stringResource(R.string.prop_none_yet)) }
            items(nodes, key = { it.optString("hash") }) { n ->
                val enabled = n.isNull("enabled") || n.optBoolean("enabled")
                val details = buildList {
                    add(shortHash(n.optString("hash")))
                    if (!n.isNull("hops")) add(hopsLabel(n))
                    if (!n.isNull("transfer_limit_kb")) add("${n.optInt("transfer_limit_kb")} KB")
                    if (!n.isNull("stamp_cost")) add(stringResource(R.string.prop_cost, n.optInt("stamp_cost")))
                    if (n.optBoolean("waiting")) add(stringResource(R.string.prop_waiting))
                    else if (!enabled) add(stringResource(R.string.prop_switched_off))
                }.joinToString("  ")
                val title = (n.optString("name").takeIf { it.isNotBlank() && it != "null" } ?: stringResource(R.string.propagation)) +
                    if (!n.isNull("stump") && n.optString("stump").isNotBlank()) "  · Stump" else ""
                ChoiceRow(title, details, mode == "manual" && n.optBoolean("selected"), selectedHint = n.optBoolean("selected")) {
                    Engine.fire("use_propagation_node", n.optString("hash"))
                }
            }
            item {
                Section(stringResource(R.string.prop_by_address)) {
                    Field(addr, { addr = it.trim(); error = false }, Modifier.fillMaxWidth(), hint = stringResource(R.string.prop_addr_hint), mono = true)
                    if (error) Text(stringResource(R.string.prop_bad_addr), style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error))
                    Button(stringResource(R.string.prop_use), modifier = Modifier.padding(top = 8.dp), enabled = addr.isNotBlank()) {
                        val clean = addr.lowercase().removePrefix("<").removeSuffix(">")
                        if (clean.length == 32 && clean.all { it in "0123456789abcdef" }) {
                            Engine.fire("use_propagation_node", clean); addr = ""
                        } else error = true
                    }
                }
            }
        }
    }
}

@Composable
private fun ChoiceRow(title: String, detail: String, chosen: Boolean, selectedHint: Boolean = false, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().rowClick(onClick), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(14.dp).clip(Small).border(1.dp, if (chosen) Amber.Ember else Amber.Border, Small)
            .background(if (chosen) Amber.Ember else Amber.Background))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.labelSmall)
        }
        if (selectedHint && !chosen) Text(stringResource(R.string.prop_in_use), style = MaterialTheme.typography.labelSmall.copy(color = Amber.Ember))
    }
}
