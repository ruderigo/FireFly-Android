package io.github.ruderigo.firefly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.audio.Codec2
import io.github.ruderigo.firefly.audio.VoiceBubble
import io.github.ruderigo.firefly.audio.VoiceEncoder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import io.github.ruderigo.firefly.engine.EngineService
import androidx.compose.ui.platform.LocalContext
import androidx.core.app.NotificationManagerCompat
import io.github.ruderigo.firefly.engine.objects
import org.json.JSONObject

@Composable
fun ChatsScreen(go: (Route) -> Unit) {
    val convs = engineData(emptyList<JSONObject>()) { Engine.arr("conversations").objects() }
    val peers = engineData(emptyList<JSONObject>()) { Engine.arr("peers").objects() }
    var adding by remember { mutableStateOf(false) }
    var addr by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val ctx = LocalContext.current
    val talking = convs.map { it.optString("peer") }.toSet()
    var deleting by remember { mutableStateOf<JSONObject?>(null) }
    var forgetting by remember { mutableStateOf<JSONObject?>(null) }
    var choosing by remember { mutableStateOf<Pair<JSONObject, Boolean>?>(null) }     // person, has a conversation
    var blocking by remember { mutableStateOf<Pair<String, String>?>(null) }          // address, name
    choosing?.let { (p, conv) ->
        val hash = p.optString(if (conv) "peer" else "hash")
        val who = p.optString("name").ifBlank { shortHash(hash) }
        PersonActions(who, onDismiss = { choosing = null },
            onDelete = { if (conv) deleting = p else forgetting = p; choosing = null },
            onBlock = { blocking = hash to who; choosing = null })
    }
    blocking?.let { (hash, who) ->
        ConfirmBlock(who, onDismiss = { blocking = null }) {
            Engine.fire("block", hash)
            NotificationManagerCompat.from(ctx).cancel(EngineService.notificationTag(hash))
            blocking = null
        }
    }
    forgetting?.let { p ->
        val who = p.optString("name").ifBlank { shortHash(p.optString("hash")) }
        ConfirmDialog(stringResource(R.string.forget_peer_title, who), stringResource(R.string.forget_peer_text),
            stringResource(R.string.stump_remove), onDismiss = { forgetting = null }, onConfirm = {
                Engine.fire("delete_conversation", p.optString("hash")); forgetting = null
            })
    }
    deleting?.let { c ->
        val who = c.optString("name").ifBlank { shortHash(c.optString("peer")) }
        ConfirmDialog(stringResource(R.string.delete_conv_title), stringResource(R.string.delete_conv_text, who),
            stringResource(R.string.delete), onDismiss = { deleting = null }, onConfirm = {
                Engine.fire("delete_conversation", c.optString("peer"))
                NotificationManagerCompat.from(ctx).cancel(EngineService.notificationTag(c.optString("peer")))
                deleting = null
            })
    }

    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.conversations), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                Button(stringResource(R.string.add_address), primary = false) { adding = !adding }
            }
            if (adding) Column(Modifier.padding(horizontal = 12.dp)) {
                Field(addr, { addr = it; error = null }, Modifier.fillMaxWidth(), hint = stringResource(R.string.address_hint), mono = true)
                error?.let { Text(it, style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error)) }
                Row(Modifier.padding(vertical = 8.dp)) {
                    Button(stringResource(R.string.open_chat)) {
                        val clean = addr.trim().lowercase().removePrefix("<").removeSuffix(">")
                        if (clean.length == 32 && clean.all { it in "0123456789abcdef" }) {
                            Engine.fire("add_contact", clean); adding = false; addr = ""; go(Route.Chat(clean))
                        } else error = "32 hex"
                    }
                }
            }
        }
        if (convs.isEmpty()) item { Empty(stringResource(R.string.no_conversations)) }
        items(convs, key = { it.optString("peer") }) { c ->
            val unread = c.optInt("unread")
            Row(Modifier.fillMaxWidth().rowPress(onClick = { go(Route.Chat(c.optString("peer"))) }, onLongClick = { choosing = c to true }),
                verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(c.optString("name").ifBlank { shortHash(c.optString("peer")) }, style = MaterialTheme.typography.titleMedium,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text((if (c.optInt("outgoing") == 1) stateGlyph(c.optString("state")) + " " else "") +
                        (if (!c.isNull("audio_mode")) "▶ " + stringResource(R.string.voice_note) else c.optString("content")),
                        style = MaterialTheme.typography.bodyMedium.copy(color = Amber.Muted), maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(clock(c.optDouble("ts")), style = MaterialTheme.typography.labelSmall)
                    if (unread > 0) Text("$unread", modifier = Modifier.padding(top = 4.dp).clip(Small).background(Amber.Ember)
                        .padding(horizontal = 6.dp), style = MaterialTheme.typography.labelMedium.copy(color = Amber.Background))
                }
            }
        }
        val nearby = peers.filter { it.optString("hash") !in talking }
        if (nearby.isNotEmpty()) item {
            Text(stringResource(R.string.heard_nearby), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(12.dp))
        }
        items(nearby, key = { "p" + it.optString("hash") }) { p ->
            Row(Modifier.fillMaxWidth().rowPress(onClick = { go(Route.Chat(p.optString("hash"))) }, onLongClick = { choosing = p to false })) {
                Text(p.optString("name").ifBlank { shortHash(p.optString("hash")) }, style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.weight(1f), maxLines = 1)
                Text(hopsLabel(p), style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
fun hopsLabel(p: JSONObject): String = if (p.isNull("hops")) "" else
    stringResource(R.string.hops, p.optInt("hops"))

@Composable
fun ConversationScreen(peer: String, back: () -> Unit) {
    val info = engineData(JSONObject(), peer) { Engine.obj("peer", peer) }
    val msgs = engineData(emptyList<JSONObject>(), peer) { Engine.arr("messages", peer).objects() }
    val ctx = LocalContext.current
    LaunchedEffect(msgs.size) {
        Engine.fire("mark_read", peer)
        NotificationManagerCompat.from(ctx).cancel(EngineService.notificationTag(peer))
    }
    val list = rememberLazyListState()
    LaunchedEffect(msgs.size) { if (msgs.isNotEmpty()) list.animateScrollToItem(msgs.lastIndex) }
    Column(Modifier.fillMaxSize()) {
        var confirmDelete by remember { mutableStateOf(false) }
        var confirmBlock by remember { mutableStateOf(false) }
        if (confirmBlock) ConfirmBlock(info.optString("name").ifBlank { shortHash(peer) }, onDismiss = { confirmBlock = false }) {
            Engine.fire("block", peer)
            NotificationManagerCompat.from(ctx).cancel(EngineService.notificationTag(peer))
            confirmBlock = false
            back()
        }
        if (confirmDelete) ConfirmDialog(stringResource(R.string.delete_conv_title),
            stringResource(R.string.delete_conv_text, info.optString("name").ifBlank { shortHash(peer) }),
            stringResource(R.string.delete), onDismiss = { confirmDelete = false }, onConfirm = {
                Engine.fire("delete_conversation", peer)
                NotificationManagerCompat.from(ctx).cancel(EngineService.notificationTag(peer))
                confirmDelete = false
                back()
            })
        TopBar(info.optString("name").ifBlank { shortHash(peer) },
            listOfNotNull(shortHash(peer), if (info.isNull("hops")) null else hopsLabel(info)).joinToString("  "), back) {
            OverflowMenu(listOf(stringResource(R.string.delete_conv_menu) to { confirmDelete = true },
                stringResource(R.string.block) to { confirmBlock = true }))
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(vertical = 8.dp)) {
            items(msgs, key = { it.optLong("id") }) { m -> MessageRow(m) }
        }
        val scope = rememberCoroutineScope()
        Composer(stringResource(R.string.message_hint), counter = { PacketCounter(it) },
            onVoice = { rec ->
                scope.launch(Dispatchers.Default) {
                    // The engine picks the codec for this person (Automatic: Opus off LoRa).
                    val mode = Engine.call("voice_mode_for", peer).toIntOrNull() ?: Codec2.MODE_1200
                    val bits = VoiceEncoder.encode(rec, mode) ?: return@launch
                    Engine.call("send_voice", peer, mode, bits)
                }
            }) { Engine.fire("send", peer, it) }
    }
}

@Composable
private fun MessageRow(m: JSONObject) {
    val mine = m.optInt("outgoing") == 1
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
        horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
        if (!m.isNull("audio_mode")) {
            VoiceBubble(m, mine)
            if (m.optString("content").isNotBlank()) Text(m.optString("content"), style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 4.dp))
        } else Box(Modifier.widthIn(max = 320.dp).clip(Large).background(if (mine) Amber.Panel else Amber.Sidebar)
            .border(1.dp, if (mine) Amber.Ember.copy(alpha = 0.45f) else Amber.Border, Large).padding(10.dp)) {
            SelectionContainer { Text(m.optString("content"), style = MaterialTheme.typography.bodyLarge) }
        }
        val meta = buildList {
            add(clock(m.optDouble("ts")))
            if (mine) add(stateGlyph(m.optString("state")))
            if (!mine && !m.isNull("rssi")) add("%.0f dBm".format(m.optDouble("rssi")))
            if (!mine && !m.isNull("snr")) add("%.1f dB".format(m.optDouble("snr")))
            if (!mine && m.optString("method") == "propagated") add(stringResource(R.string.via_node))
            if (m.optInt("verified", 1) == 0) add(stringResource(R.string.unverified))
            m.optString("attachments").takeIf { it.isNotBlank() && it != "null" }?.let { add("[$it]") }
        }.joinToString("  ")
        Text(meta, style = MaterialTheme.typography.labelSmall.copy(
            color = if (m.optString("state") == "failed") Amber.Error else Amber.Dim), modifier = Modifier.padding(top = 2.dp))
        if (mine && m.optString("state") == "failed") m.optString("reason").takeIf { it.isNotBlank() && it != "null" }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error))
        }
    }
}


