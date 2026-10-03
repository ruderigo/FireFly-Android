package io.github.ruderigo.firefly.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import io.github.ruderigo.firefly.engine.EngineService
import io.github.ruderigo.firefly.engine.strings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

private val BANDWIDTHS = listOf(7_800, 10_400, 15_600, 20_800, 31_250, 41_700, 62_500, 125_000, 250_000, 500_000)

@Composable
fun SettingsScreen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val s = engineData(JSONObject()) { Engine.obj("settings_get") }
    val status by Engine.status.collectAsState()
    if (s.length() == 0) return
    val radio = s.getJSONObject("radio")
    fun save(patch: JSONObject) = Engine.fire("settings_set", patch.toString())

    var name by remember(s) { mutableStateOf(s.optString("display_name")) }
    var freq by remember(s) { mutableStateOf("%.3f".format(radio.optLong("frequency") / 1e6)) }
    var bw by remember(s) { mutableIntStateOf(radio.optInt("bandwidth")) }
    var sf by remember(s) { mutableStateOf(radio.optInt("spreading_factor").toString()) }
    var cr by remember(s) { mutableStateOf(radio.optInt("coding_rate").toString()) }
    var txp by remember(s) { mutableStateOf(radio.optInt("tx_power").toString()) }
    var tcp by remember(s) { mutableStateOf((s.optJSONArray("tcp_peers") ?: JSONArray()).strings().joinToString("\n")) }
    var hosts by remember(s) { mutableStateOf((s.optJSONArray("rnode_hosts") ?: JSONArray()).strings().joinToString("\n")) }
    var announce by remember(s) { mutableStateOf(s.optInt("announce_interval_min").toString()) }
    var notice by remember { mutableStateOf<String?>(null) }

    val export = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri != null) scope.launch(Dispatchers.IO) {
            val key = Engine.callBytes("identity_export") ?: return@launch
            ctx.contentResolver.openOutputStream(uri)?.use { it.write(key) }
            withContext(Dispatchers.Main) { notice = ctx.getString(R.string.identity_saved) }
        }
    }
    val import = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch(Dispatchers.IO) {
            val bytes = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return@launch
            val hash = Engine.call("identity_import", bytes)
            withContext(Dispatchers.Main) {
                notice = if (hash.length == 32) ctx.getString(R.string.identity_imported, hash) else ctx.getString(R.string.identity_bad)
            }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        notice?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium.copy(color = Amber.Ember), modifier = Modifier.padding(12.dp))
        }
        Section(stringResource(R.string.your_name)) {
            Field(name, { name = it }, Modifier.fillMaxWidth(), onDone = { save(JSONObject().put("display_name", name)) })
            Text(stringResource(R.string.your_name_help), style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
            Button(stringResource(R.string.save), modifier = Modifier.padding(top = 8.dp)) { save(JSONObject().put("display_name", name)) }
        }

        Section(stringResource(R.string.radio)) {
            Toggle(stringResource(R.string.radio_enabled), radio.optBoolean("enabled")) {
                save(JSONObject().put("radio", JSONObject().put("enabled", it)))
            }
            Toggle(stringResource(R.string.keep_awake), radio.optBoolean("keep_awake", true)) {
                save(JSONObject().put("radio", JSONObject().put("keep_awake", it)))
            }
            Text(stringResource(R.string.radio_match_warning), style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 6.dp))
            Labeled(stringResource(R.string.frequency_mhz)) { Field(freq, { freq = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
            Labeled(stringResource(R.string.bandwidth)) {
                Row(Modifier.fillMaxWidth()) {
                    listOf(62_500, 125_000, 250_000, 500_000).forEach { b ->
                        Chip("${b / 1000}", b == bw) { bw = b }
                    }
                }
            }
            Row {
                Labeled("SF", Modifier.weight(1f)) { Field(sf, { sf = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
                Spacer(Modifier.width(8.dp))
                Labeled("CR 4/", Modifier.weight(1f)) { Field(cr, { cr = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
                Spacer(Modifier.width(8.dp))
                Labeled("dBm", Modifier.weight(1f)) { Field(txp, { txp = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
            }
            Row(Modifier.padding(top = 10.dp)) {
                Button(stringResource(R.string.apply)) {
                    val f = freq.replace(',', '.').toDoubleOrNull()
                    if (f != null && bw in BANDWIDTHS) save(JSONObject().put("radio", JSONObject()
                        .put("frequency", (f * 1e6).toLong()).put("bandwidth", bw)
                        .put("spreading_factor", sf.toIntOrNull() ?: 8).put("coding_rate", cr.toIntOrNull() ?: 5)
                        .put("tx_power", txp.toIntOrNull() ?: 7)))
                }
                Spacer(Modifier.width(8.dp))
                Button(stringResource(R.string.stump_defaults), primary = false) {
                    save(JSONObject().put("radio", JSONObject().put("frequency", 915_000_000).put("bandwidth", 125_000)
                        .put("spreading_factor", 8).put("coding_rate", 5).put("tx_power", 7)))
                }
            }
            OnAir(status)
            Text(stringResource(R.string.stump_defaults_help), style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 4.dp))
        }

        Section(stringResource(R.string.other_links)) {
            Toggle(stringResource(R.string.auto_interface), s.optBoolean("auto_interface")) { save(JSONObject().put("auto_interface", it)) }
            Labeled(stringResource(R.string.tcp_peers)) { Field(tcp, { tcp = it }, Modifier.fillMaxWidth(), hint = "host:4242", singleLine = false, mono = true) }
            Labeled(stringResource(R.string.wifi_rnodes)) { Field(hosts, { hosts = it }, Modifier.fillMaxWidth(), hint = "10.0.0.50", singleLine = false, mono = true) }
            Button(stringResource(R.string.save), modifier = Modifier.padding(top = 8.dp)) {
                fun lines(t: String) = JSONArray(t.lines().map { it.trim() }.filter { it.isNotEmpty() })
                save(JSONObject().put("tcp_peers", lines(tcp)).put("rnode_hosts", lines(hosts)))
            }
        }

        Section(stringResource(R.string.delivery)) {
            Labeled(stringResource(R.string.announce_every)) {
                Field(announce, { announce = it }, Modifier.width(100.dp), mono = true, number = true,
                    onDone = { save(JSONObject().put("announce_interval_min", announce.toIntOrNull() ?: 30)) })
            }
            Text(stringResource(R.string.announce_help), style = MaterialTheme.typography.bodySmall)
            Labeled(stringResource(R.string.propagation)) {
                Row {
                    listOf("auto" to R.string.prop_auto, "off" to R.string.prop_off).forEach { (v, label) ->
                        Chip(stringResource(label), s.optString("propagation_mode") == v) { save(JSONObject().put("propagation_mode", v)) }
                    }
                }
            }
            Toggle(stringResource(R.string.fallback_prop), s.optBoolean("fallback_to_propagation")) {
                save(JSONObject().put("fallback_to_propagation", it))
            }
        }

        Section(stringResource(R.string.identity)) {
            Text(stringResource(R.string.identity_help), style = MaterialTheme.typography.bodyMedium)
            Row(Modifier.padding(top = 10.dp)) {
                Button(stringResource(R.string.backup_identity)) { export.launch("firefly-identity") }
                Spacer(Modifier.width(8.dp))
                Button(stringResource(R.string.restore_identity), primary = false) { import.launch(arrayOf("*/*")) }
            }
        }

        Section(stringResource(R.string.about)) {
            Text(stringResource(R.string.about_text), style = MaterialTheme.typography.bodySmall)
            Button(stringResource(R.string.quit), primary = false, modifier = Modifier.padding(top = 10.dp)) { EngineService.quit(ctx) }
        }
        Spacer(Modifier.height(24.dp))
    }
}

/**
 * What the RNode itself reports it is using, compared with what's saved.
 * Shows "applying" during the retune, and the RNode's reason if it refuses.
 */
@Composable
private fun OnAir(status: JSONObject) {
    val r = status.optJSONObject("radio") ?: JSONObject()
    val want = status.optJSONObject("radio_wanted") ?: JSONObject()
    val state = r.optString("state")
    val (text, color) = when {
        state == "online" && !r.isNull("txpower") -> {
            val air = "%.3f MHz · %d kHz · SF%d · 4/%d · %d dBm".format(r.optDouble("frequency") / 1e6,
                r.optInt("bandwidth") / 1000, r.optInt("sf"), r.optInt("cr"), r.optInt("txpower"))
            val same = r.optLong("frequency") == want.optLong("frequency") && r.optInt("bandwidth") == want.optInt("bandwidth") &&
                r.optInt("sf") == want.optInt("spreading_factor") && r.optInt("cr") == want.optInt("coding_rate") &&
                r.optInt("txpower") == want.optInt("tx_power")
            if (same) stringResource(R.string.on_air, air) to Amber.Ember
            else stringResource(R.string.on_air_applying, air) to Amber.Muted
        }
        state == "connecting" -> stringResource(R.string.radio_applying) to Amber.Muted
        state == "refused" -> r.optString("detail") to Amber.Error
        else -> stringResource(R.string.not_on_air) to Amber.Dim
    }
    Text(text, style = MaterialTheme.typography.labelMedium.copy(color = color), modifier = Modifier.padding(top = 10.dp))
}

@Composable
private fun Toggle(label: String, on: Boolean, set: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().clip(Small).clickable { set(!on) }.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Box(Modifier.width(44.dp).height(24.dp).clip(Medium).background(if (on) Amber.Ember else Amber.Row)
            .border(1.dp, Amber.Border, Medium).padding(3.dp), contentAlignment = if (on) Alignment.CenterEnd else Alignment.CenterStart) {
            Box(Modifier.size(18.dp).clip(Small).background(if (on) Amber.Background else Amber.Muted))
        }
    }
}

@Composable
private fun Chip(label: String, selected: Boolean, onClick: () -> Unit) {
    Text(label, modifier = Modifier.padding(end = 6.dp).clip(Small).border(1.dp, if (selected) Amber.Ember else Amber.Border, Small)
        .background(if (selected) Amber.Ember.copy(alpha = 0.15f) else Amber.Panel).clickable(onClick = onClick)
        .padding(horizontal = 10.dp, vertical = 6.dp),
        style = MaterialTheme.typography.labelMedium.copy(color = if (selected) Amber.Ember else Amber.Muted))
}

@Composable
private fun Labeled(label: String, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(modifier.padding(vertical = 4.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(bottom = 4.dp))
        content()
    }
}
