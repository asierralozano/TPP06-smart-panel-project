import sys, os, re
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pathlib import Path
from tpp06_tuya_cutter import TuyaCutter
host, local, remote = sys.argv[1], sys.argv[2], sys.argv[3]
adb = sys.argv[4] if len(sys.argv) > 4 else "adb"
serial = sys.argv[5] if len(sys.argv) > 5 else None
content = Path(local).read_text()
delim = "TPP06_EOF_9f3a2b"
assert delim not in content, "delimiter collision"
if not content.endswith("\n"):
    content += "\n"
cmd = f"cat > {remote} <<'{delim}'\n{content}{delim}\nchmod +x {remote}"
c = TuyaCutter(host, Path("~/Documents/tpp06-backups"), adb=adb)
sess = c.discover()
if serial:
    sess.serial = serial
else:
    sess.connect()
sess.run_root(cmd, timeout=60, check=False)
res2 = sess.run_root(f"wc -c {remote}; md5sum {remote} 2>/dev/null; sh -n {remote} && echo SYNTAX_OK || echo SYNTAX_FAIL", timeout=30, check=False)
out = re.sub(r'(?im)^\s*Password:.*$', 'Password: ****', res2.stdout)
print("VERIFY:\n" + out)
