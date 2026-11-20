# Transfer por RED (nc sobre WiFi): rapido, para bootstrap cuando Tuya/WiFi esta vivo.
# Shell/control por USB; datos por la IP real. Verifica md5.
import sys, os, socket, time, hashlib
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pathlib import Path
from tpp06_tuya_cutter import TuyaCutter
real_ip, serial, adb = sys.argv[1], sys.argv[2], sys.argv[3]
pairs = sys.argv[4:]  # local:remote local:remote ...
c = TuyaCutter(real_ip, Path("~/Documents/tpp06-backups"), adb=adb)
c.discover(); c.session.serial = serial; sess = c.session
PORT = 9001
for pair in pairs:
    local, remote = pair.split("::")
    data = open(local, "rb").read()
    lmd5 = hashlib.md5(data).hexdigest()
    d = os.path.dirname(remote)
    sess.run_root(f"kill $(pidof nc) 2>/dev/null; mkdir -p {d}; rm -f {remote}; "
                  f"nohup nc -l -p {PORT} > {remote} 2>/dev/null </dev/null & sleep 1", check=False)
    s = socket.create_connection((real_ip, PORT), timeout=10)
    s.sendall(data); s.shutdown(socket.SHUT_WR); time.sleep(1); s.close()
    time.sleep(2)
    res = sess.run_root(f"echo SZ=$(wc -c <{remote}); echo MD5=$(md5sum {remote}|cut -d' ' -f1)", check=False)
    ok = lmd5 in res.stdout
    print(f"{os.path.basename(remote)}: LOCAL {len(data)} {lmd5} | REMOTE {res.stdout.strip().replace(chr(10),' ')} | {'OK' if ok else 'FAIL'}")
