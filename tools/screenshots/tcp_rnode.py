"""A stand-in Wi-Fi RNode for the screenshots: the engine tests' simulated RNode
(engine-tests/fake_rnode.py) behind TCP port 7633, where Reticulum's RNode driver
looks for one (tcp://<host>). The emulator reaches it at 10.0.2.2. Nothing goes on air.

    python3 tools/screenshots/tcp_rnode.py &
"""
import os, socket, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine-tests"))
from fake_rnode import FakeRNodeLink     # noqa: E402


def serve(conn):
    link = FakeRNodeLink(style="usb")
    conn.settimeout(0.05)
    try:
        while True:
            try:
                data = conn.recv(4096)
                if not data:
                    return
                link.write(data)
            except socket.timeout:
                pass
            out = link.read(4096)
            if out:
                conn.sendall(out)
    except OSError:
        pass
    finally:
        conn.close()


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 7633))
srv.listen(4)
print("stand-in RNode on 127.0.0.1:7633", flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=serve, args=(c,), daemon=True).start()
