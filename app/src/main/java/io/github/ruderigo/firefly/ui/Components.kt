package io.github.ruderigo.firefly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.Engine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.text.DateFormat
import java.util.Date

/** Re-reads from the engine whenever its store changes (or [keys] change). */
@Composable
fun <T> engineData(initial: T, vararg keys: Any?, load: suspend () -> T): T {
    val rev by Engine.revision.collectAsState()
    val v by produceState(initial, rev, *keys) { value = load() }
    return v
}

@Composable
fun TopBar(title: String, subtitle: String? = null, onBack: (() -> Unit)? = null, trailing: @Composable RowScope.() -> Unit = {}) {
    Row(
        Modifier.fillMaxWidth().background(Amber.Background).statusBarsPadding().padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (onBack != null) {
            Text("‹", style = MaterialTheme.typography.headlineSmall.copy(color = Amber.Ember, fontSize = 30.sp),
                modifier = Modifier.clip(Small).clickable(onClick = onBack).padding(horizontal = 10.dp))
        }
        Column(Modifier.weight(1f).padding(start = 4.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (!subtitle.isNullOrBlank()) Text(subtitle, style = MaterialTheme.typography.labelMedium, maxLines = 1,
                overflow = TextOverflow.Ellipsis)
        }
        trailing()
    }
    Box(Modifier.fillMaxWidth().height(1.dp).background(Amber.Border))
}

/** The radio at a glance: the reason this app exists is LoRa. */
@Composable
fun RadioIndicator(status: JSONObject, onClick: () -> Unit) {
    val r = status.optJSONObject("radio") ?: JSONObject()
    val state = r.optString("state", "off")
    val (color, label) = when (state) {
        "online" -> Amber.Ember to when (r.optString("kind")) {
            "ble" -> stringResource(R.string.radio_ble); "usb" -> stringResource(R.string.radio_usb)
            else -> stringResource(R.string.radio_wifi) }
        "connecting" -> Amber.Muted to stringResource(R.string.radio_connecting)
        "refused" -> Amber.Error to stringResource(R.string.radio_refused)
        "off" -> Amber.Dim to stringResource(R.string.radio_off)
        else -> Amber.Dim to stringResource(R.string.radio_none)
    }
    Row(Modifier.clip(Medium).border(1.dp, Amber.Border, Medium).clickable(onClick = onClick)
        .padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(Small).background(color))
        Spacer(Modifier.width(8.dp))
        Text(label, style = MaterialTheme.typography.labelMedium.copy(color = if (state == "online") Amber.Text else Amber.Muted))
    }
}

@Composable
fun Button(text: String, modifier: Modifier = Modifier, primary: Boolean = true, enabled: Boolean = true, onClick: () -> Unit) {
    val bg = if (!enabled) Amber.Row else if (primary) Amber.Ember else Color.Transparent
    val fg = if (!enabled) Amber.Dim else if (primary) Amber.Background else Amber.Ember
    Box(modifier.clip(Medium).background(bg).border(1.dp, if (primary) bg else Amber.Border, Medium)
        .clickable(enabled = enabled, onClick = onClick).padding(horizontal = 14.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.labelLarge.copy(color = fg))
    }
}

@Composable
fun Field(value: String, onChange: (String) -> Unit, modifier: Modifier = Modifier, hint: String = "",
          singleLine: Boolean = true, mono: Boolean = false, number: Boolean = false, onDone: (() -> Unit)? = null) {
    val style: TextStyle = (if (mono) MaterialTheme.typography.labelLarge.copy(fontWeight = null) else MaterialTheme.typography.bodyMedium)
        .copy(color = Amber.Text)
    BasicTextField(
        value = value, onValueChange = onChange, singleLine = singleLine, textStyle = style,
        cursorBrush = SolidColor(Amber.EmberBright),
        keyboardOptions = KeyboardOptions(keyboardType = if (number) KeyboardType.Number else KeyboardType.Text,
            imeAction = if (onDone != null) ImeAction.Done else ImeAction.Default),
        keyboardActions = KeyboardActions(onDone = { onDone?.invoke() }),
        modifier = modifier.clip(Small).background(Amber.Panel).border(1.dp, Amber.Border, Small).padding(10.dp),
        decorationBox = { inner ->
            Box { if (value.isEmpty()) Text(hint, style = style.copy(color = Amber.Dim)); inner() }
        })
}

/** Message box. [counter] shows the LoRa budget for the text being typed. */
@Composable
fun Composer(hint: String, counter: @Composable (String) -> Unit = {}, onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    Column(Modifier.fillMaxWidth().background(Amber.Sidebar).navigationBarsPadding().imePadding()) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(Amber.Border))
        Row(Modifier.padding(8.dp), verticalAlignment = Alignment.Bottom) {
            Field(text, { text = it }, Modifier.weight(1f).heightIn(min = 44.dp, max = 160.dp), hint = hint, singleLine = false)
            Spacer(Modifier.width(8.dp))
            Button(stringResource(R.string.send), enabled = text.isNotBlank()) {
                val t = text.trim(); if (t.isNotEmpty()) { onSend(t); text = "" }
            }
        }
        if (text.isNotEmpty()) Box(Modifier.padding(start = 12.dp, bottom = 6.dp)) { counter(text) }
    }
}

/** One LXMF packet holds 295 bytes; past that LXMF opens a link (more airtime). */
@Composable
fun PacketCounter(text: String) {
    val size by produceState(0, text) { value = withContext(Dispatchers.IO) { Engine.call("packet_size", text).toIntOrNull() ?: 0 } }
    val one = size <= 295
    Text(if (one) stringResource(R.string.packet_one, size) else stringResource(R.string.packet_link, size),
        style = MaterialTheme.typography.labelSmall.copy(color = if (one) Amber.Dim else Amber.Action))
}

/** Delivery glyphs, the same as FireFly on the handheld. */
fun stateGlyph(state: String): String = when (state) {
    "pending" -> "…"; "stamping" -> "⚙"; "sending" -> "↑"; "sent" -> "✓"
    "delivered" -> "✓✓"; "stored" -> "✓ node"; "failed" -> "✗"; else -> ""
}

@Composable
fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(bottom = 6.dp))
        Column(Modifier.fillMaxWidth().clip(Large).background(Amber.Panel).border(1.dp, Amber.Border, Large).padding(12.dp),
            content = content)
    }
}

@Composable
fun KeyValue(key: String, value: String, valueColor: Color = Amber.Text) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(key, style = MaterialTheme.typography.labelMedium, modifier = Modifier.width(120.dp))
        Text(value, style = MaterialTheme.typography.labelLarge.copy(fontWeight = null, color = valueColor))
    }
}

@Composable
fun Empty(text: String) {
    Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.bodyMedium.copy(color = Amber.Muted))
    }
}

fun shortHash(h: String) = if (h.length > 12) "<${h.take(8)}…${h.takeLast(4)}>" else h

fun clock(ts: Double): String {
    val d = Date((ts * 1000).toLong())
    val today = System.currentTimeMillis() - d.time < 20 * 3600 * 1000
    return (if (today) DateFormat.getTimeInstance(DateFormat.SHORT) else DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)).format(d)
}

fun Modifier.rowClick(onClick: () -> Unit) = this.clickable(onClick = onClick).padding(horizontal = 12.dp, vertical = 10.dp)
