package io.github.ruderigo.firefly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ruderigo.firefly.R
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import io.github.ruderigo.firefly.audio.VoiceBubble
import io.github.ruderigo.firefly.audio.VoiceEncoder
import io.github.ruderigo.firefly.audio.Opus
import io.github.ruderigo.firefly.audio.Codec2
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.EngineService
import androidx.compose.ui.platform.LocalContext
import androidx.core.app.NotificationManagerCompat
import io.github.ruderigo.firefly.engine.objects
import io.github.ruderigo.firefly.engine.strings
import org.json.JSONObject

/** Stump nodes heard on the mesh (from their stump.node beacon) and the ones joined over Wi-Fi. */
@Composable
fun StumpsScreen(go: (Route) -> Unit) {
    val nodes = engineData(emptyList<JSONObject>()) { Engine.arr("stump_nodes").objects() }
    val settings = engineData(JSONObject()) { Engine.obj("settings_get") }
    var url by remember(settings) { mutableStateOf(settings.optString("stump_wifi_default", "http://192.168.4.1")) }
    val hidden = engineData(0) { Engine.call("stump_hidden_count").toIntOrNull() ?: 0 }
    var acting by remember { mutableStateOf<JSONObject?>(null) }
    var removing by remember { mutableStateOf<JSONObject?>(null) }
    acting?.let { n -> StumpActions(n.optString("name").ifBlank { n.optString("address") }, onDismiss = { acting = null },
        onHide = { Engine.fire("stump_hide", n.optString("key")); acting = null },
        onRemove = { removing = n; acting = null }) }
    removing?.let { n -> ConfirmRemoveStump(n.optString("name").ifBlank { n.optString("address") }, onDismiss = { removing = null }) {
        Engine.fire("stump_remove", n.optString("key")); removing = null } }
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Text(stringResource(R.string.stumps_intro), style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(12.dp))
        }
        if (nodes.isEmpty()) item { Empty(stringResource(R.string.no_stumps)) }
        items(nodes, key = { it.optString("key") }) { n ->
            val mesh = n.optString("transport") == "mesh"
            Row(Modifier.fillMaxWidth().rowPress(onClick = { go(Route.Node(n.optString("key"))) }, onLongClick = { acting = n }),
                verticalAlignment = Alignment.CenterVertically) {
                Text("⌂", style = MaterialTheme.typography.titleLarge.copy(color = if (n.optInt("online") == 1) Amber.Ember else Amber.Dim),
                    modifier = Modifier.padding(end = 12.dp))
                Column(Modifier.weight(1f)) {
                    Text(n.optString("name").ifBlank { n.optString("address") }, style = MaterialTheme.typography.titleMedium,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(buildList {
                        add(if (mesh) stringResource(R.string.via_mesh) else stringResource(R.string.via_wifi))
                        if (mesh && !n.isNull("hops")) add(stringResource(R.string.hops, n.optInt("hops")))
                        n.optString("room").takeIf { it.isNotBlank() && it != "null" }?.let { add("#$it") }
                        if (n.optString("auth") == "ok") add(stringResource(R.string.verified))
                    }.joinToString("  "), style = MaterialTheme.typography.labelMedium)
                }
                val dms = n.optInt("unread_dms")
                if (dms > 0) Text("$dms", modifier = Modifier.clip(Small).background(Amber.DmNick).padding(horizontal = 6.dp),
                    style = MaterialTheme.typography.labelMedium.copy(color = Amber.Background))
            }
        }
        if (hidden > 0) item {
            Text(stringResource(R.string.stumps_hidden, hidden), modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)
                .clip(Small).clickable { Engine.fire("stump_restore_hidden") }.padding(6.dp),
                style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
        }
        item {
            Section(stringResource(R.string.join_wifi)) {
                Text(stringResource(R.string.join_wifi_help), style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(8.dp))
                Field(url, { url = it }, Modifier.fillMaxWidth(), mono = true)
                Spacer(Modifier.height(8.dp))
                Button(stringResource(R.string.connect)) {
                    val u = url
                    Engine.fire("stump_connect_wifi", u)
                }
            }
        }
    }
}

