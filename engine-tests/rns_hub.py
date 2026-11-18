"""A Reticulum transport hub with a TCPServerInterface, for tests: python3 rns_hub.py <configdir> <port>"""
import os, sys, time
import RNS
d, port = sys.argv[1], int(sys.argv[2])
os.makedirs(d, exist_ok=True)
open(os.path.join(d, "config"), "w").write(f"""[reticulum]
  enable_transport = Yes
  share_instance = No
[logging]
  loglevel = 2
[interfaces]
  [[Hub]]
    type = TCPServerInterface
    enabled = yes
    listen_ip = 127.0.0.1
    listen_port = {port}
""")
RNS.Reticulum(configdir=d)
if os.environ.get('FIREFLY_STAMP_VALUE') and hasattr(RNS.Transport, 'DESTINATION_VALUE'):
    RNS.Transport.DESTINATION_VALUE = float(os.environ['FIREFLY_STAMP_VALUE'])   # a cheap announce stamp (tests, Reticulum 1.6.1)
print("hub up", flush=True)
while True:
    time.sleep(1)
