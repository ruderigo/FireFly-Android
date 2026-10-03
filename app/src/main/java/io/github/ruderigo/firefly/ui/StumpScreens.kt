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
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Text(stringResource(R.string.stumps_intro), style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(12.dp))
        }
        if (nodes.isEmpty()) item { Empty(stringResource(R.string.no_stumps)) }
        items(nodes, key = { it.optString("key") }) { n ->
            val mesh = n.optString("transport") == "mesh"
            Row(Modifier.fillMaxWidth().rowClick { go(Route.Node(n.optString("key"))) }, verticalAlignment = Alignment.CenterVertically) {
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
    var tab by remember { mutableIntStateOf(0) }
    val view = engineData(JSONObject(), key) { Engine.obj("stump_view", key) }
    val mesh = key.startsWith("mesh:")
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
            when (auth) {
                "ok" -> Text(stringResource(R.string.verified), style = MaterialTheme.typography.labelMedium.copy(color = Amber.Ember))
                "waiting" -> Text(stringResource(R.string.verifying), style = MaterialTheme.typography.labelMedium)
                else -> Button(stringResource(R.string.verify_identity), primary = false) { Engine.fire("stump_auth", key) }
            }
        }
        if (auth == "failed") Text(stringResource(R.string.verify_failed, view.optString("auth_detail")),
            style = MaterialTheme.typography.bodySmall.copy(color = Amber.Error), modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp))
        Row(Modifier.fillMaxWidth().background(Amber.Sidebar)) {
            listOf("#" + view.optString("room", "…"), stringResource(R.string.private_tab, unread)).forEachIndexed { i, label ->
                Column(Modifier.weight(1f).clickable { tab = i }, horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(label, modifier = Modifier.padding(vertical = 10.dp), style = MaterialTheme.typography.labelLarge.copy(
                        color = if (tab == i) (if (i == 1) Amber.DmNick else Amber.Ember) else Amber.Muted))
                    Box(Modifier.fillMaxWidth().height(2.dp).background(if (tab == i) (if (i == 1) Amber.DmNick else Amber.Ember) else Amber.Sidebar))
                }
            }
        }
        if (tab == 0) RoomPane(key, view, mesh) else PrivatePane(key, view, threads, mesh, go)
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
            else -> withStyle(SpanStyle(color = if (body.startsWith("AUTH-FAIL")) Amber.Error else Amber.Dim)) { append(body) }
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
                    Box(Modifier.widthIn(max = 320.dp).clip(Large).background(Amber.Panel)
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
        Composer(stringResource(R.string.dm_hint, nick), counter = { if (key.startsWith("mesh:")) PacketCounter("/msg $nick $it") }) {
            Engine.fire("stump_send_dm", key, nick, it)
        }
    }
}
