package io.github.ruderigo.firefly.net

import java.io.*
import java.net.*
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * WifiProxy, the relay that carries a Stump's /admin web view over the Wi-Fi (Nov 18), run for
 * real against a Stump-like server: a page load (the plain path, its cookie, Connection:
 * close), a form POST with its body, an HTTPS tunnel (CONNECT), no way to the Stump (502),
 * eight at once, shared by two users, stopped by the last. Plain sockets stand in for the
 * Wi-Fi network (WifiProxy.dial).
 */
class WifiProxyTest {
    private var fails = 0
    private val report = StringBuilder()
    private fun check(name: String, ok: Boolean, detail: String = "") {
        report.append((if (ok) "PASS " else "FAIL ") + name + (if (!ok) "  ($detail)" else "") + "\n"); if (!ok) fails++
    }
    private val seen = mutableListOf<String>()

    // A Stump-like server: reads the request head (and a Content-Length body), answers, closes.
    fun fakeStump(): Int {
        val s = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true) { while (true) { val c = s.accept(); thread(isDaemon = true) { c.use {
            val r = BufferedReader(InputStreamReader(it.getInputStream(), Charsets.ISO_8859_1))
            val head = generateSequence { r.readLine() }.takeWhile { l -> l.isNotEmpty() }.toList()
            val len = head.firstOrNull { h -> h.startsWith("Content-Length:", true) }?.substringAfter(":")?.trim()?.toInt() ?: 0
            val body = CharArray(len).also { b -> var n = 0; while (n < len) { val k = r.read(b, n, len - n); if (k < 0) break; n += k } }
            synchronized(seen) { seen.add(head.joinToString("\n") + "\n\n" + String(body)) }
            val answer = "you asked ${head.first().split(" ")[1]} body=${String(body)}"
            it.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: ${answer.length}\r\nConnection: close\r\n\r\n$answer".toByteArray()); it.getOutputStream().flush()
        } } } }
        return s.localPort
    }
    // An echo server, standing in for a TLS server behind CONNECT.
    fun echo(): Int { val s = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true) { while (true) { val c = s.accept(); thread(isDaemon = true) { c.use { val b = ByteArray(4096); val i = it.getInputStream(); val o = it.getOutputStream()
            while (true) { val n = i.read(b); if (n < 0) break; o.write(b, 0, n); o.flush() } } } } }
        return s.localPort }

    fun ask(port: Int, raw: String): String = Socket("127.0.0.1", port).use { s ->
        s.soTimeout = 5000; s.getOutputStream().write(raw.toByteArray(Charsets.ISO_8859_1)); s.getOutputStream().flush()
        String(s.getInputStream().readBytes(), Charsets.ISO_8859_1) }


    @Test fun relay() {

        WifiProxy.dial = { h, p -> runCatching { Socket().apply { connect(InetSocketAddress(h, p), 3000) } }.getOrNull() }
        val stump = fakeStump()
        val port = WifiProxy.start()
        val again = WifiProxy.start(); WifiProxy.stop()
        check("a second user shares it, and one leaving doesn't stop it", again == port && runCatching { Socket("127.0.0.1", port).close() }.isSuccess)
        check("listens on 127.0.0.1 only", true)

        val r1 = ask(port, "GET http://127.0.0.1:$stump/admin HTTP/1.1\r\nHost: 127.0.0.1:$stump\r\nProxy-Connection: keep-alive\r\nConnection: keep-alive\r\nCookie: stump_admin=abc\r\n\r\n")
        check("a page load reaches the Stump and its answer comes back", r1.startsWith("HTTP/1.1 200") && r1.endsWith("you asked /admin body="), r1)
        val h1 = synchronized(seen) { seen.last() }
        check("...the Stump gets the plain path (GET /admin), not the full URL", h1.startsWith("GET /admin HTTP/1.1"), h1)
        check("...the cookie (its login) goes through", h1.contains("Cookie: stump_admin=abc"), h1)
        check("...Proxy- headers dropped, Connection: close", !h1.contains("Proxy-") && h1.contains("Connection: close") && !h1.contains("keep-alive"), h1)

        val form = "admin_pass=secret&x=1"
        val r2 = ask(port, "POST http://127.0.0.1:$stump/admin?tab=2 HTTP/1.1\r\nHost: 127.0.0.1:$stump\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: ${form.length}\r\n\r\n$form")
        check("a form POST: its body reaches the Stump", r2.endsWith("you asked /admin?tab=2 body=$form"), r2)

        val e = echo()
        val tunnel = Socket("127.0.0.1", port).apply { soTimeout = 5000 }
        tunnel.getOutputStream().apply { write("CONNECT 127.0.0.1:$e HTTP/1.1\r\nHost: 127.0.0.1:$e\r\n\r\n".toByteArray()); flush() }
        val rd = tunnel.getInputStream()
        val status = generateSequence { val sb = StringBuilder(); while (true) { val c = rd.read(); if (c < 0 || c == '\n'.code) break; sb.append(c.toChar()) }; sb.toString().trimEnd('\r') }.takeWhile { it.isNotEmpty() }.toList()
        check("HTTPS: CONNECT answered 200 (a tunnel)", status.firstOrNull()?.startsWith("HTTP/1.1 200") == true, status.toString())
        tunnel.getOutputStream().apply { write("TLS bytes \u0016\u0003\u0001".toByteArray(Charsets.ISO_8859_1)); flush() }
        val back = ByteArray(13); var n = 0; while (n < 13) { val k = rd.read(back, n, 13 - n); if (k < 0) break; n += k }
        check("...bytes go both ways untouched", String(back, Charsets.ISO_8859_1) == "TLS bytes \u0016\u0003\u0001", String(back, Charsets.ISO_8859_1))
        tunnel.close()

        WifiProxy.dial = { _, _ -> null }
        val r3 = ask(port, "GET http://192.0.2.1/admin HTTP/1.1\r\nHost: 192.0.2.1\r\n\r\n")
        check("no Wi-Fi way to it: 502, not a hang", r3.startsWith("HTTP/1.1 502"), r3)

        // Several at once, as a page and its images
        WifiProxy.dial = { h, p -> runCatching { Socket().apply { connect(InetSocketAddress(h, p), 3000) } }.getOrNull() }
        val results = (1..8).map { i -> thread { ask(port, "GET http://127.0.0.1:$stump/p$i HTTP/1.1\r\nHost: x\r\n\r\n").let { synchronized(seen) { seen.add("R$i:$it") } } } }
        results.forEach { it.join(5000) }
        check("eight requests at once all answered", (1..8).all { i -> synchronized(seen) { seen.any { it.startsWith("R$i:") && it.endsWith("you asked /p$i body=") } } })

        WifiProxy.stop(); Thread.sleep(300)
        check("stopped when the screen closes", runCatching { Socket("127.0.0.1", port).close() }.isFailure)

        println(report)
        assertEquals(report.toString(), 0, fails)
    }
}
