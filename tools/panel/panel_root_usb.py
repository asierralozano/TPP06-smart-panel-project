import sys, os, re
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pathlib import Path
from tpp06_tuya_cutter import TuyaCutter
host, usb_serial, cmd = sys.argv[1], sys.argv[2], sys.argv[3]
adb = sys.argv[4] if len(sys.argv) > 4 else "adb"
c = TuyaCutter(host, Path("~/Documents/tpp06-backups"), adb=adb)
sess = c.discover()
sess.serial = usb_serial
res = sess.run_root(cmd, timeout=60, check=False)
out = re.sub(r'(?im)^\s*Password:.*$', 'Password: ****', res.stdout)
print("RC_REMOTE=%d" % res.returncode)
print(out)
