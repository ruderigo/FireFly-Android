"""FireFly for Android: the messaging engine.

This package is the same engine as FireFly on RK3326 handhelds
(github.com/ruderigo/FireFly_RK3326_R36MAX_R36S), adapted for Android:
official `rns` and `lxmf`, unmodified, with the radio reached through
Android's USB and Bluetooth LE stacks (see androidlink.py).

Nothing in here imports Android or Chaquopy. The Kotlin side hands in three
plain objects (radio links, a Wi-Fi HTTP transport, an event listener), so the
whole engine also runs on a desktop Python for testing (engine-tests/).
"""
__version__ = "0.1.2"
