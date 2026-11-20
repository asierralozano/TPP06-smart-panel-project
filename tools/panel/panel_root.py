import sys, os
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pathlib import Path
from tpp06_tuya_cutter import TuyaCutter
host = sys.argv[1]
cmd = sys.argv[2]
adb = sys.argv[3] if len(sys.argv) > 3 else "adb"
c = TuyaCutter(host, Path("~/Documents/tpp06-backups"), adb=adb)
sess = c.connected_session()
res = sess.run_root(cmd, timeout=120, check=False)
print("RC_REMOTE=%d" % res.returncode)
print(res.stdout)
