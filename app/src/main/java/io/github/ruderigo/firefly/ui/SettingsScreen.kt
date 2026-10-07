package io.github.ruderigo.firefly.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import io.github.ruderigo.firefly.audio.Codec2
import io.github.ruderigo.firefly.audio.Opus
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.EngineService
import io.github.ruderigo.firefly.engine.strings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

private val BANDWIDTHS = listOf(62_500, 125_000, 250_000, 500_000)

/**
 * One rule for every section:
 *  - a section WITH a Save button: nothing in it changes until Save
 *    (Discard puts back what's saved);
 *  - a section WITHOUT one: each tap applies at once.
 * Every change is confirmed by a short banner, shown only after the engine
 * has actually taken it (or saying it failed).
 */
@Composable
fun SettingsScreen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    // Loaded on entry and after each save, never on unrelated activity
    // (a message arriving must not wipe what's being typed).
    var reloadKey by remember { mutableIntStateOf(0) }
    val s by produceState(JSONObject(), reloadKey) { value = Engine.obj("settings_get") }
    val status by Engine.status.collectAsState()
    var banner by remember { mutableStateOf<Pair<String, Boolean>?>(null) }     // text, ok
    var bannerId by remember { mutableIntStateOf(0) }
    fun show(text: String, ok: Boolean = true) { banner = text to ok; bannerId++ }

    /** Apply a change through the engine; confirm only once it has taken it. */
    fun commit(okText: String, fn: String, vararg args: Any?) {
        scope.launch {
            val ok = withContext(Dispatchers.IO) { Engine.callOk(fn, *args) }
            if (ok) { show(okText); reloadKey++ } else show(ctx.getString(R.string.settings_failed), ok = false)
        }
    }
    fun commitSettings(patch: JSONObject, okText: String = ctx.getString(R.string.settings_saved)) =
        commit(okText, "settings_set", patch.toString())

    val export = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri != null) scope.launch(Dispatchers.IO) {
            val key: ByteArray? = Engine.callBytes("identity_export")
            var written = false
            if (key != null) {
                try {
                    val out = ctx.contentResolver.openOutputStream(uri)
                    if (out != null) { out.use { it.write(key) }; written = true }
                } catch (_: Exception) { written = false }
            }
            withContext(Dispatchers.Main) {
                if (written) show(ctx.getString(R.string.identity_saved)) else show(ctx.getString(R.string.settings_failed), false)
            }
        }
    }
    val import = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch(Dispatchers.IO) {
            val bytes: ByteArray? = try { ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() } } catch (_: Exception) { null }
            val hash = if (bytes != null) Engine.call("identity_import", bytes) else ""
            withContext(Dispatchers.Main) {
                if (hash.length == 32) show(ctx.getString(R.string.identity_imported, hash))
                else show(ctx.getString(R.string.identity_bad), false)
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        if (s.length() > 0) Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            val radio = s.getJSONObject("radio")

            // ---------------------------------------------------------- Your name (Save)
            val savedName = s.optString("display_name")
            var name by remember(s) { mutableStateOf(savedName) }
            Section(stringResource(R.string.your_name)) {
                Field(name, { name = it }, Modifier.fillMaxWidth())
                Text(stringResource(R.string.your_name_help), style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
                SaveRow(dirty = name.trim() != savedName, valid = name.isNotBlank(), problem = R.string.name_invalid,
                    onDiscard = { name = savedName }) {
                    commitSettings(JSONObject().put("display_name", name.trim()), ctx.getString(R.string.settings_saved_name))
                }
            }

            // ---------------------------------------------------------- LoRa radio (instant)
            Section(stringResource(R.string.radio)) {
                Toggle(stringResource(R.string.radio_enabled), radio.optBoolean("enabled")) {
                    commitSettings(JSONObject().put("radio", JSONObject().put("enabled", it)))
                }
                Toggle(stringResource(R.string.keep_awake), radio.optBoolean("keep_awake", true)) {
                    commitSettings(JSONObject().put("radio", JSONObject().put("keep_awake", it)))
                }
            }

            // ---------------------------------------------------------- Radio channel (Save)
            val savedFreq = "%.3f".format(java.util.Locale.ROOT, radio.optLong("frequency") / 1e6)
            val savedBw = radio.optInt("bandwidth")
            val savedSf = radio.optInt("spreading_factor").toString()
            val savedCr = radio.optInt("coding_rate").toString()
            val savedTxp = radio.optInt("tx_power").toString()
            var freq by remember(s) { mutableStateOf(savedFreq) }
            var bw by remember(s) { mutableIntStateOf(savedBw) }
            var sf by remember(s) { mutableStateOf(savedSf) }
            var cr by remember(s) { mutableStateOf(savedCr) }
            var txp by remember(s) { mutableStateOf(savedTxp) }
            val f = freq.replace(',', '.').toDoubleOrNull()
            val radioValid = f != null && f in 137.0..1020.0 && (sf.toIntOrNull() ?: 0) in 5..12 &&
                (cr.toIntOrNull() ?: 0) in 5..8 && (txp.toIntOrNull() ?: -1) in 0..22
            val radioDirty = freq.replace(',', '.').toDoubleOrNull()?.let { "%.3f".format(java.util.Locale.ROOT, it) } != savedFreq ||
                bw != savedBw || sf != savedSf || cr != savedCr || txp != savedTxp
            Section(stringResource(R.string.radio_channel)) {
                Text(stringResource(R.string.radio_match_warning), style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 6.dp))
                Labeled(stringResource(R.string.frequency_mhz)) { Field(freq, { freq = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
                Labeled(stringResource(R.string.bandwidth)) {
                    Row(Modifier.fillMaxWidth()) { BANDWIDTHS.forEach { b -> Chip("${b / 1000}", b == bw) { bw = b } } }
                }
                Row {
                    Labeled("SF", Modifier.weight(1f)) { Field(sf, { sf = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
                    Spacer(Modifier.width(8.dp))
                    Labeled("CR 4/", Modifier.weight(1f)) { Field(cr, { cr = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
                    Spacer(Modifier.width(8.dp))
                    Labeled("dBm", Modifier.weight(1f)) { Field(txp, { txp = it }, Modifier.fillMaxWidth(), mono = true, number = true) }
                }
                Text(stringResource(R.string.stump_defaults_fill), modifier = Modifier.padding(top = 6.dp).clip(Small)
                    .clickable { freq = "915.000"; bw = 125_000; sf = "8"; cr = "5"; txp = "7" }.padding(vertical = 4.dp),
                    style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
                OnAir(status)
                SaveRow(dirty = radioDirty, valid = radioValid, problem = R.string.radio_invalid,
                    onDiscard = { freq = savedFreq; bw = savedBw; sf = savedSf; cr = savedCr; txp = savedTxp }) {
                    commitSettings(JSONObject().put("radio", JSONObject()
                        .put("frequency", Math.round((f ?: 915.0) * 1e6)).put("bandwidth", bw)
                        .put("spreading_factor", sf.toInt()).put("coding_rate", cr.toInt()).put("tx_power", txp.toInt())),
                        ctx.getString(R.string.settings_saved_radio))
                }
            }

            // ---------------------------------------------------------- Announcing (Save)
            val savedAnnounce = s.optInt("announce_interval_min").toString()
            var announce by remember(s) { mutableStateOf(savedAnnounce) }
            Section(stringResource(R.string.announcing)) {
                Labeled(stringResource(R.string.announce_every)) {
                    Field(announce, { announce = it }, Modifier.width(100.dp), mono = true, number = true)
                }
                Text(stringResource(R.string.announce_help), style = MaterialTheme.typography.bodySmall)
                SaveRow(dirty = announce != savedAnnounce, valid = (announce.toIntOrNull() ?: -1) in 0..1440,
                    problem = R.string.announce_invalid, onDiscard = { announce = savedAnnounce }) {
                    commitSettings(JSONObject().put("announce_interval_min", announce.toInt()))
                }
            }

            // ---------------------------------------------------------- Offline messages (instant)
            Section(stringResource(R.string.propagation)) {
                Row {
                    listOf("auto" to R.string.prop_auto, "off" to R.string.prop_off).forEach { (v, label) ->
                        Chip(stringResource(label), s.optString("propagation_mode") == v) {
                            commit(ctx.getString(R.string.settings_saved), "use_propagation_node", v)
                        }
                    }
                }
                Toggle(stringResource(R.string.fallback_prop), s.optBoolean("fallback_to_propagation")) {
                    commitSettings(JSONObject().put("fallback_to_propagation", it))
                }
            }

            // ---------------------------------------------------------- Voice note quality (instant)
            Section(stringResource(R.string.voice_quality)) {
                val current = s.optInt("voice_mode", 0)
                val choices = listOf(0 to stringResource(R.string.voice_auto), 3 to "700C", 4 to "1200", 7 to "1600",
                    8 to "2400", 9 to "3200", Opus.MODE_OGG to "Opus")
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
                    choices.forEach { (mode, label) ->
                        Chip(label, current == mode) { commitSettings(JSONObject().put("voice_mode", mode)) }
                    }
                }
                Text(stringResource(when (current) {
                    0 -> R.string.voice_q_auto; 3 -> R.string.voice_q_700; 7 -> R.string.voice_q_1600
                    8 -> R.string.voice_q_2400; 9 -> R.string.voice_q_3200; Opus.MODE_OGG -> R.string.voice_q_opus
                    else -> R.string.voice_q_1200 }), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp))
                when (current) {
                    0 -> {}
                    Opus.MODE_OGG -> Text(stringResource(R.string.voice_q_opus_cost), style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(top = 4.dp))
                    else -> Text(stringResource(R.string.voice_q_cost, "%.1f".format(Codec2.perPacketSeconds(current)),
                        "%.1f".format(Codec2.fifteenSecondBytes(current) / 1000.0)),
                        style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(top = 4.dp))
                }
                Text(stringResource(R.string.voice_q_help), style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
            }

            // ---------------------------------------------------------- Other links (Save)
            val savedAuto = s.optBoolean("auto_interface")
            val savedTcp = (s.optJSONArray("tcp_peers") ?: JSONArray()).strings().joinToString("\n")
            val savedHosts = (s.optJSONArray("rnode_hosts") ?: JSONArray()).strings().joinToString("\n")
            var auto by remember(s) { mutableStateOf(savedAuto) }
            var tcp by remember(s) { mutableStateOf(savedTcp) }
            var hosts by remember(s) { mutableStateOf(savedHosts) }
            fun lines(t: String) = t.lines().map { it.trim() }.filter { it.isNotEmpty() }
            val tcpValid = lines(tcp).all { l -> l.substringAfterLast(':', "").toIntOrNull()?.let { it in 1..65535 } == true &&
                l.substringBeforeLast(':').isNotBlank() }
            Section(stringResource(R.string.other_links)) {
                Toggle(stringResource(R.string.auto_interface), auto) { auto = it }
                Labeled(stringResource(R.string.tcp_peers)) { Field(tcp, { tcp = it }, Modifier.fillMaxWidth(), hint = "host:4242", singleLine = false, mono = true) }
                Labeled(stringResource(R.string.wifi_rnodes)) { Field(hosts, { hosts = it }, Modifier.fillMaxWidth(), hint = "10.0.0.50", singleLine = false, mono = true) }
                SaveRow(dirty = auto != savedAuto || lines(tcp) != lines(savedTcp) || lines(hosts) != lines(savedHosts),
                    valid = tcpValid, problem = R.string.tcp_invalid,
                    onDiscard = { auto = savedAuto; tcp = savedTcp; hosts = savedHosts }) {
                    commitSettings(JSONObject().put("auto_interface", auto)
                        .put("tcp_peers", JSONArray(lines(tcp))).put("rnode_hosts", JSONArray(lines(hosts))),
                        ctx.getString(R.string.settings_saved_links))
                }
            }

            // ---------------------------------------------------------- Blocked people (actions)
            val blockedPeople by produceState(emptyList<JSONObject>(), reloadKey) {
                value = Engine.arr("blocked").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
            }
            var blockAddr by remember { mutableStateOf("") }
            Section(stringResource(R.string.blocked_people)) {
                if (blockedPeople.isEmpty()) Text(stringResource(R.string.blocked_none), style = MaterialTheme.typography.bodySmall)
                blockedPeople.forEach { b ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(b.optString("name").takeIf { it.isNotBlank() && it != "null" } ?: shortHash(b.optString("hash")),
                                style = MaterialTheme.typography.bodyLarge)
                            Text(shortHash(b.optString("hash")), style = MaterialTheme.typography.labelSmall)
                        }
                        Button(stringResource(R.string.unblock), primary = false) {
                            commit(ctx.getString(R.string.unblocked), "unblock", b.optString("hash"))
                        }
                    }
                }
                Text(stringResource(R.string.block_by_address), style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(top = 10.dp, bottom = 4.dp))
                val clean = blockAddr.trim().lowercase().removePrefix("<").removeSuffix(">")
                val addrOk = clean.length == 32 && clean.all { it in "0123456789abcdef" }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Field(blockAddr, { blockAddr = it }, Modifier.weight(1f), hint = stringResource(R.string.address_hint), mono = true)
                    Spacer(Modifier.width(8.dp))
                    Button(stringResource(R.string.block), enabled = addrOk) {
                        commit(ctx.getString(R.string.blocked_done), "block", clean); blockAddr = ""
                    }
                }
                Text(stringResource(R.string.blocked_help), style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
            }

            // ---------------------------------------------------------- Identity (actions)
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
            Spacer(Modifier.height(72.dp))         // room for the banner over the last section
        }

        // The confirmation: same place, same look, for every change.
        LaunchedEffect(bannerId) { if (banner != null) { delay(2500); banner = null } }
        AnimatedVisibility(visible = banner != null, enter = fadeIn(), exit = fadeOut(),
            modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp)) {
            val (text, ok) = banner ?: ("" to true)
            Text((if (ok) "✓  " else "") + text, modifier = Modifier.clip(Medium).background(Amber.Panel)
                .border(1.dp, if (ok) Amber.Ember else Amber.Error, Medium).padding(horizontal = 16.dp, vertical = 10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = if (ok) Amber.Text else Amber.Error))
        }
    }
}

/**
 * The bottom of every section that has drafts: Save (disabled until something
 * changed and is valid) and Discard (only when something changed).
 */
@Composable
private fun SaveRow(dirty: Boolean, valid: Boolean, problem: Int, onDiscard: () -> Unit, onSave: () -> Unit) {
    if (dirty && !valid) Text(stringResource(problem), style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error),
        modifier = Modifier.padding(top = 8.dp))
    Row(Modifier.padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Button(stringResource(R.string.save), enabled = dirty && valid, onClick = onSave)
        if (dirty) {
            Spacer(Modifier.width(12.dp))
            Text(stringResource(R.string.discard), modifier = Modifier.clip(Small).clickable(onClick = onDiscard).padding(8.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        }
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
