package io.github.ruderigo.firefly.ui

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.ruderigo.firefly.FireFlyApp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.engine.EngineService
import io.github.ruderigo.firefly.radio.BlePermissions
import kotlinx.coroutines.flow.MutableStateFlow

sealed interface Route {
    data class Home(val tab: Int = 0) : Route
    data class Chat(val peer: String) : Route
    data class Node(val key: String) : Route
    data class Dm(val key: String, val nick: String) : Route
    data object Pair : Route
}

class MainActivity : ComponentActivity() {

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        Engine.searchRadio()            // BLE permission may have just been granted
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val wanted = BlePermissions.required.toMutableList()
        if (Build.VERSION.SDK_INT >= 33) wanted += Manifest.permission.POST_NOTIFICATIONS
        permissions.launch(wanted.toTypedArray())
        EngineService.start(this)
        if (savedInstanceState == null) routeFrom(intent)
        setContent { FireFlyTheme { App() } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (!routeFrom(intent)) Engine.searchRadio()      // not a notification: an RNode was plugged in
    }

    /** A notification says which conversation to open. */
    private fun routeFrom(intent: Intent?): Boolean {
        if (intent == null) return false
        val route: Route? = when (intent.getStringExtra(EXTRA_OPEN)) {
            "chat" -> intent.getStringExtra(EXTRA_PEER)?.let { Route.Chat(it) }
            "dm" -> {
                val node = intent.getStringExtra(EXTRA_NODE); val nick = intent.getStringExtra(EXTRA_NICK)
                if (node != null && nick != null) Route.Dm(node, nick) else null
            }
            else -> null
        }
        if (route == null) return false
        pendingRoute.value = route
        intent?.removeExtra(EXTRA_OPEN)          // don't reopen it on rotation or return
        return true
    }

    companion object {
        const val EXTRA_OPEN = "open"
        const val EXTRA_PEER = "peer"
        const val EXTRA_NODE = "node"
        const val EXTRA_NICK = "nick"
        val pendingRoute = MutableStateFlow<Route?>(null)
    }

    override fun onResume() { super.onResume(); FireFlyApp.inForeground = true; Engine.refreshStatus() }
    override fun onPause() { super.onPause(); FireFlyApp.inForeground = false }
}

@Composable
fun App() {
    val stack = remember { mutableStateListOf<Route>(Route.Home()) }
    val go: (Route) -> Unit = { stack.add(it) }
    val back: () -> Unit = { if (stack.size > 1) stack.removeAt(stack.lastIndex) }
    BackHandler(enabled = stack.size > 1, onBack = back)
    val pending by MainActivity.pendingRoute.collectAsState()
    LaunchedEffect(pending) {
        val r = pending ?: return@LaunchedEffect
        // Back from the conversation leads to its list: Chats for LXMF, the node for a Stump DM.
        stack.clear()
        when (r) {
            is Route.Dm -> { stack.add(Route.Home(1)); stack.add(Route.Node(r.key)) }
            else -> stack.add(Route.Home(0))
        }
        stack.add(r)
        MainActivity.pendingRoute.value = null
    }

    val state by Engine.state.collectAsState()
    Box(Modifier.fillMaxSize().background(Amber.Background)) {
        when (val s = state) {
            is Engine.State.Failed -> Empty(stringResource(R.string.engine_failed, s.error))
            is Engine.State.Running -> when (val r = stack.last()) {
                is Route.Home -> Home(r.tab, { stack[stack.lastIndex] = Route.Home(it) }, go)
                is Route.Chat -> ConversationScreen(r.peer, back)
                is Route.Node -> NodeScreen(r.key, back, go)
                is Route.Dm -> DmScreen(r.key, r.nick, back)
                Route.Pair -> PairScreen(back)
            }
            else -> Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally) {
                Text("firefly", style = MaterialTheme.typography.headlineSmall.copy(color = Amber.Ember))
                Text(stringResource(R.string.starting), style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

@Composable
fun Home(tab: Int, setTab: (Int) -> Unit, go: (Route) -> Unit) {
    val status by Engine.status.collectAsState()
    LaunchedEffect(Unit) { while (true) { Engine.refreshStatus(); kotlinx.coroutines.delay(2000) } }
    val tabs = listOf(R.string.tab_chats, R.string.tab_stumps, R.string.tab_network, R.string.tab_settings)
    Column(Modifier.fillMaxSize()) {
        TopBar("firefly", status.optString("display_name")) {
            RadioIndicator(status) { setTab(2) }
        }
        Box(Modifier.weight(1f)) {
            when (tab) {
                0 -> ChatsScreen(go)
                1 -> StumpsScreen(go)
                2 -> NetworkScreen(status, go)
                else -> SettingsScreen()
            }
        }
        Column(Modifier.background(Amber.Sidebar).navigationBarsPadding()) {
            Box(Modifier.fillMaxWidth().height(1.dp).background(Amber.Border))
            Row(Modifier.fillMaxWidth()) {
                tabs.forEachIndexed { i, res ->
                    val active = i == tab
                    Column(Modifier.weight(1f).clickable { setTab(i) }, horizontalAlignment = Alignment.CenterHorizontally) {
                        Box(Modifier.fillMaxWidth().height(2.dp).background(if (active) Amber.Ember else Amber.Sidebar))
                        Text(stringResource(res), modifier = Modifier.padding(vertical = 14.dp),
                            style = MaterialTheme.typography.labelLarge.copy(color = if (active) Amber.Ember else Amber.Muted))
                    }
                }
            }
        }
    }
}