@Composable
fun NodeScreen(key: String, back: () -> Unit, go: (Route) -> Unit) {
    var tab by remember(key) { mutableStateOf("room") }
    val view = engineData(JSONObject(), key) { Engine.obj("stump_view", key) }
    val mesh = key.startsWith("mesh:")
    // Billboard and files: Wi-Fi only, read once when the node opens (and on Refresh),
    // not on every chat change. A feature the node doesn't offer has no tab.
    var board by remember(key) { mutableStateOf<JSONObject?>(null) }
    var shelf by remember(key) { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(key) {
        if (!mesh) {
            board = Engine.obj("stump_board", key)
            shelf = Engine.obj("stump_files", key)
        }
    }
    val auth = view.optString("auth", "none")
    val threads = view.optJSONArray("threads")?.objects() ?: emptyList()
    val unread = threads.sumOf { it.optInt("unread") }
    Column(Modifier.fillMaxSize()) {
        TopBar(view.optString("name").ifBlank { key.substringAfter(":") },
            listOfNotNull(
                if (mesh) stringResource(R.string.via_mesh) else stringResource(R.string.via_wifi),
                view.optString("nick").takeIf { it.isNotBlank() && it != "null" }?.let { stringResource(R.string.you_are, it) },
                if (view.optInt("online") == 0) stringResource(R.string.unreachable) else null,
            ).joinToString("  "), back) {
            var menuRemove by remember { mutableStateOf(false) }
            if (menuRemove) ConfirmRemoveStump(view.optString("name").ifBlank { key.substringAfter(":") },
                onDismiss = { menuRemove = false }) { Engine.fire("stump_remove", key); menuRemove = false; back() }
            when (auth) {
                "ok" -> Text(stringResource(R.string.verified), style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
                "waiting" -> Text(stringResource(R.string.verifying), style = MaterialTheme.typography.labelMedium)
                else -> Button(stringResource(R.string.verify_identity), primary = false) { Engine.fire("stump_auth", key) }
            }
            OverflowMenu(listOf(
                stringResource(R.string.stump_hide) to { Engine.fire("stump_hide", key); back() },
                stringResource(R.string.stump_remove) to { menuRemove = true }))
        }
        view.optJSONObject("propagation")?.takeIf { it.optBoolean("enabled", false) }?.let { pn ->
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.node_keeps_messages), style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                if (pn.optBoolean("selected")) Text(stringResource(R.string.prop_in_use), style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
                else Text(stringResource(R.string.node_pn_use), modifier = Modifier.clip(Small)
                    .clickable { Engine.fire("use_propagation_node", pn.optString("hash")) }.padding(6.dp),
                    style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
            }
        }
        if (auth == "failed") Text(stringResource(R.string.verify_failed, view.optString("auth_detail")),
            style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error), modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp))
        val tabs = buildList {
            add("room" to "#" + view.optString("room", "…"))
            add("dm" to stringResource(R.string.private_tab, unread))
            if (!mesh && board.webState() != "off") add("board" to stringResource(R.string.tab_board))
            if (!mesh && shelf.webState() != "off") add("files" to stringResource(R.string.tab_files))
        }
        val shown = if (tabs.any { it.first == tab }) tab else "room"     // a tab whose feature turned out off
        Row(Modifier.fillMaxWidth().background(Amber.Sidebar)) {
            tabs.forEach { (id, label) ->
                val accent = if (id == "dm") Amber.DmNick else Amber.Ember
                Column(Modifier.weight(1f).clickable { tab = id }, horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(label, modifier = Modifier.padding(vertical = 10.dp), maxLines = 1, overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.labelLarge.copy(color = if (shown == id) accent else Amber.Muted))
                    Box(Modifier.fillMaxWidth().height(2.dp).background(if (shown == id) accent else Amber.Sidebar))
                }
            }
        }
        when (shown) {
            "dm" -> PrivatePane(key, view, threads, mesh, go)
            "board" -> BoardPane(key, board) { board = it }
            "files" -> FilesPane(key, shelf) { shelf = it }
            else -> RoomPane(key, view, mesh)
        }
    }
}

