package io.github.ruderigo.firefly.radio

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothProfile
import android.content.Context
import android.os.Build
import android.util.Log
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * An RNode over Bluetooth LE: the Nordic UART Service, as RNode firmware
 * exposes it (the same service Reticulum's desktop driver uses via bleak).
 *
 * What makes BLE reliable here, compared with a prototype that fired writes
 * on a timer:
 *  - Android allows ONE outstanding GATT operation. Every write waits for its
 *    onCharacteristicWrite before the next, on a dedicated writer thread.
 *  - Chunks are sized from the negotiated MTU (MTU - 3), requested up to 517.
 *  - The RNode only talks to bonded phones, so an unbonded device fails fast
 *    with a clear reason instead of timing out silently.
 *  - Notifications are enabled via the CCCD, and the link reports ready only
 *    after that write is acknowledged, so Reticulum's first detect isn't lost.
 *  - High connection priority while attached: LoRa is slow, but the RNode
 *    protocol is chatty at configuration time.
 */
@SuppressLint("MissingPermission")   // checked by RadioLinks before any BLE use
class BleRadioLink private constructor(private val address: String) : RadioLink {

    private val rx = ByteQueue()
    private val tx = LinkedBlockingQueue<ByteArray>()
    @Volatile private var gatt: BluetoothGatt? = null
    @Volatile private var rxChar: BluetoothGattCharacteristic? = null
    @Volatile private var open = false
    @Volatile private var closed = false
    @Volatile private var mtu = 23
    @Volatile var failure: String? = null; private set
    private val ready = CountDownLatch(1)
    private var writeDone = CountDownLatch(1)
    private var writer: Thread? = null

    override fun isOpen() = open && !closed
    override fun read(maxBytes: Int): ByteArray = if (isOpen()) rx.take(maxBytes) else throw IllegalStateException("closed")

    override fun write(data: ByteArray): Int {
        check(isOpen()) { "closed" }
        tx.put(data)
        return data.size
    }

    override fun close() {
        if (closed) return
        closed = true; open = false
        writer?.interrupt()
        ready.countDown()
        gatt?.let { g -> try { g.disconnect() } catch (_: Exception) {}; try { g.close() } catch (_: Exception) {} }
        gatt = null
    }

    private fun fail(reason: String) {
        failure = reason
        Log.w(TAG, "$address: $reason")
        close()
    }

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                if (!g.requestMtu(517)) g.discoverServices()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                fail(if (open) "disconnected" else "could not connect (status $status); is it on and in range?")
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, newMtu: Int, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) mtu = newMtu
            g.discoverServices()
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val service = g.getService(NUS_SERVICE)
            val rxc = service?.getCharacteristic(NUS_RX)
            val txc = service?.getCharacteristic(NUS_TX)
            if (rxc == null || txc == null) return fail("no RNode UART service: is Bluetooth enabled on the RNode?")
            rxChar = rxc
            g.setCharacteristicNotification(txc, true)
            val cccd = txc.getDescriptor(CCCD) ?: return fail("RNode notifications unavailable")
            val value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            val started = if (Build.VERSION.SDK_INT >= 33) {
                g.writeDescriptor(cccd, value) == BluetoothGatt.GATT_SUCCESS
            } else {
                @Suppress("DEPRECATION") run { cccd.value = value; g.writeDescriptor(cccd) }
            }
            if (!started) fail("could not enable notifications")
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            if (d.uuid != CCCD) return
            if (status != BluetoothGatt.GATT_SUCCESS) {
                return fail("notifications refused (status $status): pair the RNode again")
            }
            g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
            open = true
            startWriter()
            ready.countDown()
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) {
            if (c.uuid == NUS_TX) rx.put(value)
        }

        @Deprecated("API < 33")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT < 33 && c.uuid == NUS_TX) c.value?.let { rx.put(it.copyOf()) }
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            writeDone.countDown()
        }
    }

    private fun startWriter() {
        writer = Thread({
            try {
                while (!closed) {
                    val frame = tx.take()
                    var off = 0
                    val chunk = maxOf(20, mtu - 3)
                    while (off < frame.size && !closed) {
                        val end = minOf(frame.size, off + chunk)
                        if (!writeChunk(frame.copyOfRange(off, end))) { fail("write failed"); return@Thread }
                        off = end
                    }
                }
            } catch (_: InterruptedException) { }
        }, "ble-rnode-writer").apply { isDaemon = true; start() }
    }

    private fun writeChunk(bytes: ByteArray): Boolean {
        val g = gatt ?: return false
        val c = rxChar ?: return false
        repeat(20) {                                   // the stack can be briefly busy
            writeDone = CountDownLatch(1)
            val type = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
            val ok = if (Build.VERSION.SDK_INT >= 33) {
                g.writeCharacteristic(c, bytes, type) == BluetoothGatt.GATT_SUCCESS
            } else {
                @Suppress("DEPRECATION") run { c.writeType = type; c.value = bytes; g.writeCharacteristic(c) }
            }
            if (ok) return writeDone.await(2, TimeUnit.SECONDS)
            Thread.sleep(10)
        }
        return false
    }

    companion object {
        private const val TAG = "FireFly/BLE"
        val NUS_SERVICE: UUID = UUID.fromString("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
        val NUS_RX: UUID = UUID.fromString("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")   // phone -> RNode
        val NUS_TX: UUID = UUID.fromString("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")   // RNode -> phone
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

        /** Blocks up to [timeoutMs]; returns an open link, or a closed one whose [failure] says why. */
        fun open(context: Context, adapter: BluetoothAdapter, address: String, timeoutMs: Long): BleRadioLink {
            val link = BleRadioLink(address)
            val device: BluetoothDevice = try { adapter.getRemoteDevice(address) } catch (e: Exception) {
                link.fail("invalid address"); return link
            }
            if (device.bondState != BluetoothDevice.BOND_BONDED) {
                link.fail("not paired with this phone: pair it from Network > Bluetooth RNode"); return link
            }
            link.gatt = device.connectGatt(context, false, link.callback, BluetoothDevice.TRANSPORT_LE)
            if (!link.ready.await(timeoutMs, TimeUnit.MILLISECONDS) && !link.open) {
                link.fail(link.failure ?: "timed out connecting")
            }
            return link
        }
    }
}
