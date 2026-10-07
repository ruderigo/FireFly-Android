package io.github.ruderigo.firefly.radio

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import androidx.core.content.ContextCompat
import com.hoho.android.usbserial.driver.CdcAcmSerialDriver
import com.hoho.android.usbserial.driver.ProbeTable
import com.hoho.android.usbserial.driver.UsbSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialProber
import org.json.JSONArray
import org.json.JSONObject

/**
 * The registry Python's RadioManager asks for radios:
 *   usbPortsJson()  -> USB serial devices we may open
 *   open(port, ms)  -> a RadioLink for "usb:<device>" or "ble:<address>", or null
 * Called from Python threads; everything here is thread-safe.
 */
class RadioLinks(private val context: Context, private val onDevicesChanged: () -> Unit) {

    private val usb = context.getSystemService(UsbManager::class.java)
    private val bt = context.getSystemService(BluetoothManager::class.java)?.adapter
    @Volatile var lastBleFailure: String? = null; private set

    private val prober: UsbSerialProber by lazy {
        val table = UsbSerialProber.getDefaultProbeTable()
        table.addProduct(0x303A, 0x1001, CdcAcmSerialDriver::class.java)   // ESP32-S3 native USB
        table.addProduct(0x303A, 0x4001, CdcAcmSerialDriver::class.java)
        UsbSerialProber(table)
    }

    private fun drivers(): List<UsbSerialDriver> = try { prober.findAllDrivers(usb) } catch (_: Exception) { emptyList() }

    fun usbPortsJson(): String {
        val out = JSONArray()
        for (d in drivers()) if (usb.hasPermission(d.device)) {
            out.put(JSONObject().put("id", "usb:" + d.device.deviceName).put("label", label(d.device)))
        }
        return out.toString()
    }

    /** USB devices that look like serial adapters but need the user's permission first. */
    fun usbNeedingPermission(): List<UsbDevice> = drivers().map { it.device }.filter { !usb.hasPermission(it) }

    fun requestUsbPermission(device: UsbDevice) {
        val flags = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        val pi = PendingIntent.getBroadcast(context, 0, Intent(ACTION_USB_PERMISSION).setPackage(context.packageName), flags)
        usb.requestPermission(device, pi)
    }

    fun open(port: String, timeoutMs: Int): RadioLink? = when {
        port.startsWith("usb:") -> {
            val name = port.removePrefix("usb:")
            drivers().firstOrNull { it.device.deviceName == name && usb.hasPermission(it.device) }
                ?.let { UsbRadioLink.open(usb, it.device, it) }
        }
        port.startsWith("ble:") -> openBle(port.removePrefix("ble:"), timeoutMs)
        else -> null
    }

    @SuppressLint("MissingPermission")
    private fun openBle(address: String, timeoutMs: Int): RadioLink? {
        val adapter = bt ?: return null.also { lastBleFailure = "no Bluetooth on this device" }
        if (!BlePermissions.granted(context)) return null.also { lastBleFailure = "Bluetooth permission not granted" }
        if (!adapter.isEnabled) return null.also { lastBleFailure = "Bluetooth is off" }
        val link = BleRadioLink.open(context, adapter, address, timeoutMs.toLong())
        lastBleFailure = link.failure
        return if (link.isOpen()) link else null
    }

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            if (i.action == UsbManager.ACTION_USB_DEVICE_ATTACHED) {
                usbNeedingPermission().forEach { requestUsbPermission(it) }
            }
            onDevicesChanged()
        }
    }

    fun register() {
        val f = IntentFilter().apply {
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
            addAction(ACTION_USB_PERMISSION)
        }
        ContextCompat.registerReceiver(context, receiver, f, ContextCompat.RECEIVER_NOT_EXPORTED)
        usbNeedingPermission().forEach { requestUsbPermission(it) }
    }

    fun unregister() = try { context.unregisterReceiver(receiver) } catch (_: Exception) {}

    companion object {
        const val ACTION_USB_PERMISSION = "io.github.ruderigo.firefly.USB_PERMISSION"
        fun label(d: UsbDevice) = listOfNotNull(d.manufacturerName, d.productName).joinToString(" ").ifBlank {
            "USB %04x:%04x".format(d.vendorId, d.productId)
        }
    }
}

object BlePermissions {
    val required: Array<String> = if (Build.VERSION.SDK_INT >= 31)
        arrayOf(android.Manifest.permission.BLUETOOTH_SCAN, android.Manifest.permission.BLUETOOTH_CONNECT)
    else arrayOf(android.Manifest.permission.ACCESS_FINE_LOCATION)

    fun granted(c: Context) = required.all {
        ContextCompat.checkSelfPermission(c, it) == android.content.pm.PackageManager.PERMISSION_GRANTED
    }
}