@Composable
private fun ColumnScope.RoomPane(key: String, view: JSONObject, mesh: Boolean) {
    val rooms = view.optJSONArray("rooms")?.objects() ?: emptyList()
    val here = view.optString("room")
    Row(Modifier.fillMaxWidth().background(Amber.Sidebar).horizontalScroll(rememberScrollState()).padding(8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        val names = (rooms.map { it.optString("name") } + here).filter { it.isNotBlank() }.distinct()
        names.forEach { r ->
            val info = rooms.firstOrNull { it.optString("name") == r }
            val tier = info?.optString("tier")?.takeIf { it == "minted" || it == "hybrid" }
            Text("#$r" + (tier?.let { " ·$it" } ?: "") + (info?.optInt("count", -1)?.takeIf { it >= 0 }?.let { " $it" } ?: ""),
                modifier = Modifier.padding(end = 6.dp).clip(Small)
                    .border(1.dp, if (r == here) Amber.Ember else Amber.Border, Small)
                    .clickable(enabled = r != here) { Engine.fire("stump_join", key, r) }
                    .padding(horizontal = 8.dp, vertical = 5.dp),
                style = MaterialTheme.typography.labelMedium.copy(color = if (r == here) Amber.Ember else Amber.Muted))
        }
        Text(stringResource(R.string.list_rooms), modifier = Modifier.clip(Small).clickable { Engine.fire("stump_command", key, "/rooms") }
            .padding(horizontal = 8.dp, vertical = 5.dp), style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
    }
    view.optString("topic").takeIf { it.isNotBlank() && it != "null" }?.let {
        Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp))
    }
    val lines = view.optJSONArray("lines")?.objects() ?: emptyList()
    val list = rememberLazyListState()
    LaunchedEffect(lines.size) { if (lines.isNotEmpty()) list.scrollToItem(lines.lastIndex) }
    LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(8.dp)) {
        if (lines.isEmpty()) item { Empty(stringResource(if (mesh) R.string.room_empty_mesh else R.string.room_empty)) }
        items(lines, key = { it.optLong("id") }) { RoomLine(it) }
    }
    Composer(stringResource(R.string.room_hint, here), counter = { if (mesh) PacketCounter(it) }) { Engine.fire("stump_post", key, it) }
}

/** One RRC line, coloured by meaning like the node's own web chat. */
@Composable
private fun RoomLine(l: JSONObject) {
    val kind = l.optString("kind")
    val nick = l.optString("nick").takeIf { it.isNotBlank() && it != "null" }
    val body = l.optString("body")
    val mono = MaterialTheme.typography.labelLarge.copy(fontWeight = null)
    val text = buildAnnotatedString {
        when (kind) {
            "msg" -> {
                withStyle(SpanStyle(color = if (l.optInt("mine") == 1) Amber.EmberBright else Amber.Ember)) { append("<$nick> ") }
                append(body)
            }
            "action" -> withStyle(SpanStyle(color = Amber.Action)) { append("* $nick $body") }
            "command" -> withStyle(SpanStyle(color = Amber.Dim)) { append(body) }
            else -> withStyle(SpanStyle(color = if (body.startsWith("AUTH-FAIL") || body.startsWith("⊘")) Amber.Error else Amber.Dim)) { append(body) }
        }
        if (l.optInt("mine") == 1 && !l.isNull("state")) {
            withStyle(SpanStyle(color = if (l.optString("state") == "failed") Amber.Error else Amber.Dim)) {
                append("  " + stateGlyph(l.optString("state")))
            }
        }
    }
    SelectionContainer { Text(text, style = mono, modifier = Modifier.padding(vertical = 2.dp)) }
}

@Composable
private fun ColumnScope.PrivatePane(key: String, view: JSONObject, threads: List<JSONObject>, mesh: Boolean, go: (Route) -> Unit) {
    val talking = threads.map { it.optString("nick") }.toSet()
    val me = view.optString("nick")
    val stumps = view.optJSONArray("stumps")?.strings()?.toSet() ?: emptySet()
    val others = (view.optJSONArray("users")?.strings() ?: emptyList()).map { it.removePrefix("~") }
        .filter { it != me && it !in talking }
    var to by remember { mutableStateOf("") }
    LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
        item { Text(stringResource(R.string.direct_messages), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(12.dp)) }
        if (threads.isEmpty()) item { Empty(stringResource(R.string.no_dms)) }
        items(threads, key = { "t" + it.optString("nick") }) { t ->
            Row(Modifier.fillMaxWidth().rowClick { go(Route.Dm(key, t.optString("nick"))) }, verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(t.optString("nick"), style = MaterialTheme.typography.titleMedium.copy(color = Amber.DmNick))
                    Text((if (t.optInt("outgoing") == 1) stateGlyph(t.optString("state")) + " " else "") + t.optString("body"),
                        style = MaterialTheme.typography.bodyMedium.copy(color = Amber.DmBody), maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                if (t.optInt("unread") > 0) Text("${t.optInt("unread")}", modifier = Modifier.clip(Small).background(Amber.DmNick)
                    .padding(horizontal = 6.dp), style = MaterialTheme.typography.labelMedium.copy(color = Amber.Background))
            }
        }
        item {
            Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.message_someone), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                if (mesh) Text(stringResource(R.string.who_is_here), modifier = Modifier.clip(Small)
                    .clickable { Engine.fire("stump_command", key, "/names") }.padding(6.dp),
                    style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
            }
        }
        items(others, key = { "u$it" }) { nick ->
            Row(Modifier.fillMaxWidth().rowClick { go(Route.Dm(key, nick)) }) {
                Text(nick, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                if (nick in stumps) Text("stump", style = MaterialTheme.typography.labelSmall)
            }
        }
        item {
            Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Field(to, { to = it }, Modifier.weight(1f), hint = stringResource(R.string.nick_hint), mono = true)
                Spacer(Modifier.width(8.dp))
                Button(stringResource(R.string.open_chat), enabled = to.isNotBlank()) { go(Route.Dm(key, to.trim().removePrefix("~"))) }
            }
        }
    }
}

