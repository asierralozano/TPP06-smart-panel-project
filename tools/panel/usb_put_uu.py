# Uploader 100% USB: uuencode -m (base64) -> heredoc por el cable -> uudecode en
# el panel. Troceado + verificado por md5 por trozo y total. Sin red, sin adb push.
import sys, os, subprocess, hashlib, tempfile
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pathlib import Path
from tpp06_tuya_cutter import TuyaCutter, DEFAULT_PRODUCT_TEST_PORT

serial, local, remote = sys.argv[1], sys.argv[2], sys.argv[3]
adb = sys.argv[4] if len(sys.argv) > 4 else "adb"
CHUNK = 98304
DELIM = "TPP06_UU_7Z9q"

# 8090 por el tunel USB (para derivar la password); nada por red.
subprocess.run([adb, "-s", serial, "forward",
                f"tcp:{DEFAULT_PRODUCT_TEST_PORT}", f"tcp:{DEFAULT_PRODUCT_TEST_PORT}"],
               capture_output=True)
c = TuyaCutter("127.0.0.1", Path("~/Documents/tpp06-backups"), adb=adb)
c.discover()
c.session.serial = serial
sess = c.session

data = Path(local).read_bytes()
full_md5 = hashlib.md5(data).hexdigest()
nchunks = (len(data) + CHUNK - 1) // CHUNK
d = os.path.dirname(remote) or "/tmp"
sess.run_root(f"mkdir -p {d}; rm -f /tmp/_uu_part_*", check=False)

for i in range(nchunks):
    chunk = data[i*CHUNK:(i+1)*CHUNK]
    cmd5 = hashlib.md5(chunk).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(chunk); tfp = tf.name
    uu = subprocess.run(["uuencode", "-m", tfp, "c"], capture_output=True, text=True).stdout
    os.unlink(tfp)
    part = f"/tmp/_uu_part_{i:03d}"
    ok = False
    for attempt in range(3):
        command = (f"cat > /tmp/_uu.in <<'{DELIM}'\n{uu}{DELIM}\n"
                   f"uudecode -o {part} /tmp/_uu.in 2>/tmp/_uuerr; "
                   f"md5sum {part} 2>/dev/null | cut -d' ' -f1")
        res = sess.run_root(command, timeout=180, check=False)
        if cmd5 in res.stdout:
            ok = True; break
    if not ok:
        print(f"CHUNK {i} FALLO tras reintentos:\n{res.stdout[-300:]}"); sys.exit(2)
    print(f"  chunk {i+1}/{nchunks} OK")

# ensamblar en orden y verificar total
res = sess.run_root(f"cat /tmp/_uu_part_* > {remote}; chmod 755 {remote}; "
                    f"echo SZ=$(wc -c < {remote}); echo MD5=$(md5sum {remote} | cut -d' ' -f1); "
                    f"rm -f /tmp/_uu_part_* /tmp/_uu.in", timeout=120, check=False)
print(res.stdout.strip())
print(f"LOCAL SZ={len(data)} MD5={full_md5}")
print("MATCH" if full_md5 in res.stdout else "MISMATCH")
