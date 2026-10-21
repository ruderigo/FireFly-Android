package io.github.ruderigo.firefly.ui

import android.text.format.Formatter
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.objects
import io.github.ruderigo.firefly.net.FileTransfers
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/*
 * A Stump's billboard and file shelf, over Wi-Fi (CLIENT_QUICKSTART.md,
 * "Billboard" and "Files (fservbot)"). Only for a node reached over Wi-Fi:
 * neither ever rides the mesh. A tab whose feature is off on the node
 * (404 "not offered on this node") isn't shown at all.
 */

const val TITLE_MAX = 80
const val BODY_MAX = 600

/** "ok" | "off" | "unreachable" | "error" | "" (not loaded yet). */
fun JSONObject?.webState(): String = this?.optString("state", "") ?: ""

@Composable
fun ColumnScope.BoardPane(key: String, board: JSONObject?, onBoard: (JSONObject) -> Unit) {
    val scope = rememberCoroutineScope()
    var title by remember(key) { mutableStateOf("") }
    var body by remember(key) { mutableStateOf("") }
    var posting by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<Pair<String, Boolean>?>(null) }      // text, ok
    var loading by remember { mutableStateOf(false) }
    val posts = board?.optJSONArray("posts")?.objects() ?: emptyList()
    val reasons = mapOf(
        "dropped" to stringResource(R.string.board_dropped),
        "unreachable" to stringResource(R.string.web_unreachable),
        "off" to stringResource(R.string.board_off),
        "storage" to stringResource(R.string.board_storage),
        "rate_limited" to stringResource(R.string.board_rate_limited),
    )
    val postedText = stringResource(R.string.board_posted)
    val errorText = stringResource(R.string.web_error_generic)

    var query by remember(key) { mutableStateOf("") }

    fun refresh() {
        loading = true
        scope.launch { onBoard(Engine.obj("stump_board", key, query.trim())); loading = false }
    }

    LazyColumn(Modifier.weight(1f).fillMaxWidth(), contentPadding = PaddingValues(bottom = 8.dp)) {
        item {
            WebHeader(stringResource(R.string.board_intro), loading) { refresh() }
        }
        item { SearchRow(query, { query = it }, stringResource(R.string.board_search_hint)) { refresh() } }
        when (board.webState()) {
            "" -> item { Empty(stringResource(R.string.web_loading)) }
            "unreachable", "error" -> item { WebProblem(board!!) { refresh() } }
            else -> {
                if (posts.isEmpty()) item { Empty(stringResource(R.string.board_empty)) }
                items(posts) { BoardPost(it) }          // ids can be null (older posts): no keys
            }
        }
    }
    // Writing a post: title required (80), details optional (600, line breaks kept).
    Column(Modifier.fillMaxWidth().background(Amber.Sidebar).navigationBarsPadding().imePadding().padding(8.dp)) {
        result?.let { (text, ok) ->
            Text(text, style = MaterialTheme.typography.labelMedium.copy(color = if (ok) Amber.Ember else Amber.Error),
                modifier = Modifier.padding(start = 4.dp, bottom = 6.dp))
        }
        Field(title, { title = it.replace("\n", " ").take(TITLE_MAX) }, Modifier.fillMaxWidth(), hint = stringResource(R.string.board_title_hint))
        Spacer(Modifier.height(6.dp))
        Field(body, { body = it.take(BODY_MAX) }, Modifier.fillMaxWidth().heightIn(min = 44.dp, max = 140.dp),
            hint = stringResource(R.string.board_body_hint), singleLine = false)
        Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("${title.length}/$TITLE_MAX · ${body.length}/$BODY_MAX", style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.weight(1f).padding(start = 4.dp))
            Button(stringResource(if (posting) R.string.board_posting else R.string.board_post),
                enabled = title.isNotBlank() && !posting) {
                posting = true
                result = null
                val t = title; val b = body
                scope.launch {
                    val r = Engine.obj("stump_board_post", key, t, b)
                    r.optJSONObject("board")?.let(onBoard)
                    if (r.optBoolean("ok")) {
                        title = ""; body = ""
                        result = postedText to true
                    } else {
                        result = (reasons[r.optString("reason")] ?: errorText) to false
                    }
                    posting = false
                }
            }
        }
    }
}

