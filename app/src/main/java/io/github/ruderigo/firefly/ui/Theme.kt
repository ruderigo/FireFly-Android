package io.github.ruderigo.firefly.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * The Stump ecosystem's Amber theme, verbatim from CLIENT_QUICKSTART.md, so
 * FireFly reads as part of the same family as the node's web console.
 * One accent (ember) means "interactive"; backgrounds get darker as density
 * rises (page -> panel -> sidebar -> row); purple is DMs only, red is errors only.
 */
object Amber {
    val Background = Color(0xFF1B1512)
    val Panel = Color(0xFF2A2119)
    val Ember = Color(0xFFD97A3A)
    val EmberBright = Color(0xFFF0A050)
    val Text = Color(0xFFECDFC8)
    val Muted = Color(0xFF9C8D76)
    val Border = Color(0xFF493C2E)
    val Sidebar = Color(0xFF221B15)
    val Row = Color(0xFF2F271E)
    val Dim = Color(0xFF7D715F)
    val Action = Color(0xFFC8B48F)
    val DmBody = Color(0xFFC8A2C8)
    val DmNick = Color(0xFFD8B4D8)
    val Error = Color(0xFFE07A5A)
}

/** System fonts only: it has to render with no internet behind it. */
val Serif = FontFamily.Serif
val Mono = FontFamily.Monospace

val Small = RoundedCornerShape(4.dp)    // small controls
val Medium = RoundedCornerShape(6.dp)   // buttons, tiles
val Large = RoundedCornerShape(8.dp)    // panels, cards

@Composable
fun FireFlyTheme(content: @Composable () -> Unit) {
    val colors = darkColorScheme(
        primary = Amber.Ember, onPrimary = Amber.Background,
        secondary = Amber.EmberBright, onSecondary = Amber.Background,
        background = Amber.Background, onBackground = Amber.Text,
        surface = Amber.Panel, onSurface = Amber.Text,
        surfaceVariant = Amber.Sidebar, onSurfaceVariant = Amber.Muted,
        surfaceContainer = Amber.Panel, surfaceContainerHigh = Amber.Panel,
        surfaceContainerHighest = Amber.Row,
        outline = Amber.Border, outlineVariant = Amber.Border,
        error = Amber.Error, onError = Amber.Background,
    )
    val body = TextStyle(fontFamily = Serif, fontSize = 17.sp, lineHeight = 25.sp, color = Amber.Text)
    val mono = TextStyle(fontFamily = Mono, color = Amber.Text)
    val type = Typography(
        headlineSmall = mono.copy(fontSize = 22.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.5.sp),
        titleLarge = mono.copy(fontSize = 19.sp, fontWeight = FontWeight.Bold),
        titleMedium = mono.copy(fontSize = 16.sp, fontWeight = FontWeight.Bold),
        titleSmall = mono.copy(fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Amber.Muted),
        bodyLarge = body,
        bodyMedium = body.copy(fontSize = 15.sp, lineHeight = 22.sp),
        bodySmall = body.copy(fontSize = 13.sp, lineHeight = 18.sp, color = Amber.Muted),
        labelLarge = mono.copy(fontSize = 14.sp, fontWeight = FontWeight.Bold),
        labelMedium = mono.copy(fontSize = 12.sp, color = Amber.Muted),
        labelSmall = mono.copy(fontSize = 11.sp, color = Amber.Dim),
    )
    MaterialTheme(colorScheme = colors, typography = type,
        shapes = Shapes(extraSmall = Small, small = Small, medium = Medium, large = Large, extraLarge = Large),
        content = content)
}
