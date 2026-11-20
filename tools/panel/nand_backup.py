import sys, os
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pathlib import Path
from tpp06_tuya_cutter import TuyaCutter
host, adb = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "adb")
outdir = Path(os.path.expanduser("~/Documents/tpp06-backups/nand-137")); outdir.mkdir(parents=True, exist_ok=True)
c = TuyaCutter(host, Path("~/Documents/tpp06-backups"), adb=adb)
sess = c.connected_session()
# partition -> mtd device  (per-device unique only)
parts = {"factory": "mtd13", "env": "mtd2"}
import hashlib
for name, dev in parts.items():
    remote = f"/tmp/tpp06-nand-{name}.bin"
    sess.run_root(f"dd if=/dev/{dev} of={remote} bs=65536 2>/dev/null; sync", timeout=120)
    local = outdir / f"{name}-{dev}.bin"
    sess.download(remote, local)
    sess.run_root(f"rm -f {remote}")
    data = local.read_bytes()
    print(f"OK {name} ({dev}): {len(data)} bytes  sha256={hashlib.sha256(data).hexdigest()}")
print("DONE")