@Composable
private fun BoardPost(p: JSONObject) {
    var open by remember { mutableStateOf(false) }
    val body = p.optString("body")
    val expires = if (p.isNull("expires_in")) -1 else p.optInt("expires_in", -1)
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp).clip(Large).background(Amber.Panel)
        .border(1.dp, Amber.Border, Large).clickable(enabled = body.isNotEmpty()) { open = !open }.padding(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(p.optString("title"), style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold),
                modifier = Modifier.weight(1f))
            if (body.isNotEmpty()) Text(if (open) "▾" else "▸", style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
        }
        val meta = listOfNotNull(
            p.optString("sig").takeIf { it.isNotBlank() }?.let { "— $it" },
            if (!p.isNull("posted")) clock(p.optDouble("posted")) else null,
            when {
                expires < 0 -> null
                expires < 3600 -> stringResource(R.string.board_expires_soon)
                else -> stringResource(R.string.board_expires_in, expires / 3600)
            },
        ).joinToString("  ")
        if (meta.isNotEmpty()) Text(meta, style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 2.dp))
        if (open && body.isNotEmpty()) SelectionContainer {
            Text(body, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp))
        }
    }
}

@Composable
fun ColumnScope.FilesPane(key: String, shelf: JSONObject?, onShelf: (JSONObject) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val transfer by FileTransfers.current.collectAsState()
    var loading by remember { mutableStateOf(false) }
    var picked by remember { mutableStateOf<Triple<android.net.Uri, String, Long>?>(null) }   // to upload
    var saving by remember { mutableStateOf<JSONObject?>(null) }                             // to download
    val credits = shelf?.optBoolean("credits") ?: false
    val busy = transfer?.state == FileTransfers.State.RUNNING       // one transfer at a time

    var query by remember(key) { mutableStateOf("") }
    var cat by remember(key) { mutableStateOf("") }

    fun refresh() {
        loading = true
        scope.launch { onShelf(Engine.obj("stump_files", key, query.trim(), cat)); loading = false }
    }
    // A finished upload changes the shelf (and maybe evicted old files): read it again.
    LaunchedEffect(transfer?.state, transfer?.kind) {
        val t = transfer
        if (t != null && t.nodeKey == key && t.kind == FileTransfers.Kind.UPLOAD && t.state == FileTransfers.State.DONE) refresh()
    }

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            val (name, size) = FileTransfers.describe(ctx, uri)
            picked = Triple(uri, name, size)
        }
    }
    val save = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val f = saving
        saving = null
        if (uri != null && f != null) {
            val name = f.optString("name")
            scope.launch {
                val url = withContext(Dispatchers.IO) { Engine.call("stump_download_url", key, name) }
                if (url.isNotEmpty()) FileTransfers.download(ctx, key, url, name,
                    if (f.isNull("size")) -1L else f.optLong("size", -1L), uri)
            }
        }
    }

    picked?.let { (uri, name, size) ->
        UploadDialog(name, size, credits, onDismiss = { picked = null }) { slot ->
            picked = null
            scope.launch {
                val url = withContext(Dispatchers.IO) { Engine.call("stump_upload_url", key) }
                if (url.isNotEmpty()) FileTransfers.upload(ctx, key, url, uri, name, size, slot)
            }
        }
    }

    transfer?.takeIf { it.nodeKey == key }?.let { TransferBar(it) }

    LazyColumn(Modifier.weight(1f).fillMaxWidth(), contentPadding = PaddingValues(bottom = 8.dp)) {
        item {
            WebHeader(stringResource(if (credits) R.string.files_intro else R.string.files_intro_free), loading) { refresh() }
        }
        item { Column {
            SearchRow(query, { query = it }, stringResource(R.string.files_search_hint)) { refresh() }
            // The node's kinds of file (cat): a tap filters, a second tap shows everything again.
            val cats = listOf("video" to R.string.cat_video, "music" to R.string.cat_music, "document" to R.string.cat_document,
                "image" to R.string.cat_image, "other" to R.string.cat_other)
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 4.dp)) {
                cats.forEach { (c, label) ->
                    Text(classGlyph(c) + " " + stringResource(label), modifier = Modifier.padding(end = 6.dp).clip(Small)
                        .border(1.dp, if (cat == c) Amber.Ember else Amber.Border, Small)
                        .clickable { cat = if (cat == c) "" else c; refresh() }
                        .padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelMedium.copy(color = if (cat == c) Amber.Ember else Amber.Muted))
                }
            }
        } }
        when (shelf.webState()) {
            "" -> item { Empty(stringResource(R.string.web_loading)) }
            "unreachable", "error" -> item { WebProblem(shelf!!) { refresh() } }
            else -> {
                val files = shelf!!.optJSONArray("files")?.objects() ?: emptyList()
                if (!shelf.optBoolean("sd", true)) item { Empty(stringResource(R.string.files_no_sd)) }
                else if (files.isEmpty()) item { Empty(stringResource(R.string.files_empty)) }
                items(files, key = { it.optString("name") }) { f ->
                    FileRow(f, credits, enabled = !busy) { saving = f; save.launch(f.optString("name")) }
                }
            }
        }
    }
    if (shelf.webState() == "ok" && shelf!!.optBoolean("sd", true)) {
        Row(Modifier.fillMaxWidth().background(Amber.Sidebar).navigationBarsPadding().padding(8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.files_bring_help), style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.weight(1f).padding(start = 4.dp, end = 8.dp))
            Button(stringResource(R.string.files_bring), enabled = !busy) { pick.launch("*/*") }
        }
    }
}

