package io.github.ruderigo.firefly.radio

import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.util.Log
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialDriver
import com.hoho.android.usbserial.util.SerialInputOutputManager

/** An RNode on USB OTG, through usb-serial-for-android (CP210x, CH34x, FTDI, CDC-ACM). */
class UsbRadioLink private constructor(
    private val port: UsbSerialPort,
    private val io: SerialInputOutputManager,
    private val rx: ByteQueue,
) : RadioLink {

    @Volatile private var open = true

    override fun isOpen() = open
    override fun read(maxBytes: Int): ByteArray = if (open) rx.take(maxBytes) else throw IllegalStateException("closed")

    override fun write(data: ByteArray): Int {
        check(open) { "closed" }
        return try {
            port.write(data, WRITE_TIMEOUT_MS)
            data.size
        } catch (e: Exception) {
            Log.w(TAG, "USB write failed: ${e.message}")
            close(); 0
        }
    }

    override fun close() {
        if (!open) return
        open = false
        try { io.stop() } catch (_: Exception) {}
        try { port.close() } catch (_: Exception) {}
    }

    companion object {
        private const val TAG = "FireFly/USB"
        private const val WRITE_TIMEOUT_MS = 1000
        private const val VID_ESPRESSIF = 0x303A

        fun open(usb: UsbManager, device: UsbDevice, driver: UsbSerialDriver): UsbRadioLink? {
            val connection = usb.openDevice(device) ?: return null
            val port = driver.ports.firstOrNull() ?: return null
            return try {
                port.open(connection)
                port.setParameters(115200, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
                // Line control decides whether an ESP32 resets or drops into its bootloader.
                // UART bridges (CP210x & co.): assert DTR, then RTS, like pyserial on Linux;
                // this order never passes through the EN-low reset state of the
                // auto-program circuit. ESP32-S3 native USB: DTR on, RTS off
                // (found on hardware by the prototype; RTS resets the chip).
                if (device.vendorId == VID_ESPRESSIF) {
                    port.dtr = true; port.rts = false
                } else {
                    port.dtr = true; port.rts = true
                }
                val rx = ByteQueue()
                lateinit var link: UsbRadioLink
                val io = SerialInputOutputManager(port, object : SerialInputOutputManager.Listener {
                    override fun onNewData(data: ByteArray) = rx.put(data)
                    override fun onRunError(e: Exception) {
                        Log.i(TAG, "USB link ended: ${e.message}")
                        link.close()
                    }
                })
                link = UsbRadioLink(port, io, rx)
                io.start()
                link
            } catch (e: Exception) {
                Log.w(TAG, "Could not open ${device.deviceName}: ${e.message}")
                try { port.close() } catch (_: Exception) {}
                null
            }
        }
    }
}