/** Long-press on a person: delete (they can come back) or block (they can't, until unblocked). */
@Composable
private fun PersonActions(name: String, onDismiss: () -> Unit, onDelete: () -> Unit, onBlock: () -> Unit) {
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = Amber.Panel, shape = Large,
        title = { Text(name, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column {
                Column(Modifier.fillMaxWidth().clip(Small).clickable(onClick = onDelete).padding(vertical = 8.dp)) {
                    Text(stringResource(R.string.delete), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Error))
                    Text(stringResource(R.string.delete_person_text), style = MaterialTheme.typography.bodySmall)
                }
                Column(Modifier.fillMaxWidth().clip(Small).clickable(onClick = onBlock).padding(vertical = 8.dp)) {
                    Text(stringResource(R.string.block), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Error))
                    Text(stringResource(R.string.block_person_text), style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {},
        dismissButton = {
            Text(stringResource(R.string.cancel), modifier = Modifier.clip(Small).clickable { onDismiss() }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        })
}

@Composable
fun ConfirmBlock(name: String, onDismiss: () -> Unit, onConfirm: () -> Unit) =
    ConfirmDialog(stringResource(R.string.block_title, name), stringResource(R.string.block_text),
        stringResource(R.string.block), onConfirm = onConfirm, onDismiss = onDismiss)