private fun classGlyph(c: String) = when (c) { "video" -> "▶"; "music" -> "♫"; "document" -> "¶"; "image" -> "▣"; else -> "◇" }

/** A search box: Done (or the ⌕) asks the node again with these words. */
@Composable
private fun SearchRow(value: String, onChange: (String) -> Unit, hint: String, onSearch: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Field(value, onChange, Modifier.weight(1f), hint = hint, onDone = onSearch)
        Text("⌕", modifier = Modifier.padding(start = 6.dp).clip(Small).clickable(onClick = onSearch).padding(8.dp),
            style = MaterialTheme.typography.titleLarge.copy(color = Amber.Ember))
        if (value.isNotEmpty()) Text("✕", modifier = Modifier.clip(Small).clickable { onChange(""); onSearch() }.padding(8.dp),
            style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
    }
}

@Composable
private fun FileRow(f: JSONObject, credits: Boolean, enabled: Boolean, onSave: () -> Unit) {
    val ctx = LocalContext.current
    Row(Modifier.fillMaxWidth().clickable(enabled = enabled, onClick = onSave).padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(classGlyph(f.optString("cat").ifBlank { f.optString("class") }), style = MaterialTheme.typography.titleLarge.copy(color = Amber.Ember),
            modifier = Modifier.width(32.dp))
        Column(Modifier.weight(1f)) {
            Text(f.optString("name"), style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(
                if (f.isNull("size")) stringResource(R.string.files_size_unknown) else Formatter.formatShortFileSize(ctx, f.optLong("size")),
                if (credits) stringResource(R.string.files_cost, f.optInt("cost")) else null,
            ).joinToString("  "), style = MaterialTheme.typography.labelMedium)
        }
        Text("↓", style = MaterialTheme.typography.titleLarge.copy(color = if (enabled) Amber.Ember else Amber.Dim))
    }
}

@Composable
private fun UploadDialog(name: String, size: Long, credits: Boolean, onDismiss: () -> Unit, onUpload: (String?) -> Unit) {
    val ctx = LocalContext.current
    var slot by remember { mutableStateOf("") }
    val cost by produceState(-1, name, credits) {
        value = withContext(Dispatchers.IO) { Engine.call("stump_upload_cost", name, credits).toIntOrNull() ?: -1 }
    }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = Amber.Panel, shape = Large,
        title = { Text(stringResource(R.string.files_upload_title, name), style = MaterialTheme.typography.titleMedium) },
        text = {
            Column {
                Text(listOfNotNull(
                    if (size >= 0) Formatter.formatShortFileSize(ctx, size) else null,
                    if (credits && cost >= 0) stringResource(R.string.files_upload_worth, cost) else null,
                ).joinToString(" · "), style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(12.dp))
                Text(stringResource(R.string.files_slot_help), style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(6.dp))
                Field(slot, { slot = it.trim() }, Modifier.fillMaxWidth(), hint = stringResource(R.string.files_slot_hint), mono = true)
            }
        },
        confirmButton = {
            Text(stringResource(R.string.files_upload), modifier = Modifier.clip(Small).clickable { onUpload(slot.ifBlank { null }) }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember))
        },
        dismissButton = {
            Text(stringResource(R.string.cancel), modifier = Modifier.clip(Small).clickable { onDismiss() }.padding(10.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        })
}

