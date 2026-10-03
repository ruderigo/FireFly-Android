package io.github.ruderigo.firefly.radio

/**
 * One open connection to an RNode, as Reticulum's RNode driver sees it.
 *
 * Python (firefly/androidlink.py) wraps this in a pyserial-shaped port and
 * hands it to Reticulum's own RNodeInterface, so everything above the bytes
 * (detection, radio configuration and its verification, statistics) is
 * Reticulum's reference code. This contract is tested on the desktop against
 * simulated RNodes in engine-tests/.
 *
 * Rules: [read] never blocks; [close] is idempotent and ends only this
 * connection; a new [RadioLinks.open] always returns a new object.
 */
interface RadioLink {
    fun isOpen(): Boolean
    fun read(maxBytes: Int): ByteArray
    fun write(data: ByteArray): Int
    fun close()
}

/** Thread-safe byte FIFO between Android's I/O callbacks and Python's read loop. */
class ByteQueue(private val limit: Int = 256 * 1024) {
    private var buf = ByteArray(8192)
    private var head = 0
    private var size = 0

    @Synchronized fun put(data: ByteArray) {
        if (size + data.size > limit) return            // a stalled reader must not eat the heap
        if (size + data.size > buf.size) {
            val grown = ByteArray(maxOf(buf.size * 2, size + data.size))
            for (i in 0 until size) grown[i] = buf[(head + i) % buf.size]
            buf = grown; head = 0
        }
        for (b in data) { buf[(head + size) % buf.size] = b; size++ }
    }

    @Synchronized fun take(max: Int): ByteArray {
        val n = minOf(max, size)
        val out = ByteArray(n)
        for (i in 0 until n) out[i] = buf[(head + i) % buf.size]
        head = (head + n) % buf.size; size -= n
        return out
    }

    @Synchronized fun clear() { head = 0; size = 0 }
}
