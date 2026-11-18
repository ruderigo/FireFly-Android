package io.github.ruderigo.firefly.net

import android.net.Network
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import kotlin.concurrent.thread

/**
 * A small HTTP proxy inside FireFly, on 127.0.0.1 only, that sends what goes through it over
 * the Wi-Fi network (Nov 18: a Stump's /admin in FireFly). A web view goes over the phone's
 * default network, which on a Wi-Fi with no internet (a Stump's hotspot) is mobile data: the
 * Stump can't be reached. Pointed at this proxy (androidx.webkit ProxyController, FireFly's
 * web views only), its requests take the Wi-Fi, whatever the connection; the rest of FireFly
 * and every other app (a show streaming beside it, split screen) are untouched.
 *
 *  - CONNECT host:port (HTTPS, /admin on a Stump with a certificate): a tunnel over the Wi-Fi.
 *  - GET/POST http://host/path: sent on as "GET /path" (the Stump reads the path as given),
 *    with Connection: close; the Stump closes after every answer anyway.
 * Names are looked up on the Wi-Fi too (a Stump's own DNS answers its certificate's name).
 */
object WifiProxy {
    @Volatile private var server: ServerSocket? = null
    @Volatile private var users = 0
    /** The Wi-Fi network to send over (WifiHttp's: the one joined from the app, else the system's). */
    var network: () -> Network? = { WifiHttp.instance?.wifiNetwork() }
    /** How a connection to host:port is made: over [network] (tests: plain sockets). */
    var dial: (String, Int) -> Socket? = { host, port -> openOnWifi(host, port) }

    /** Starts it (once for any number of users) -> its port on 127.0.0.1. */
    @Synchronized fun start(): Int {
        users++
        server?.let { return it.localPort }
        val s = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        server = s
        thread(name = "wifi-proxy", isDaemon = true) {
            while (!s.isClosed) {
                val c = runCatching { s.accept() }.getOrNull() ?: break
                thread(name = "wifi-proxy-conn", isDaemon = true) { serve(c) }
            }
        }
        return s.localPort
    }

    /** One user done; the last one stops it. */
    @Synchronized fun stop() {
        users = maxOf(0, users - 1)
        if (users == 0) { runCatching { server?.close() }; server = null }
    }

    private fun serve(client: Socket) {
        client.use { c ->
            runCatching {
                c.soTimeout = 60_000
                val input = c.getInputStream()
                val head = readHead(input) ?: return
                val lines = head.split("\r\n")
                val parts = lines.first().split(" ")
                if (parts.size < 3) return
                val (method, target, version) = Triple(parts[0], parts[1], parts[2])
                if (method.equals("CONNECT", true)) {
                    val host = target.substringBeforeLast(":")
                    val port = target.substringAfterLast(":").toIntOrNull() ?: 443
                    val up = dial(host, port) ?: return reply(c.getOutputStream(), "502 Bad Gateway")
                    up.use {
                        c.getOutputStream().apply { write("HTTP/1.1 200 Connection established\r\n\r\n".toByteArray()); flush() }
                        pipeBoth(c, it, input)
                    }
                    return
                }
                val uri = runCatching { URI(target) }.getOrNull() ?: return reply(c.getOutputStream(), "400 Bad Request")
                val host = uri.host ?: return reply(c.getOutputStream(), "400 Bad Request")
                val port = if (uri.port > 0) uri.port else 80
                val path = (uri.rawPath?.ifEmpty { "/" } ?: "/") + (uri.rawQuery?.let { "?$it" } ?: "")
                val up = dial(host, port) ?: return reply(c.getOutputStream(), "502 Bad Gateway")
                up.use {
                    val out = StringBuilder("$method $path $version\r\n")
                    lines.drop(1).filter { it.isNotEmpty() }
                        .filterNot { it.startsWith("Proxy-", true) || it.startsWith("Connection:", true) || it.startsWith("Keep-Alive:", true) }
                        .forEach { out.append(it).append("\r\n") }
                    out.append("Connection: close\r\n\r\n")
                    it.getOutputStream().apply { write(out.toString().toByteArray(Charsets.ISO_8859_1)); flush() }
                    pipeBoth(c, it, input)          // the body (a form's POST) up, the answer down
                }
            }
        }
    }

    /** Request line and headers, up to the blank line (64 KB at most) -> null if cut short. */
    private fun readHead(input: InputStream): String? {
        val buf = ByteArrayOutputStream()
        var matched = 0
        val end = byteArrayOf(13, 10, 13, 10)
        while (buf.size() < 65_536) {
            val b = input.read()
            if (b < 0) return null
            buf.write(b)
            matched = if (b.toByte() == end[matched]) matched + 1 else if (b == 13) 1 else 0
            if (matched == 4) return buf.toString(Charsets.ISO_8859_1.name()).removeSuffix("\r\n\r\n")
        }
        return null
    }

    /** A connection to host:port over the Wi-Fi (its name looked up there too) -> null if none. */
    private fun openOnWifi(host: String, port: Int): Socket? {
        val n = network() ?: return null
        return runCatching {
            val address = n.getByName(host.removePrefix("[").removeSuffix("]"))
            n.socketFactory.createSocket().apply {
                connect(InetSocketAddress(address, port), 15_000)
                soTimeout = 60_000
            }
        }.getOrNull()
    }

    private fun pipeBoth(client: Socket, up: Socket, clientIn: InputStream) {
        val upward = thread(name = "wifi-proxy-up", isDaemon = true) {
            runCatching { copy(clientIn, up.getOutputStream()) }
            runCatching { up.shutdownOutput() }
        }
        runCatching { copy(up.getInputStream(), client.getOutputStream()) }
        runCatching { client.shutdownOutput() }
        upward.join(2_000)
    }

    private fun copy(from: InputStream, to: OutputStream) {
        val b = ByteArray(16_384)
        while (true) {
            val n = from.read(b)
            if (n < 0) break
            to.write(b, 0, n)
            to.flush()
        }
    }

    private fun reply(out: OutputStream, status: String) {
        runCatching { out.write("HTTP/1.1 $status\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray()); out.flush() }
    }
}
