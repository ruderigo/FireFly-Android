@file:OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class, androidx.compose.material3.ExperimentalMaterial3Api::class)

package io.github.ruderigo.firefly.ui

import kotlinx.coroutines.delay
import androidx.compose.animation.fadeOut
import androidx.compose.animation.fadeIn
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.foundation.basicMarquee
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
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
import kotlinx.coroutines.withContext
import io.github.ruderigo.firefly.audio.VoiceBubble
import io.github.ruderigo.firefly.audio.VoiceEncoder
import io.github.ruderigo.firefly.audio.Opus
import io.github.ruderigo.firefly.audio.Codec2
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.net.WifiHttp
import io.github.ruderigo.firefly.engine.EngineService
import androidx.compose.ui.platform.LocalContext
import androidx.core.app.NotificationManagerCompat
import io.github.ruderigo.firefly.engine.objects
import io.github.ruderigo.firefly.engine.strings
import org.json.JSONObject

/** Stump nodes heard on the mesh (from their stump.node beacon) and the ones joined over Wi-Fi. */
@Composable
fun StumpsScreen(go: (Route) -> Unit) {
    val onWifi = WifiHttp.local.collectAsState().value != null
    val nodes = engineData(emptyList<JSONObject>(), onWifi) { Engine.arr("stump_nodes", onWifi).objects() }
    // Pull down: ask every Stump whether it's there (Wi-Fi probed, LoRa asked for a path). One
    // at a time; pulling again meanwhile does nothing.
    var refreshing by remember { mutableStateOf(false) }
    val listScope = rememberCoroutineScope()
    fun refreshAll() {
        if (refreshing) return
        refreshing = true
        listScope.launch {
            val started = System.currentTimeMillis()
            runCatching { withContext(Dispatchers.IO) { Engine.call("stump_refresh_all", onWifi) } }
            delay((800 - (System.currentTimeMillis() - started)).coerceAtLeast(0))   // long enough to see
            refreshing = false
        }
    }
    // Stumps we know that answer on this Wi-Fi (STUMP?): open them here, or follow them to the
    // address the router gave them since. One broadcast, at most every 20 s.
    val gateway by WifiHttp.gateway.collectAsState()
    LaunchedEffect(gateway) { if (gateway != null) Engine.arr("stump_refresh_wifi") }
    val hidden = engineData(0) { Engine.call("stump_hidden_count").toIntOrNull() ?: 0 }
    var acting by remember { mutableStateOf<JSONObject?>(null) }
    var removing by remember { mutableStateOf<JSONObject?>(null) }
    // A row may stand for one Stump on LoRa and Wi-Fi: hide or remove acts on both.
    fun keysOf(n: JSONObject) = listOf(n.optString("key")) + (n.optJSONArray("others")?.objects()?.map { it.optString("key") } ?: emptyList())
    acting?.let { n -> StumpActions(n.optString("name").ifBlank { n.optString("address") }, onDismiss = { acting = null },
        onHide = { keysOf(n).forEach { Engine.fire("stump_hide", it) }; acting = null },
        onRemove = { removing = n; acting = null }) }
    removing?.let { n -> ConfirmRemoveStump(n.optString("name").ifBlank { n.optString("address") }, onDismiss = { removing = null }) {
        keysOf(n).forEach { Engine.fire("stump_remove", it) }; removing = null } }
    androidx.compose.material3.pulltorefresh.PullToRefreshBox(isRefreshing = refreshing, onRefresh = { refreshAll() },
        modifier = Modifier.fillMaxSize()) {
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.stumps_intro), style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(8.dp))
                Button(stringResource(R.string.find_stumps)) { go(Route.FindStumps) }
            }
        }
        if (nodes.isEmpty()) item { Empty(stringResource(R.string.no_stumps)) }
        items(nodes, key = { it.optString("group").ifBlank { it.optString("key") } }) { n ->
            val mesh = n.optString("transport") == "mesh"
            val both = (n.optJSONArray("transports")?.length() ?: 1) > 1
            Row(Modifier.fillMaxWidth().rowPress(onClick = { go(Route.Node(n.optString("key"))) }, onLongClick = { acting = n }),
                verticalAlignment = Alignment.CenterVertically) {
                // A stump: filled when it answers (on the Wi-Fi, or heard over LoRa lately), outlined when not.
                val alive = n.optBoolean("alive")
                androidx.compose.material3.Icon(painterResource(if (alive) R.drawable.ic_stump_online else R.drawable.ic_stump),
                    contentDescription = stringResource(if (alive) R.string.stump_online else R.string.stump_not_answering),
                    tint = if (alive) Amber.Ember else Amber.Dim, modifier = Modifier.padding(end = 12.dp).size(26.dp))
                Column(Modifier.weight(1f)) {
                    Text(n.optString("name").ifBlank { n.optString("address") }, style = MaterialTheme.typography.titleMedium,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(buildList {
                        add(if (both) stringResource(R.string.via_both) else if (mesh) stringResource(R.string.via_mesh)
                            else stringResource(R.string.via_wifi))
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
    }
    }
}


/**
 * Open a Stump by its address (in the Network tab). The gateway is offered only when it
 * really is a Stump (its own hotspot, 192.168.n.1 along a chain): on home Wi-Fi it's the
 * router. Connect asks /node.json before opening anything.
 */
@Composable
fun StumpOverWifi(go: (Route) -> Unit) {
    val settings = engineData(JSONObject()) { Engine.obj("settings_get") }
    val gateway by WifiHttp.gateway.collectAsState()
    // Asked (/node.json) before it's offered: null while asking, then the answer.
    var gwProbe by remember(gateway) { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(gateway) { gateway?.let { gwProbe = Engine.obj("discover_probe", "http://$it") } }
    val gwStump = gwProbe?.takeIf { it.optBoolean("stump") }
    var url by remember(settings, gwStump) {
        mutableStateOf(gwStump?.optString("base") ?: settings.optString("stump_wifi_default", "http://192.168.4.1"))
    }
    var connecting by remember { mutableStateOf(false) }
    var connectError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    Section(stringResource(R.string.join_wifi)) {
        Text(stringResource(R.string.join_wifi_help), style = MaterialTheme.typography.bodySmall)
        gateway?.let { gw ->
            val p = gwProbe
            Text(when {
                p == null -> stringResource(R.string.find_checking, gw)
                p.optBoolean("stump") -> stringResource(R.string.join_wifi_gateway_stump,
                    p.optString("name").takeIf { it.isNotBlank() && it != "null" } ?: "Stump", gw)
                else -> stringResource(R.string.join_wifi_gateway_router, gw)
            }, style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 4.dp))
        }
        Spacer(Modifier.height(8.dp))
        Field(url, { url = it; connectError = null }, Modifier.fillMaxWidth(), mono = true)
        connectError?.let {
            Text(it, style = MaterialTheme.typography.labelSmall.copy(color = Amber.Error), modifier = Modifier.padding(top = 4.dp))
        }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Button(stringResource(if (connecting) R.string.connect_checking else R.string.connect), enabled = !connecting) {
                val u = url
                connecting = true; connectError = null
                scope.launch {
                    val r = Engine.obj("stump_connect_checked", u)
                    connecting = false
                    val k = r.optString("key")
                    if (k.isNotBlank() && k != "null") {
                        go(Route.Node(k))
                    } else {
                        connectError = r.optString("error").takeIf { it.isNotBlank() && it != "null" }
                            ?.let { why -> "${r.optString("base", u)}: $why" }
                    }
                }
            }
            if (connectError != null) {
                Spacer(Modifier.width(8.dp))
                Button(stringResource(R.string.find_stumps), primary = false) { go(Route.FindStumps) }
            }
        }
    }
}

@Composable
fun NodeScreen(openedAs: String, back: () -> Unit, go: (Route) -> Unit) {
    // A Wi-Fi Stump the router moved is found again by its identity: the screen follows it.
    var key by remember(openedAs) { mutableStateOf(openedAs) }
    var tab by remember(openedAs) { mutableStateOf("room") }
    val scope = rememberCoroutineScope()
    val onWifi = WifiHttp.local.collectAsState().value != null       // the phone is on a Wi-Fi
    val view = engineData(JSONObject(), key, onWifi) { Engine.obj("stump_view", key, null, onWifi) }
    LaunchedEffect(view) {
        view.optString("moved_to").takeIf { it.isNotBlank() && it != "null" && it != key }?.let { key = it }
    }
    val mesh = key.startsWith("mesh:")
    // Billboard and files come from wherever this Stump answers on the Wi-Fi now, whichever
    // side the screen was opened on; the engine says (reach: "wifi" with its address, "lora",
    // "no_wifi", "offline"). Settled: a single missed poll doesn't make the icons blink.
    val reachNow = view.optJSONObject("reach")
    val reachState = settled(reachNow?.optString("state") ?: if (mesh) "lora" else "wifi") { if (it == "wifi") 300L else 1500L }
    val webKey = reachNow?.optString("web")?.takeIf { reachState == "wifi" && it.isNotBlank() && it != "null" }
        ?: key.takeIf { !mesh && reachState == "wifi" }
    // Over LoRa only, the billboard is asked of the Stump's LoRa side (/board, Stump 1.0.0-beta.38).
    val loraKey = view.optString("lora_key").takeIf { it.isNotBlank() && it != "null" }
    val loraBoard = engineData(JSONObject(), loraKey, reachState) {
        if (loraKey != null && reachState == "lora") Engine.obj("stump_board_lora_view", loraKey) else JSONObject()
    }
    var board by remember(key, webKey) { mutableStateOf<JSONObject?>(null) }
    var shelf by remember(key, webKey) { mutableStateOf<JSONObject?>(null) }
    var plugins by remember(key) { mutableStateOf<JSONObject?>(null) }     // its !commands
    var tools by remember(key) { mutableStateOf<JSONObject?>(null) }       // a newer FireFly on its card
    var chain by remember(key) { mutableStateOf<JSONObject?>(null) }       // its place in a chain of Stumps
    var radio by remember(key) { mutableStateOf<JSONObject?>(null) }       // its radio settings beside ours
    val version = myVersion(LocalContext.current)
    LaunchedEffect(webKey) {
        if (webKey != null && mesh) {
            board = Engine.obj("stump_board", webKey)
            shelf = Engine.obj("stump_files", webKey)
        }
    }
    LaunchedEffect(key) {
        if (!mesh) {
            board = Engine.obj("stump_board", key)
            shelf = Engine.obj("stump_files", key)
            plugins = Engine.obj("stump_plugins", key)
            tools = Engine.obj("stump_tools", key, version)
            chain = Engine.obj("stump_chain", key)
            radio = Engine.obj("stump_radio", key)
        }
    }
    // It restarted (a new boot id in its beacon, Stump 1.0.0-beta.43): its chain comes back
    // within seconds, so ask again shortly after, then once more.
    val restartedAt = view.optDouble("restarted_at").takeIf { !it.isNaN() }
    LaunchedEffect(key, restartedAt) {
        if (restartedAt == null || mesh) return@LaunchedEffect
        for (wait in listOf(5_000L, 20_000L)) {
            delay(wait)
            chain = Engine.obj("stump_chain", key)
            radio = Engine.obj("stump_radio", key)
        }
    }
    val auth = view.optString("auth", "none")
    // Into a Stump only once it knows who we are: FireFly verifies itself (Wi-Fi and LoRa) and
    // the room waits behind a popup until it's done, or the user chooses to go in anyway.
    var entered by remember(openedAs) { mutableStateOf(false) }
    if (!entered) VerifyGate(key, view.optString("name").ifBlank { key.substringAfter(":") }, mesh,
        onEnter = { entered = true }, onBack = back)
    val threads = view.optJSONArray("threads")?.objects() ?: emptyList()
    val unread = threads.sumOf { it.optInt("unread") }
    Column(Modifier.fillMaxSize()) {
        TopBar(view.optString("name").ifBlank { key.substringAfter(":") },
            listOfNotNull(
                if (mesh) stringResource(R.string.via_mesh) else stringResource(R.string.via_wifi),
                view.optString("nick").takeIf { it.isNotBlank() && it != "null" }?.let { stringResource(R.string.you_are, it) },
                if (view.optInt("online") == 0) stringResource(R.string.unreachable) +
                    (view.optString("offline_reason").takeIf { !mesh && it.isNotBlank() && it != "null" }?.let { ": $it" } ?: "")
                else null,
                chain?.takeIf { it.optBoolean("enabled") }?.let {
                    stringResource(if (it.optString("role") == "root") R.string.chain_root else R.string.chain_member)
                },
                // The node's own radio, from its poll (Stump 1.0.0-beta.5+): its status light.
                view.optJSONObject("hw")?.optString("state")?.let { st ->
                    when (st) {
                        "online" -> stringResource(R.string.node_radio_online)
                        "connecting" -> stringResource(R.string.node_radio_connecting)
                        "error" -> stringResource(R.string.node_radio_error)
                        "none" -> stringResource(R.string.node_radio_none)
                        else -> null
                    }
                },
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
        if (mesh && !view.optBoolean("reachable", true)) {
            val alt = view.optString("wifi_alt").takeIf { it.isNotBlank() && it != "null" }
            // No radio: if this Stump answers on the Wi-Fi (STUMP?, by its identity), chat there.
            var looking by remember(key) { mutableStateOf(false) }
            fun lookOnWifi(thenFind: Boolean) {
                if (looking) return
                looking = true
                scope.launch {
                    val r = Engine.obj("stump_find_wifi", key.substringAfter(":"))
                    looking = false
                    val k = r.optString("key").takeIf { it.isNotBlank() && it != "null" }
                    if (k != null) go(Route.Node(k)) else if (thenFind) go(Route.FindStumps)
                }
            }
            LaunchedEffect(key, alt) { if (alt == null) lookOnWifi(thenFind = false) }
            NoRadioBanner(alt, looking) { if (it != null) go(Route.Node(it)) else lookOnWifi(thenFind = true) }
        }
        if (!mesh) UpdateBanner(key, tools)
        // A rescue start (Stump 1.0.0-beta.26): no radio, chain or plugins for now; chat over Wi-Fi.
        if (!mesh && radio?.optString("mode") == "rescue") {
            Text(stringResource(R.string.stump_rescue), modifier = Modifier.fillMaxWidth().background(Amber.Sidebar)
                .padding(horizontal = 12.dp, vertical = 8.dp), style = MaterialTheme.typography.labelMedium.copy(color = Amber.Error))
        }
        if (!mesh) RadioMatch(radio) {
            scope.launch {
                withContext(Dispatchers.IO) { Engine.call("stump_use_radio", key) }
                radio = Engine.obj("stump_radio", key)
            }
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
        // The navigation: the room on the left half; private messages, billboard and files as
        // icons sharing the right half (only those this Stump offers here). An icon is outlined
        // when there's nothing new and filled when there is (unread DMs; later, new notices or
        // files).
        // The same layout over Wi-Fi and LoRa: a tab this Stump doesn't offer here (the billboard
        // and files over LoRa, or switched off) stays in place, dimmed, and can't be opened.
        val iconTabs = listOf(
            Triple("dm", R.drawable.ic_tab_envelope to R.drawable.ic_tab_envelope_new, unread > 0),
            Triple("board", R.drawable.ic_tab_board to R.drawable.ic_tab_board_new, false),
            Triple("files", R.drawable.ic_tab_files to R.drawable.ic_tab_files_new, false))
        val offered = mapOf("dm" to true,
            // the billboard over Wi-Fi, or over LoRa (/board); still loading counts as offered
            "board" to ((webKey != null && board.webState() != "off") ||
                        (reachState == "lora" && loraKey != null && loraBoard.optString("state") != "off")),
            "files" to (webKey != null && shelf.webState() != "off"))
        val shown = if (tab == "room" || offered[tab] == true) tab else "room"   // a tab whose feature turned out off
        val tabDescriptions = mapOf("dm" to stringResource(R.string.private_tab, unread),
            "board" to stringResource(R.string.tab_board), "files" to stringResource(R.string.tab_files))
        // A tap on a billboard or files icon: the underline goes there while the engine checks
        // the Stump still answers on the Wi-Fi (a probe), then it opens; or a note says why not
        // (600 ms) and the underline goes back to the room. One at a time: taps while it's busy
        // (checking, or the note) are ignored.
        var bounce by remember(key) { mutableStateOf<String?>(null) }
        var notice by remember(key) { mutableStateOf<String?>(null) }
        var busy by remember(key) { mutableStateOf(false) }
        val stumpName = view.optString("name").ifBlank { key.substringAfter(":") }
        val texts = mapOf("lora" to stringResource(R.string.tab_not_over_lora), "no_wifi" to stringResource(R.string.tab_no_wifi),
            "offline" to stringResource(R.string.stump_offline, stumpName),
            "board_off" to stringResource(R.string.tab_board_off), "files_off" to stringResource(R.string.tab_files_off))
        fun reason(id: String, state: String) = when {
            state == "wifi" -> texts.getValue("${id}_off")
            state == "lora" && id == "board" && loraBoard.optString("state") == "off" -> texts.getValue("board_off")
            else -> texts[state] ?: texts.getValue("offline")
        }
        fun bounceBack(id: String, why: String, startedAt: Long = System.currentTimeMillis()) {
            scope.launch {
                busy = true
                bounce = id
                delay((300 - (System.currentTimeMillis() - startedAt)).coerceAtLeast(0))
                notice = why
                delay(600)
                notice = null
                bounce = null
                tab = "room"
                busy = false
            }
        }
        fun open(id: String) {
            if (busy) return
            if (id == "room" || id == "dm") { tab = id; return }
            if (offered[id] != true) return bounceBack(id, reason(id, reachState))
            val started = System.currentTimeMillis()
            busy = true
            bounce = id
            scope.launch {
                val r = runCatching { JSONObject(withContext(Dispatchers.IO) { Engine.call("stump_reach", key, onWifi, true) }) }
                    .getOrNull()
                val state = r?.optString("state") ?: "offline"
                if (state == "wifi" || (state == "lora" && id == "board" && loraKey != null)) {
                    bounce = null; tab = id; busy = false
                } else {
                    busy = false
                    bounceBack(id, reason(id, state), started)
                }
            }
        }
        // On the billboard or files when it stops being reachable: say why, and back to the room.
        LaunchedEffect(tab, offered[tab]) {
            if ((tab == "board" || tab == "files") && offered[tab] == false && !busy) bounceBack(tab, reason(tab, reachState))
        }
        val under = bounce ?: shown                     // where the underline is
        BoxWithConstraints(Modifier.fillMaxWidth().background(Amber.Sidebar)) {
            val half = maxWidth / 2
            val slot = half / iconTabs.size
            val index = iconTabs.indexOfFirst { it.first == under }
            val lineStart by animateDpAsState(if (index < 0) 0.dp else half + slot * index, tween(220), label = "tab line")
            val lineWidth by animateDpAsState(if (index < 0) half else slot, tween(220), label = "tab width")
            val lineColor by animateColorAsState(if (under == "dm") Amber.DmNick else Amber.Ember, tween(220), label = "tab colour")
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min).padding(bottom = 2.dp)) {
                Box(Modifier.weight(1f).fillMaxHeight().clickable { open("room") }, contentAlignment = Alignment.CenterStart) {
                    // "#room (topic)", from the left; too long for the half, it scrolls by itself so it
                    // can be read through, left to right.
                    val topic = view.optString("topic").takeIf { it.isNotBlank() && it != "null" }
                    val roomColor = if (under == "room") Amber.Ember else Amber.Muted
                    Text(buildAnnotatedString {
                            withStyle(SpanStyle(color = roomColor)) { append("#" + view.optString("room", "…")) }
                            if (topic != null) withStyle(SpanStyle(color = Amber.Dim, fontWeight = FontWeight.Normal)) { append("  ($topic)") }
                        },
                        modifier = Modifier.padding(start = 12.dp, end = 4.dp, top = 10.dp, bottom = 10.dp)
                            .basicMarquee(iterations = Int.MAX_VALUE, initialDelayMillis = 1500, repeatDelayMillis = 2500),
                        maxLines = 1, style = MaterialTheme.typography.labelLarge)
                }
                Row(Modifier.weight(1f)) {
                    iconTabs.forEach { (id, icons, isNew) ->
                        val accent = if (id == "dm") Amber.DmNick else Amber.Ember
                        val available = offered[id] == true
                        val color = when { !available -> Amber.Border; under == id -> accent; isNew -> accent; else -> Amber.Muted }
                        Box(Modifier.weight(1f).fillMaxHeight()
                            .clickable { open(id) }
                            .semantics { contentDescription = tabDescriptions.getValue(id) + if (available) "" else ", " + reason(id, reachState) },
                            contentAlignment = Alignment.Center) {
                            androidx.compose.material3.Icon(
                                painterResource(if (isNew) icons.second else icons.first), contentDescription = null,
                                tint = color, modifier = Modifier.size(22.dp))
                        }
                    }
                }
            }
            Box(Modifier.align(Alignment.BottomStart).offset(x = lineStart).width(lineWidth).height(2.dp).background(lineColor))
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            Column(Modifier.fillMaxSize()) {
                when (shown) {
                    "dm" -> PrivatePane(key, view, threads, mesh, go)
                    "board" -> if (webKey == null && loraKey != null) LoraBoardPane(loraKey, loraBoard)
                               else BoardPane(webKey ?: key, board) { board = it }
                    "files" -> FilesPane(webKey ?: key, shelf) { shelf = it }
                    else -> RoomPane(key, view, mesh, plugins)
                }
            }
            androidx.compose.animation.AnimatedVisibility(visible = notice != null, enter = fadeIn(tween(120)),
                exit = fadeOut(tween(150)), modifier = Modifier.align(Alignment.TopCenter).padding(top = 10.dp)) {
                Text(notice ?: "", modifier = Modifier.clip(Medium).background(Amber.Panel)
                    .border(1.dp, Amber.Border, Medium).padding(horizontal = 14.dp, vertical = 8.dp),
                    style = MaterialTheme.typography.labelLarge.copy(color = Amber.Text))
            }
        }
    }
}

@Composable
private fun ColumnScope.RoomPane(key: String, view: JSONObject, mesh: Boolean, plugins: JSONObject? = null) {
    val draft = remember(key) { mutableStateOf("") }
    val rooms = view.optJSONArray("rooms")?.objects() ?: emptyList()
    val here = view.optString("room")
    // A room switch or a refresh on its way (the engine says, until the Stump answers; a tap
    // counts at once, for 2 s, until the engine's word comes): the buttons wait, the room
    // being joined blinks with "…", and "refresh" reads "refreshing…".
    var pressedJoin by remember(key) { mutableStateOf<String?>(null) }
    var pressedAt by remember(key) { mutableStateOf(0L) }
    var pressedRoomsAt by remember(key) { mutableStateOf(0L) }
    var recheck by remember { mutableStateOf(0) }
    LaunchedEffect(pressedAt, pressedRoomsAt) { delay(2100); recheck++ }
    val now = if (recheck >= 0) System.currentTimeMillis() else 0L
    val joining = view.optString("joining").takeIf { it.isNotBlank() && it != "null" }
        ?: pressedJoin?.takeIf { now - pressedAt < 2000 && !stumpSameRoom(it, here) }
    val refreshing = view.optBoolean("refreshing") || now - pressedRoomsAt < 2000
    var blink by remember { mutableStateOf(true) }
    LaunchedEffect(joining) { blink = true; while (joining != null) { delay(450); blink = !blink } }
    Row(Modifier.fillMaxWidth().background(Amber.Sidebar).horizontalScroll(rememberScrollState()).padding(8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        val names = (rooms.map { it.optString("name") } + here).filter { it.isNotBlank() }.distinct()
        names.forEach { r ->
            val info = rooms.firstOrNull { it.optString("name") == r }
            val tier = info?.optString("tier")?.takeIf { it == "minted" || it == "hybrid" }
            val target = joining != null && stumpSameRoom(r, joining)
            Text("#$r" + (tier?.let { " ·$it" } ?: "") + (info?.optInt("count", -1)?.takeIf { it >= 0 }?.let { " $it" } ?: "") +
                    (if (target) " …" else ""),
                modifier = Modifier.padding(end = 6.dp).clip(Small)
                    .border(1.dp, if (r == here || target) Amber.Ember else Amber.Border, Small)
                    .clickable(enabled = r != here && joining == null) {
                        pressedJoin = r; pressedAt = System.currentTimeMillis()
                        Engine.fire("stump_join", key, r)
                    }
                    .padding(horizontal = 8.dp, vertical = 5.dp),
                style = MaterialTheme.typography.labelMedium.copy(color = when {
                    target -> if (blink) Amber.Ember else Amber.Dim
                    r == here -> Amber.Ember
                    joining != null -> Amber.Border
                    else -> Amber.Muted
                }))
        }
        Text(stringResource(if (refreshing) R.string.rooms_refreshing else R.string.list_rooms),
            modifier = Modifier.clip(Small).clickable(enabled = !refreshing) {
                pressedRoomsAt = System.currentTimeMillis()
                Engine.fire("stump_command", key, "/rooms")
            }.padding(horizontal = 8.dp, vertical = 5.dp),
            style = MaterialTheme.typography.labelMedium.copy(color = if (refreshing) Amber.Dim else Amber.Ember))
    }
    // (The topic is in the room's tab, after its name.)
    val lines = view.optJSONArray("lines")?.objects() ?: emptyList()
    val list = rememberLazyListState()
    LaunchedEffect(lines.size) { if (lines.isNotEmpty()) list.scrollToItem(lines.lastIndex) }
    LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(8.dp)) {
        if (lines.isEmpty()) item { Empty(stringResource(if (mesh) R.string.room_empty_mesh else R.string.room_empty)) }
        items(lines, key = { it.optLong("id") }) { l ->
            // A line the node refused (⧗: too fast) wasn't sent: a tap sends it again.
            val failed = l.optInt("mine") == 1 && l.optString("state") in listOf("failed", "error", "auth")
            Box(if (failed) Modifier.clickable { Engine.fire("stump_post", key, l.optString("body")) } else Modifier) {
                RoomLine(l)
            }
        }
    }
    PluginChips(plugins) { word -> if (!draft.value.startsWith(word.trim())) draft.value = word }
    Composer(stringResource(R.string.room_hint, here), counter = { if (mesh) PacketCounter(it) }, draft = draft) {
        Engine.fire("stump_post", key, it)
    }
}

/** One RRC line, coloured by meaning like the node's own web chat. */
@Composable
private fun RoomLine(l: JSONObject) {
    // Refused by the node (⧗, too fast) or never delivered over LoRa: two different reasons.
    // "error": it never reached the node over Wi-Fi (the reason is the line just after it).
    val notSent = stringResource(when (l.optString("local_state").takeIf { !l.isNull("local_state") }) {
        "failed" -> R.string.line_not_sent
        "error" -> R.string.line_not_sent_net
        "auth" -> R.string.line_not_sent_auth
        else -> R.string.line_not_delivered
    })
    val lineFailed = l.optString("state") in listOf("failed", "error", "auth")
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
            withStyle(SpanStyle(color = if (lineFailed) Amber.Error else Amber.Dim)) {
                append("  " + stateGlyph(if (lineFailed) "failed" else l.optString("state")))
            }
            if (lineFailed) withStyle(SpanStyle(color = Amber.Error)) {
                append("  " + notSent)
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
    // People first, then the Stumps (🪵, as the node's web chat groups them). On a chain,
    // people on other Stumps are listed as nick@thatstump and reached the same way.
    val blocked = view.optJSONArray("blocked")?.strings() ?: emptyList()
    val bots = view.optJSONArray("bots")?.strings()?.toSet() ?: emptySet()     // "bot": true (beta.36): no DMs to them
    val others = (view.optJSONArray("users")?.strings() ?: emptyList()).map { it.removePrefix("~") }
        .filter { it != me && it !in talking && it.lowercase() !in blocked && it !in bots }.sortedBy { it in stumps }
    var to by remember { mutableStateOf("") }
    var choosing by remember { mutableStateOf<String?>(null) }
    var deleting by remember { mutableStateOf<String?>(null) }
    var blocking by remember { mutableStateOf<String?>(null) }
    choosing?.let { nick ->
        PersonActions(nick, onDismiss = { choosing = null },
            onDelete = { deleting = nick; choosing = null }, onBlock = { blocking = nick; choosing = null },
            deleteText = stringResource(R.string.stump_dm_delete_short), blockText = stringResource(R.string.stump_dm_block_short))
    }
    deleting?.let { nick -> ConfirmStumpDmDelete(nick, onDismiss = { deleting = null }) { Engine.fire("stump_dm_delete", key, nick); deleting = null } }
    blocking?.let { nick -> ConfirmStumpBlock(nick, onDismiss = { blocking = null }) { Engine.fire("stump_block_nick", key, nick); blocking = null } }
    LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
        item { Text(stringResource(R.string.direct_messages), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(12.dp)) }
        if (threads.isEmpty()) item { Empty(stringResource(R.string.no_dms)) }
        items(threads, key = { "t" + it.optString("nick") }) { t ->
            // Tap: the conversation. Long press: delete it, or block them on this Stump.
            Row(Modifier.fillMaxWidth().rowPress(onClick = { go(Route.Dm(key, t.optString("nick"))) },
                onLongClick = { choosing = t.optString("nick") }), verticalAlignment = Alignment.CenterVertically) {
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
                Text((if (nick in stumps) "🪵 " else "") + nick, style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.weight(1f))
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
        if (blocked.isNotEmpty()) {
            item { Text(stringResource(R.string.stump_blocked_here), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(12.dp)) }
            items(blocked, key = { "b$it" }) { nick ->
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(nick, style = MaterialTheme.typography.bodyLarge.copy(color = Amber.Dim), modifier = Modifier.weight(1f))
                    Button(stringResource(R.string.unblock), primary = false) { Engine.fire("stump_unblock_nick", key, nick) }
                }
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
        var deleting by remember { mutableStateOf(false) }
        var blocking by remember { mutableStateOf(false) }
        if (deleting) ConfirmStumpDmDelete(nick, onDismiss = { deleting = false }) {
            Engine.fire("stump_dm_delete", key, nick); deleting = false; back()
        }
        if (blocking) ConfirmStumpBlock(nick, onDismiss = { blocking = false }) {
            Engine.fire("stump_block_nick", key, nick); blocking = false; back()
        }
        TopBar(nick, stringResource(R.string.dm_via, node.optString("name")), back) {
            OverflowMenu(listOf(
                stringResource(R.string.delete) to { deleting = true },
                stringResource(R.string.block) to { blocking = true }))
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(vertical = 8.dp)) {
            if (msgs.isEmpty()) item { Empty(stringResource(R.string.dm_empty)) }
            items(msgs, key = { it.optLong("id") }) { m ->
                val mine = m.optInt("outgoing") == 1
                Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                    horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
                    if (!m.isNull("audio_mode")) VoiceBubble(m, mine, fetch = "stump_dm_audio", dm = true)
                    else Box(Modifier.widthIn(max = 320.dp).clip(Large).background(Amber.Panel)
                        .border(1.dp, Amber.DmBody.copy(alpha = if (mine) 0.5f else 0.25f), Large)
                        .clickable(enabled = mine && m.optString("state") == "failed") {
                            Engine.fire("stump_send_dm", key, nick, m.optString("body"))      // send it again
                        }.padding(10.dp)) {
                        SelectionContainer { Text(m.optString("body"), style = MaterialTheme.typography.bodyLarge.copy(color = Amber.DmBody)) }
                    }
                    val state = m.optString("state")
                    val meta = clock(m.optDouble("ts")) + if (mine) "  " + when (state) {
                        "pending" -> stringResource(R.string.dm_pending)
                        "delivered" -> stringResource(R.string.dm_delivered)
                        "failed" -> stringResource(R.string.dm_failed, m.optString("reason")) +
                            (if (m.isNull("audio_mode")) "  " + stringResource(R.string.tap_to_resend) else "")
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

/**
 * Entering a Stump: "Verifying you with <name>…" until the Stump has FireFly's signature
 * (/hello over Wi-Fi, /auth over LoRa), then the room opens by itself. Already verified this
 * run: no popup at all. A failure says why and offers Try again, Go in anyway, or Back.
 */
@Composable
private fun VerifyGate(key: String, name: String, mesh: Boolean, onEnter: () -> Unit, onBack: () -> Unit) {
    var state by remember(key) { mutableStateOf<String?>(null) }      // null: not asked yet (no popup)
    var detail by remember(key) { mutableStateOf("") }
    var attempt by remember(key) { mutableIntStateOf(0) }
    var elapsed by remember(key) { mutableIntStateOf(0) }
    LaunchedEffect(key, attempt) {
        val started = System.currentTimeMillis()
        var again = true                    // entering, or Try again: ask anew; then only read
        while (true) {
            val v = Engine.obj("stump_verify", key, again)
            again = false
            val st = v.optString("state", "verifying")
            elapsed = ((System.currentTimeMillis() - started) / 1000).toInt()
            if (st == "ok" || st == "not_needed") {
                if (state != null) { state = "ok"; delay(600) }        // seen: "Verified ✓"
                onEnter(); return@LaunchedEffect
            }
            state = st
            detail = v.optString("detail").takeIf { it != "null" } ?: ""
            if (st == "failed") return@LaunchedEffect
            delay(700)
        }
    }
    val st = state ?: return
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onBack, containerColor = Amber.Panel, shape = Large,
        properties = androidx.compose.ui.window.DialogProperties(dismissOnClickOutside = false),
        title = { Text(stringResource(R.string.verify_gate_title, name), style = MaterialTheme.typography.titleMedium) },
        text = {
            Column {
                when (st) {
                    "ok" -> Text(stringResource(R.string.verify_gate_ok),
                        style = MaterialTheme.typography.bodyMedium.copy(color = Amber.Ember))
                    "failed" -> {
                        Text(stringResource(R.string.verify_failed, detail.ifBlank { "?" }),
                            style = MaterialTheme.typography.bodyMedium.copy(color = Amber.Error))
                        Spacer(Modifier.height(6.dp))
                        Text(stringResource(R.string.verify_gate_failed_help), style = MaterialTheme.typography.bodySmall)
                    }
                    else -> {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            androidx.compose.material3.CircularProgressIndicator(Modifier.size(20.dp), color = Amber.Ember,
                                strokeWidth = 2.dp)
                            Spacer(Modifier.width(12.dp))
                            Text(stringResource(R.string.verify_gate_body), style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.weight(1f))
                        }
                        Spacer(Modifier.height(6.dp))
                        Text(stringResource(if (mesh) R.string.verify_gate_lora else R.string.verify_gate_wifi, elapsed),
                            style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        },
        confirmButton = {
            if (st == "failed") Row {
                Text(stringResource(R.string.verify_gate_enter_anyway), modifier = Modifier.clip(Small)
                    .clickable { onEnter() }.padding(10.dp), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
                Text(stringResource(R.string.verify_gate_retry), modifier = Modifier.clip(Small)
                    .clickable { state = "verifying"; elapsed = 0; attempt++ }.padding(10.dp),
                    style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
            }
        },
        dismissButton = {
            if (st != "ok") Text(stringResource(R.string.verify_gate_back), modifier = Modifier.clip(Small)
                .clickable { onBack() }.padding(10.dp), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        })
}

@Composable
private fun ConfirmRemoveStump(name: String, onDismiss: () -> Unit, onConfirm: () -> Unit) =
    ConfirmDialog(stringResource(R.string.stump_remove_title, name), stringResource(R.string.stump_remove_confirm),
        stringResource(R.string.stump_remove), onConfirm = onConfirm, onDismiss = onDismiss)


/**
 * Every node of a mesh needs the same radio settings: a mismatched radio doesn't error, it
 * just never hears anything. When the node's (/node.json) differ from ours, say so and offer
 * to use them. TX power stays ours.
 */
@Composable
private fun RadioMatch(radio: JSONObject?, onUse: () -> Unit) {
    val node = radio?.optJSONObject("node") ?: return
    val differs = radio.optJSONArray("differs")?.strings() ?: emptyList()
    if (differs.isEmpty()) return
    val f = node.optLong("frequency") / 1_000_000.0
    val bw = node.optLong("bandwidth") / 1000.0
    val desc = "%.3f MHz · %s kHz · SF%d · CR 4/%d".format(f, if (bw % 1.0 == 0.0) "%.0f".format(bw) else "%.1f".format(bw),
        node.optInt("sf"), node.optInt("cr"))
    Row(Modifier.fillMaxWidth().background(Amber.Sidebar).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(stringResource(R.string.radio_mismatch), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Error))
            Text(stringResource(R.string.radio_mismatch_detail, desc), style = MaterialTheme.typography.labelSmall)
        }
        Spacer(Modifier.width(8.dp))
        Button(stringResource(R.string.radio_use_these), onClick = onUse)
    }
    Box(Modifier.fillMaxWidth().height(1.dp).background(Amber.Border))
}


/** A Stump opened over LoRa with no radio and no other path: nothing can be sent. Say so. */
@Composable
private fun NoRadioBanner(wifiKey: String?, looking: Boolean, openWifi: (String?) -> Unit) {
    Row(Modifier.fillMaxWidth().background(Amber.Sidebar).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(stringResource(R.string.no_radio_title), style = MaterialTheme.typography.labelLarge.copy(color = Amber.Error))
            Text(stringResource(if (wifiKey != null) R.string.no_radio_wifi else R.string.no_radio_text),
                style = MaterialTheme.typography.labelSmall)
        }
        Spacer(Modifier.width(8.dp))
        // Its Wi-Fi entry if there is one; otherwise look for it on this network.
        Button(stringResource(when {
            wifiKey != null -> R.string.open_over_wifi
            looking -> R.string.find_lan_searching
            else -> R.string.find_over_wifi
        }), enabled = wifiKey != null || !looking) { openWifi(wifiKey) }
    }
    Box(Modifier.fillMaxWidth().height(1.dp).background(Amber.Border))
}


/** Delete a private conversation on a Stump: this phone only; they can write again. */
@Composable
private fun ConfirmStumpDmDelete(nick: String, onDismiss: () -> Unit, onConfirm: () -> Unit) =
    ConfirmDialog(stringResource(R.string.delete_conv_title), stringResource(R.string.stump_dm_delete_text, nick),
        stringResource(R.string.delete), onConfirm = onConfirm, onDismiss = onDismiss)

/** Block a nick on a Stump: the conversation goes, and their private messages are dropped here. */
@Composable
private fun ConfirmStumpBlock(nick: String, onDismiss: () -> Unit, onConfirm: () -> Unit) =
    ConfirmDialog(stringResource(R.string.block_title, nick), stringResource(R.string.stump_block_text, nick),
        stringResource(R.string.block), onConfirm = onConfirm, onDismiss = onDismiss)


/** The same room by the Stump's names for it (#lxmf is #lora-mesh), case aside. */
private fun stumpSameRoom(a: String, b: String): Boolean {
    val alias = mapOf("lxmf" to "lora-mesh", "lora" to "lora-mesh", "chained" to "wifi-mesh", "mesh" to "wifi-mesh")
    fun canon(r: String) = r.trim().removePrefix("#").lowercase().let { alias[it] ?: it }
    return canon(a) == canon(b)
}
