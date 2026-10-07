package io.github.ruderigo.firefly.audio

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.engine.Engine
import io.github.ruderigo.firefly.ui.Amber
import io.github.ruderigo.firefly.ui.Large
import io.github.ruderigo.firefly.ui.Medium
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** What the composer shows while a voice note is being recorded. */
class RecordingState {
    var recorder by mutableStateOf<VoiceRecorder?>(null)
    var elapsedMs by mutableLongStateOf(0L)
    var cancelling by mutableStateOf(false)
    var hint by mutableStateOf<Int?>(null)
}

/**
 * Hold to record, release to send, slide left to cancel. Recording stops by
 * itself at 15 s; releasing then sends those 15 s.
 */
@Composable
fun HoldToTalk(state: RecordingState, onRecorded: (Recorded) -> Unit) {
    val ctx = LocalContext.current
    val send by rememberUpdatedState(onRecorded)
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        state.hint = if (granted) R.string.voice_hold_hint else R.string.voice_mic_denied
    }
    LaunchedEffect(state.recorder) {
        val r = state.recorder ?: return@LaunchedEffect
        while (state.recorder === r) { state.elapsedMs = minOf(r.elapsedMs, VoiceRecorder.MAX_MS); delay(100) }
    }
    val active = state.recorder != null
    Box(Modifier.clip(Medium).background(if (active) Amber.EmberBright else Amber.Ember)
        .pointerInput(Unit) {
            awaitEachGesture {
                val down = awaitFirstDown()
                if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    permission.launch(Manifest.permission.RECORD_AUDIO)
                    return@awaitEachGesture
                }
                val r = VoiceRecorder()
                if (!r.start()) { state.hint = R.string.voice_mic_unavailable; return@awaitEachGesture }
                state.hint = null; state.cancelling = false; state.elapsedMs = 0; state.recorder = r
                val slide = 100.dp.toPx()
                while (true) {
                    val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: break
                    state.cancelling = down.position.x - change.position.x > slide
                    if (!change.pressed) break
                }
                state.recorder = null
                if (state.cancelling) { r.cancel(); state.cancelling = false; return@awaitEachGesture }
                val rec = r.stop()
                if (rec == null) state.hint = R.string.voice_hold_hint else send(rec)
            }
        }
        .padding(horizontal = 14.dp, vertical = 10.dp), contentAlignment = Alignment.Center) {
        Text(stringResource(if (active) R.string.voice_release else R.string.voice_hold),
            style = MaterialTheme.typography.labelLarge.copy(color = Amber.Background))
    }
}

/** The line above the composer while recording (or a short hint after). */
@Composable
fun RecordingBar(state: RecordingState) {
    val hint: Int? = state.hint
    val recording = state.recorder != null
    val text = when {
        recording && state.cancelling -> stringResource(R.string.voice_cancel)
        recording -> stringResource(R.string.voice_recording, "%.1f".format(state.elapsedMs / 1000.0))
        hint != null -> stringResource(hint)
        else -> return
    }
    Text(text, modifier = Modifier.padding(start = 12.dp, top = 6.dp),
        style = MaterialTheme.typography.labelMedium.copy(
            color = if (state.cancelling) Amber.Error else if (recording) Amber.EmberBright else Amber.Muted))
}

/** A voice note in a conversation: tap to play, tap again to stop. */
@Composable
fun VoiceBubble(m: JSONObject, mine: Boolean, fetch: String = "message_audio", dm: Boolean = false) {
    val id = m.optLong("id")
    val playId = if (dm) -id else id            // Stump DMs and chat messages number separately
    val accent = if (dm) Amber.DmBody else Amber.Ember
    val playing by VoicePlayer.playing.collectAsState()
    val me = playing == playId
    val scope = rememberCoroutineScope()
    val playable = m.optBoolean("audio_playable", false)
    val ms = m.optLong("audio_ms", 0)
    val tenths = (ms + 50) / 100                       // rounded half up, as the ♪ label on a Stump
    Row(Modifier.widthIn(min = 170.dp, max = 320.dp).clip(Large).background(if (mine) Amber.Panel else Amber.Sidebar)
        .border(1.dp, if (mine || dm) accent.copy(alpha = 0.45f) else Amber.Border, Large)
        .clickable(enabled = playable) {
            if (me) VoicePlayer.stop()
            else scope.launch(Dispatchers.Default) {
                val bits = Engine.callBytes(fetch, id) ?: return@launch
                val (pcm, rate) = VoiceEncoder.decode(m.optInt("audio_mode"), bits) ?: return@launch
                VoicePlayer.play(playId, pcm, rate)
            }
        }.padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(if (me) "■" else "▶", style = MaterialTheme.typography.titleLarge.copy(
            color = if (playable) accent else Amber.Dim))
        Spacer(Modifier.width(12.dp))
        Column {
            Text(if (playable) stringResource(R.string.voice_note) else stringResource(R.string.voice_unsupported),
                style = MaterialTheme.typography.bodyMedium)
            if (ms > 0) Text("${tenths / 10}.${tenths % 10} s", style = MaterialTheme.typography.labelSmall)
        }
    }
}
