package io.github.ruderigo.firefly.ui

import android.content.Intent
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
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
import io.github.ruderigo.firefly.engine.nameText
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.objects
import io.github.ruderigo.firefly.net.WifiDiscovery
import io.github.ruderigo.firefly.net.WifiHttp
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * The + button › search (Nov 2026): the Stumps on this Wi-Fi not in the list yet (STUMP?,
 * about a second, then the sweep for older ones), each with a checkbox, unticked; Select all /
 * Deselect all; Add puts the ticked ones in the list, none opened. Over LoRa they're added
 * by themselves: how many. Hotspots and adding by address are the + button's other two.
 */
@Composable
fun FindStumpsPopup(onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val gateway by WifiHttp.gateway.collectAsState()
    val local by WifiHttp.local.collectAsState()

    var found by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var ticked by remember { mutableStateOf(setOf<String>()) }
    var quickDone by remember { mutableStateOf(false) }
    var sweepDone by remember { mutableStateOf(false) }
    var adding by remember { mutableStateOf(false) }

    fun keyOf(f: JSONObject) = f.optString("lxmf").takeIf { it.isNotBlank() && it != "null" } ?: f.optString("base")
    fun merge(more: List<JSONObject>) {
        val have = found.map { keyOf(it) }.toSet()
        found = found + more.filter { keyOf(it) !in have }
    }

    // This Wi-Fi: the quick answer first, then the sweep. Again whenever the Wi-Fi changes
    // (a hotspot joined from here).
    LaunchedEffect(gateway, local) {
        quickDone = false; sweepDone = false
        if (gateway == null) { quickDone = true; sweepDone = true; return@LaunchedEffect }
        merge(runCatching { Engine.arr("find_new_stumps", "quick").objects() }.getOrDefault(emptyList()))
        quickDone = true
        val l = local
        merge(runCatching {
            Engine.arr("find_new_stumps", "sweep", l?.substringBefore('/'), l?.substringAfter('/')?.toIntOrNull() ?: 24, "[]").objects()
        }.getOrDefault(emptyList()))
        sweepDone = true
    }

    val lora = engineData(0) {
        Engine.arr("stump_nodes").objects().count { r ->
            val t = r.optJSONArray("transports"); t != null && (0 until t.length()).any { t.getString(it) == "mesh" }
        }
    }

    fun add() {
        val chosen = found.filter { keyOf(it) in ticked }
        if (chosen.isEmpty() || adding) return
        adding = true
        scope.launch {
            val r = Engine.obj("stumps_add_found", JSONArray(chosen).toString())
            adding = false
            Toast.makeText(ctx, ctx.getString(R.string.find_popup_added, r.optInt("added")), Toast.LENGTH_LONG).show()
            onDismiss()
        }
    }

    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = Amber.Panel, shape = Large,
        title = { Text(stringResource(R.string.find_stumps), style = MaterialTheme.typography.titleMedium) },
        text = {
            LazyColumn(Modifier.heightIn(max = 460.dp)) {
                // ---- this Wi-Fi
                item {
                    when {
                        gateway == null -> Hint(stringResource(R.string.find_popup_not_wifi))
                        !sweepDone -> Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 4.dp)) {
                            androidx.compose.material3.CircularProgressIndicator(Modifier.size(18.dp), color = Amber.Ember, strokeWidth = 2.dp)
                            Spacer(Modifier.width(10.dp))
                            Text(stringResource(if (!quickDone) R.string.find_popup_searching else R.string.find_popup_sweeping),
                                style = MaterialTheme.typography.bodySmall)
                        }
                        found.isEmpty() -> Hint(stringResource(R.string.find_popup_none))
                    }
                }
                if (found.isNotEmpty()) item {
                    Row(Modifier.padding(top = 4.dp)) {
                        TextLink(stringResource(R.string.find_select_all)) { ticked = found.map { keyOf(it) }.toSet() }
                        Spacer(Modifier.width(8.dp))
                        TextLink(stringResource(R.string.find_deselect_all)) { ticked = emptySet() }
                    }
                }
                items(found, key = { "f" + keyOf(it) }) { f ->
                    val k = keyOf(f)
                    Row(Modifier.fillMaxWidth().clip(Small).clickable { ticked = if (k in ticked) ticked - k else ticked + k }
                        .padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = k in ticked, onCheckedChange = { on -> ticked = if (on) ticked + k else ticked - k },
                            colors = CheckboxDefaults.colors(checkedColor = Amber.Ember, uncheckedColor = Amber.Muted))
                        Column(Modifier.weight(1f)) {
                            Text(f.nameText().takeIf { it.isNotBlank() && it != "null" } ?: f.optString("base"),
                                style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(listOfNotNull(f.optString("base").substringAfter("://").trimEnd('/'),
                                f.optString("release").takeIf { it.isNotBlank() && it != "null" }).joinToString(" · "),
                                style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }

                // ---- LoRa
                if (lora > 0) item {
                    Spacer(Modifier.height(12.dp))
                    Hint(stringResource(R.string.find_popup_lora, lora))
                }
            }
        },
        confirmButton = {
            val n = found.count { keyOf(it) in ticked }
            Text(stringResource(R.string.find_popup_add, n),
                modifier = Modifier.clip(Small).clickable(enabled = n > 0 && !adding) { add() }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = if (n > 0 && !adding) Amber.Ember else Amber.Dim))
        },
        dismissButton = {
            Text(stringResource(R.string.cancel), modifier = Modifier.clip(Small).clickable { onDismiss() }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        })
}

@Composable
internal fun Hint(text: String, modifier: Modifier = Modifier) =
    Text(text, style = MaterialTheme.typography.bodySmall, modifier = modifier.padding(vertical = 4.dp))

@Composable
internal fun TextLink(text: String, onClick: () -> Unit) =
    Text(text, modifier = Modifier.clip(Small).clickable(onClick = onClick).padding(horizontal = 6.dp, vertical = 6.dp),
        style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