/** Progress, then the node's own answer ("Uploaded. Your balance: 4") or why it failed. */
@Composable
private fun TransferBar(t: FileTransfers.Transfer) {
    val ctx = LocalContext.current
    val running = t.state == FileTransfers.State.RUNNING
    val upload = t.kind == FileTransfers.Kind.UPLOAD
    val headline = when (t.state) {
        FileTransfers.State.RUNNING -> stringResource(if (upload) R.string.files_uploading else R.string.files_downloading, t.name)
        FileTransfers.State.DONE -> when {
            !upload -> stringResource(R.string.files_saved, t.name)
            t.slot -> stringResource(R.string.files_slot_delivered, t.storedName ?: t.name)
            t.balance != null -> stringResource(R.string.files_uploaded_balance, t.storedName ?: t.name, t.balance)
            t.storedName != null -> stringResource(R.string.files_uploaded, t.storedName)
            else -> t.message.ifBlank { stringResource(R.string.files_uploaded, t.name) }    // an older node's sentence
        }
        FileTransfers.State.FAILED -> stringResource(R.string.files_failed, t.name, failReason(t))
    }
    Column(Modifier.fillMaxWidth().background(Amber.Sidebar).padding(horizontal = 12.dp, vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(headline, style = MaterialTheme.typography.labelMedium.copy(
                color = if (t.state == FileTransfers.State.FAILED) Amber.Error else if (running) Amber.Text else Amber.Ember),
                modifier = Modifier.weight(1f))
            if (!running) Text("✕", modifier = Modifier.clip(Small).clickable { FileTransfers.clear() }.padding(horizontal = 8.dp),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
        }
        if (running) {
            val fraction = if (t.total > 0) (t.done.toFloat() / t.total).coerceIn(0f, 1f) else 0f
            Box(Modifier.fillMaxWidth().padding(top = 6.dp).height(4.dp).clip(Small).background(Amber.Row)) {
                Box(Modifier.fillMaxWidth(fraction).fillMaxHeight().background(Amber.Ember))
            }
            Text(Formatter.formatShortFileSize(ctx, t.done) + if (t.total > 0) " / " + Formatter.formatShortFileSize(ctx, t.total) else "",
                style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 2.dp))
        }
    }
}

@Composable
private fun failReason(t: FileTransfers.Transfer): String = when (t.error) {
    // A current node names the failure with a stable token; an older one only has the status.
    "no_filename", "empty" -> stringResource(R.string.files_fail_400)
    "no_card" -> stringResource(R.string.files_fail_503)
    "full" -> stringResource(R.string.files_fail_507)
    "interrupted" -> stringResource(R.string.files_fail_500)
    else -> failByStatus(t)
}

@Composable
private fun failByStatus(t: FileTransfers.Transfer): String = when (t.status) {
    400 -> stringResource(R.string.files_fail_400)
    404 -> stringResource(R.string.files_fail_404)
    500 -> stringResource(R.string.files_fail_500)
    503 -> stringResource(R.string.files_fail_503)
    507 -> stringResource(R.string.files_fail_507)
    -1 -> stringResource(R.string.files_fail_lost)
    else -> t.message.ifBlank { "HTTP ${t.status}" }
}

@Composable
private fun WebHeader(intro: String, loading: Boolean, onRefresh: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.Top) {
        Text(intro, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f).padding(end = 8.dp))
        Text(stringResource(if (loading) R.string.web_loading else R.string.web_refresh),
            modifier = Modifier.clip(Small).clickable(enabled = !loading, onClick = onRefresh).padding(6.dp),
            style = MaterialTheme.typography.labelMedium.copy(color = if (loading) Amber.Dim else Amber.Ember))
    }
}

@Composable
private fun WebProblem(r: JSONObject, onRetry: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(if (r.webState() == "unreachable") stringResource(R.string.web_unreachable)
             else stringResource(R.string.web_error, r.optInt("status")),
            style = MaterialTheme.typography.bodyMedium.copy(color = Amber.Muted))
        Spacer(Modifier.height(8.dp))
        Button(stringResource(R.string.web_retry), primary = false, onClick = onRetry)
    }
}


/** This app's version and build number, as people read it: "0.5.0-beta.9 (build 25)". */
fun versionLabel(ctx: android.content.Context): String {
    val info = runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0) }.getOrNull()
    val code = info?.let { androidx.core.content.pm.PackageInfoCompat.getLongVersionCode(it) }
    return (info?.versionName ?: "?") + (code?.let { " (build $it)" } ?: "")
}

/** This app's version, as the node compares versions (0.5.0-beta.1). */
fun myVersion(ctx: android.content.Context): String =
    runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }.getOrNull() ?: "0"

