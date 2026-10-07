package io.github.ruderigo.firefly.radio

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.ParcelUuid
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

data class FoundRNode(val address: String, val name: String, val rssi: Int, val bonded: Boolean)

/**
 * Finds RNodes advertising the UART service and pairs with them.
 *
 * RNode firmware only accepts bonded phones. Pairing: put the RNode in
 * pairing mode (button, or `rnodeconf <port> -p`), then enter the PIN it
 * shows on its display in Android's pairing dialog.
 */
@SuppressLint("MissingPermission")
class BlePairing(private val context: Context) {
    private val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
    private val _found = MutableStateFlow<List<FoundRNode>>(emptyList())
    val found: StateFlow<List<FoundRNode>> = _found
    private val _state = MutableStateFlow("idle")         // idle scanning pairing:<addr> paired:<addr> failed:<reason>
    val state: StateFlow<String> = _state

    private val scanCb = object : ScanCallback() {
        override fun onScanResult(type: Int, r: ScanResult) {
            val d = r.device
            val name = r.scanRecord?.deviceName ?: d.name ?: "RNode"
            val item = FoundRNode(d.address, name, r.rssi, d.bondState == BluetoothDevice.BOND_BONDED)
            _found.value = (_found.value.filter { it.address != item.address } + item).sortedByDescending { it.rssi }
        }
        override fun onScanFailed(errorCode: Int) { _state.value = "failed:scan error $errorCode" }
    }

    fun available() = adapter != null && adapter.isEnabled && BlePermissions.granted(context)

    fun startScan() {
        val scanner = adapter?.bluetoothLeScanner ?: return
        _found.value = emptyList()
        _state.value = "scanning"
        val filter = ScanFilter.Builder().setServiceUuid(ParcelUuid(BleRadioLink.NUS_SERVICE)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        scanner.startScan(listOf(filter), settings, scanCb)
    }

    fun stopScan() {
        try { adapter?.bluetoothLeScanner?.stopScan(scanCb) } catch (_: Exception) {}
        if (_state.value == "scanning") _state.value = "idle"
    }

    /** Starts bonding; [onPaired] runs once Android reports the bond. */
    fun pair(address: String, onPaired: (String, String) -> Unit) {
        stopScan()
        val device = adapter?.getRemoteDevice(address) ?: return
        val name = _found.value.firstOrNull { it.address == address }?.name ?: device.name ?: "RNode"
        if (device.bondState == BluetoothDevice.BOND_BONDED) {
            _state.value = "paired:$address"; onPaired(address, name); return
        }
        _state.value = "pairing:$address"
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                val d: BluetoothDevice? = if (android.os.Build.VERSION.SDK_INT >= 33)
                    i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                else @Suppress("DEPRECATION") i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                if (d?.address != address) return
                when (i.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, -1)) {
                    BluetoothDevice.BOND_BONDED -> {
                        _state.value = "paired:$address"; onPaired(address, name); context.unregisterReceiver(this)
                    }
                    BluetoothDevice.BOND_NONE -> {
                        _state.value = "failed:pairing was refused or the PIN was wrong"; context.unregisterReceiver(this)
                    }
                }
            }
        }
        ContextCompat.registerReceiver(context, receiver, IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED),
            ContextCompat.RECEIVER_EXPORTED)
        if (!device.createBond()) {
            _state.value = "failed:Android refused to start pairing"
            try { context.unregisterReceiver(receiver) } catch (_: Exception) {}
        }
    }
}