@Composable
fun DmScreen(key: String, nick: String, back: () -> Unit) {
    val msgs = engineData(emptyList<JSONObject>(), key, nick) { Engine.arr("stump_dm_thread", key, nick).objects() }
    val node = engineData(JSONObject(), key) { Engine.obj("stump_view", key) }
    val list = rememberLazyListState()
    val ctx = LocalContext.current
    LaunchedEffect(msgs.size) {
        if (msgs.isNotEmpty()) list.animateScrollToItem(msgs.lastIndex)
        NotificationManagerCompat.from(ctx).cancel(EngineService.notificationTag(key, nick))
    }
    Column(Modifier.fillMaxSize()) {
        TopBar(nick, stringResource(R.string.dm_via, node.optString("name")), back)
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(vertical = 8.dp)) {
            if (msgs.isEmpty()) item { Empty(stringResource(R.string.dm_empty)) }
            items(msgs, key = { it.optLong("id") }) { m ->
                val mine = m.optInt("outgoing") == 1
                Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                    horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
                    if (!m.isNull("audio_mode")) VoiceBubble(m, mine, fetch = "stump_dm_audio", dm = true)
                    else Box(Modifier.widthIn(max = 320.dp).clip(Large).background(Amber.Panel)
                        .border(1.dp, Amber.DmBody.copy(alpha = if (mine) 0.5f else 0.25f), Large).padding(10.dp)) {
                        SelectionContainer { Text(m.optString("body"), style = MaterialTheme.typography.bodyLarge.copy(color = Amber.DmBody)) }
                    }
                    val state = m.optString("state")
                    val meta = clock(m.optDouble("ts")) + if (mine) "  " + when (state) {
                        "pending" -> stringResource(R.string.dm_pending)
                        "delivered" -> stringResource(R.string.dm_delivered)
                        "failed" -> stringResource(R.string.dm_failed, m.optString("reason"))
                        else -> ""
                    } else ""
                    Text(meta, style = MaterialTheme.typography.labelSmall.copy(color = if (state == "failed") Amber.Error else Amber.Dim))
                }
            }
        }
        val scope = rememberCoroutineScope()
        Composer(stringResource(R.string.dm_hint, nick), counter = { if (key.startsWith("mesh:")) PacketCounter("/msg $nick $it") },
            onVoice = { rec ->
                scope.launch(Dispatchers.Default) {
                    // Same setting as any chat (Opus by default); Automatic judges the link to this node.
                    val mode = Engine.call("voice_mode_for_stump", key).toIntOrNull() ?: Opus.MODE_OGG
                    val bits = VoiceEncoder.encode(rec, mode) ?: return@launch
                    Engine.call("stump_send_voice", key, nick, mode, bits)
                }
            }) {
            Engine.fire("stump_send_dm", key, nick, it)
        }
    }
}


/** Long-press on a Stump: hide it (history kept) or remove it (history deleted). */
@Composable
private fun StumpActions(name: String, onDismiss: () -> Unit, onHide: () -> Unit, onRemove: () -> Unit) {
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = Amber.Panel, shape = Large,
        title = { Text(name, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column {
                Column(Modifier.fillMaxWidth().clip(Small).clickable(onClick = onHide).padding(vertical = 8.dp)) {
                    Text(stringResource(R.string.stump_hide), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
                    Text(stringResource(R.string.stump_hide_text), style = MaterialTheme.typography.bodySmall)
                }
                Column(Modifier.fillMaxWidth().clip(Small).clickable(onClick = onRemove).padding(vertical = 8.dp)) {
                    Text(stringResource(R.string.stump_remove), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Error))
                    Text(stringResource(R.string.stump_remove_text), style = MaterialTheme.typography.bodySmall)
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
private fun ConfirmRemoveStump(name: String, onDismiss: () -> Unit, onConfirm: () -> Unit) =
    ConfirmDialog(stringResource(R.string.stump_remove_title, name), stringResource(R.string.stump_remove_confirm),
        stringResource(R.string.stump_remove), onConfirm = onConfirm, onDismiss = onDismiss)