/**
 * "A newer FireFly is on this Stump" (from /tools.json): download it where the
 * user chooses, then hand it to Android's installer. It installs over this one
 * only if it's signed with the same key; Android says so if it isn't.
 */
@Composable
fun UpdateBanner(key: String, tools: JSONObject?) {
    val update = tools?.optJSONObject("update") ?: return
    val ctx = LocalContext.current
    val transfer by FileTransfers.current.collectAsState()
    val file = update.optString("file").ifBlank { "FireFly-Android-${update.optString("version")}.apk" }
    val mine = transfer?.takeIf { it.nodeKey == key && it.kind == FileTransfers.Kind.DOWNLOAD && it.name == file }
    val busy = transfer?.state == FileTransfers.State.RUNNING
    var dismissed by remember(key) { mutableStateOf(false) }
    if (dismissed && mine == null) return

    val save = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/vnd.android.package-archive")) { uri ->
        if (uri != null) FileTransfers.download(ctx, key, update.optString("url"), file,
            if (update.isNull("size")) -1L else update.optLong("size", -1L), uri)
    }
    Column(Modifier.fillMaxWidth().background(Amber.Sidebar).padding(horizontal = 12.dp, vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.update_on_node, update.optString("version")),
                style = MaterialTheme.typography.labelLarge.copy(color = Amber.Ember), modifier = Modifier.weight(1f))
            val target = mine?.target
            when {
                mine != null && mine.state == FileTransfers.State.RUNNING -> Text(
                    if (mine.total > 0) "${100 * mine.done / mine.total} %" else Formatter.formatShortFileSize(ctx, mine.done),
                    style = MaterialTheme.typography.labelMedium)
                mine != null && mine.state == FileTransfers.State.DONE && target != null ->
                    Button(stringResource(R.string.update_install)) { install(ctx, target) }
                else -> {
                    Button(stringResource(R.string.update_get), enabled = !busy) { save.launch(file) }
                    Spacer(Modifier.width(4.dp))
                    Text("✕", modifier = Modifier.clip(Small).clickable { dismissed = true; if (mine != null) FileTransfers.clear() }.padding(8.dp),
                        style = MaterialTheme.typography.labelLarge.copy(color = Amber.Muted))
                }
            }
        }
        Text(when {
                mine != null && mine.state == FileTransfers.State.FAILED -> stringResource(R.string.files_failed, file, failReason(mine))
                mine != null && mine.state == FileTransfers.State.DONE -> stringResource(R.string.update_ready)
                else -> listOfNotNull(
                    if (update.isNull("size")) null else Formatter.formatShortFileSize(ctx, update.optLong("size")),
                    stringResource(R.string.update_same_key)).joinToString(" · ")
            },
            style = MaterialTheme.typography.labelSmall.copy(
                color = if (mine?.state == FileTransfers.State.FAILED) Amber.Error else Amber.Dim),
            modifier = Modifier.padding(top = 2.dp))
    }
    Box(Modifier.fillMaxWidth().height(1.dp).background(Amber.Border))
}

/** Android's installer, after the user has allowed FireFly to install apps (asked once). */
private fun install(ctx: android.content.Context, apk: android.net.Uri) {
    val pm = ctx.packageManager
    if (android.os.Build.VERSION.SDK_INT >= 26 && !pm.canRequestPackageInstalls()) {
        ctx.startActivity(android.content.Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            android.net.Uri.parse("package:" + ctx.packageName)).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
        return
    }
    val view = android.content.Intent(android.content.Intent.ACTION_VIEW)
        .setDataAndType(apk, "application/vnd.android.package-archive")
        .addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { ctx.startActivity(view) }
}

/** The node's plugin commands (/plugins.json) as chips: a tap puts "!roll " in the box. */
@Composable
fun PluginChips(plugins: JSONObject?, onPick: (String) -> Unit) {
    val commands = plugins?.optJSONArray("commands")?.objects() ?: return
    if (commands.isEmpty()) return
    Row(Modifier.fillMaxWidth().background(Amber.Sidebar).horizontalScroll(rememberScrollState())
        .padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        commands.forEach { c ->
            val word = c.optString("word")
            Text(word, modifier = Modifier.padding(end = 6.dp).clip(Small).border(1.dp, Amber.Border, Small)
                .clickable { onPick("$word ") }.padding(horizontal = 8.dp, vertical = 4.dp),
                style = MaterialTheme.typography.labelMedium.copy(color = Amber.Action))
        }
    }
}
