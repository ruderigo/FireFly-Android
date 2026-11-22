"""Run on the computer an emulator is attached to (adb): seeds a fresh FireFly with the demo
(seed.py), opens a few screens and saves them as PNGs.

    python3 tools/screenshots/capture.py <apk> <out dir>

Taps go by the text on screen (uiautomator), so they follow the app's layout. Each screen
also leaves its view tree (<name>.xml) next to the PNG, to see what was there.
"""
import json, os, re, subprocess, sys, time, xml.etree.ElementTree as ET

PKG = "io.github.ruderigo.firefly"
HERE = os.path.dirname(os.path.abspath(__file__))
apk, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)


def adb(*args, check=True, binary=False):
    r = subprocess.run(["adb", *args], capture_output=True, check=False)
    if check and r.returncode:
        print("adb", *args, "->", r.returncode, r.stderr.decode(errors="replace"))
    return r.stdout if binary else r.stdout.decode(errors="replace")


def sh(cmd):
    return adb("shell", cmd)


def tree():
    sh("uiautomator dump /sdcard/ui.xml >/dev/null")
    xml = adb("shell", "cat /sdcard/ui.xml")
    try:
        return ET.fromstring(xml[xml.index("<"):]), xml
    except Exception:
        return None, xml


def find(text, exact=False):
    root, _ = tree()
    if root is None:
        return None
    for n in root.iter("node"):
        t = (n.get("text") or "") + "|" + (n.get("content-desc") or "")
        if (exact and text in t.split("|")) or (not exact and text in t):
            x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds")))
            return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def tap(text, exact=False, wait=20):
    end = time.time() + wait
    while time.time() < end:
        p = find(text, exact)
        if p:
            sh(f"input tap {p[0]} {p[1]}")
            time.sleep(1.5)
            return True
        time.sleep(1)
    print("not found on screen:", text)
    return False


def shot(name, settle=2.5):
    time.sleep(settle)
    png = adb("exec-out", "screencap", "-p", binary=True)
    open(os.path.join(out, name + ".png"), "wb").write(png)
    open(os.path.join(out, name + ".xml"), "w").write(tree()[1])
    print("saved", name, len(png), "bytes")


def start(*extras):
    sh(f"am start -W -n {PKG}/.ui.MainActivity " + " ".join(extras))


# A clean status bar: 9:41, full Wi-Fi and battery, no notifications.
sh("settings put global sysui_demo_allowed 1")
for c in ("enter", "clock -e hhmm 0941", "battery -e level 100 -e plugged false",
          "network -e wifi show -e level 4 -e mobile hide", "notifications -e visible false"):
    cmd, *rest = c.split(" ", 1)
    sh(f"am broadcast -a com.android.systemui.demo -e command {cmd} {rest[0] if rest else ''}")

# Install with every permission granted (no dialogs), seed before the first start.
adb("install", "-r", "-g", apk)
demo = json.loads(subprocess.check_output([sys.executable, os.path.join(HERE, "seed.py"),
                                           os.path.join(out, "_seed")]).decode().strip().splitlines()[-1])
for f in ("firefly.db", "settings.json"):
    adb("push", os.path.join(out, "_seed", f), f"/data/local/tmp/{f}")
sh(f"run-as {PKG} sh -c 'mkdir -p files/firefly && cp /data/local/tmp/firefly.db /data/local/tmp/settings.json files/firefly/'")
print(sh(f"run-as {PKG} ls -la files/firefly"))

start()
time.sleep(20)                       # Python and Reticulum start
shot("01-chats")

start(f"--es open chat --es peer {demo['sam']}")
shot("02-chat", settle=4)

sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
tap("Stumps", exact=True)
shot("03-stumps")

tap("Cedar Grove")
shot("04-room", settle=8)

start(f"--es open dm --es node {demo['stump_key']} --es nick priya")
shot("05-stump-dm", settle=4)

sh("input keyevent KEYCODE_BACK"); time.sleep(1); sh("input keyevent KEYCODE_BACK"); time.sleep(1.5)
tap("Network", exact=True)
shot("06-network")

subprocess.run(f"adb logcat -d > {os.path.join(out, 'logcat.txt')}", shell=True)
