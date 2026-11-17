"""Exercise a real native curses frontend in an offline pseudo-terminal."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

binary = Path(sys.argv[1]).resolve()
command = [str(binary), "--demo", "--no-splash"]
height, width = (24, 80) if "--compact" in sys.argv else (48, 140)
output = []
seen = threading.Event()


def interact(send):
    if "--interact" not in sys.argv:
        return
    # Click the visible expand button through the terminal mouse protocol.
    send(f"\x1b[<0;{width - 42};1M\x1b[<0;{width - 42};1m")
    deadline = time.monotonic() + 8
    while "NOW PLAYING" not in "".join(output) and time.monotonic() < deadline:
        time.sleep(0.05)
    if "NOW PLAYING" not in "".join(output):
        raise RuntimeError("Mouse did not open the expanded player: " + "".join(output)[-2000:])
    send("\x1bOR")  # F3 returns to the library.


if os.name == "nt":
    from winpty import PtyProcess

    process = PtyProcess.spawn(subprocess.list2cmdline(command), dimensions=(height, width))

    def read():
        while process.isalive():
            try:
                text = process.read(8192)
            except EOFError:
                return
            output.append(text)
            if "tspotify" in "".join(output):
                seen.set()

    threading.Thread(target=read, daemon=True).start()
    if not seen.wait(20):
        process.close(force=True)
        raise SystemExit("Native terminal failed to render: " + "".join(output)[-2000:])
    interact(process.write)
    process.write("q")
    deadline = time.monotonic() + 10
    while process.isalive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.isalive():
        process.close(force=True)
        raise SystemExit("Native terminal did not exit after q")
else:
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
    env = dict(os.environ, TERM="xterm-256color")
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)

    def read():
        while process.poll() is None:
            try:
                data = os.read(master, 8192)
            except OSError:
                return
            if not data:
                return
            output.append(data.decode(errors="replace"))
            if b"\x1b[6n" in data:
                os.write(master, b"\x1b[1;1R")
            if "tspotify" in "".join(output):
                seen.set()

    threading.Thread(target=read, daemon=True).start()
    if not seen.wait(20):
        process.kill()
        process.wait()
        raise SystemExit("Native terminal failed to render: " + "".join(output)[-2000:])
    interact(lambda value: os.write(master, value.encode()))
    os.write(master, b"q")
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise SystemExit("Native terminal did not exit after q") from None
    os.close(master)
    if code:
        raise SystemExit("Native terminal returned an error: " + "".join(output)[-2000:])

print("Native terminal rendered dotted UI and exited with q; no account or audio used.")
