"""Pinned, verified streaming engine and a foreground-owned cross-platform PTY."""

import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import threading
import time
import zipfile
from pathlib import Path

import httpx

from . import paths

VERSION = "0.25.2"
ASSETS = {
    ("Linux", "x86_64"): (
        "x86_64-unknown-linux-gnu.tar.gz",
        "08b572e0ec4b93418ade1d99703dbd0d2bdd14061babd36ea001153d749a1f4c",
    ),
    ("Linux", "aarch64"): (
        "aarch64-unknown-linux-gnu.tar.gz",
        "05fc52a25ecdf12c554f3fd567f3caef9e3604df2e49538fff7598b2003a26bb",
    ),
    ("Darwin", "arm64"): (
        "aarch64-apple-darwin.tar.gz",
        "082de0ed83120804b3e1c2b91b9c21e37dfa111c51593847f52e1a40b6fb9b55",
    ),
    ("Darwin", "x86_64"): (
        "x86_64-apple-darwin.tar.gz",
        "9b7c6fde406c9caa341e1b6f5041627b941f8f7f9f8733b004af8470a2f83937",
    ),
    ("Windows", "AMD64"): (
        "x86_64-pc-windows-msvc.zip",
        "5ddd441c1572c09c84a253b6fb2a63beb64627a785a88328d2e438a5c29432c9",
    ),
}


def asset_for(system=None, machine=None):
    system, machine = system or platform.system(), machine or platform.machine()
    if system == "Windows" and machine.lower() in ("x86_64", "amd64"):
        machine = "AMD64"
    if system == "Linux" and machine == "arm64":
        machine = "aarch64"
    try:
        return ASSETS[system, machine]
    except KeyError:
        raise RuntimeError(f"Нет проверенной сборки Spotify engine для {system}/{machine}") from None


def unpack_engine(data, suffix):
    """Extract exactly one regular executable, never archive paths or symlinks."""
    name = "spotify_player.exe" if suffix.endswith(".zip") else "spotify_player"
    if suffix.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [x for x in archive.infolist() if Path(x.filename).name == name and not x.is_dir()]
            if len(entries) != 1 or entries[0].file_size > 200_000_000:
                raise RuntimeError("Неверный архив движка")
            return archive.read(entries[0])
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        entries = [x for x in archive.getmembers() if Path(x.name).name == name and x.isfile()]
        if len(entries) != 1 or entries[0].size > 200_000_000:
            raise RuntimeError("Неверный архив движка")
        return archive.extractfile(entries[0]).read()


def engine_path(install=True):
    configured = os.environ.get("TSPOTIFY_ENGINE")
    if configured:
        value = Path(configured).expanduser().resolve()
        if not value.is_file():
            raise RuntimeError("TSPOTIFY_ENGINE не указывает на исполняемый файл")
        return value
    name = "spotify_player.exe" if os.name == "nt" else "spotify_player"
    target = paths.DATA / "bin" / name
    if target.exists():
        return target
    existing = shutil.which(name)
    if existing:
        version = subprocess.run([existing, "--version"], capture_output=True, text=True, timeout=5)
        if version.returncode == 0 and VERSION in version.stdout:
            return Path(existing)
    if not install:
        return None
    suffix, expected = asset_for()
    print(f"Загружаю проверенный Spotify engine {VERSION} ({suffix})…", flush=True)
    url = f"https://github.com/aome510/spotify-player/releases/download/v{VERSION}/spotify_player-{suffix}"
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes(131072):
            data.extend(chunk)
            if len(data) > 150_000_000:
                raise RuntimeError("Архив превышает допустимый размер")
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Контрольная сумма Spotify engine не совпала; установка остановлена")
    binary = unpack_engine(data, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".download")
    temp.write_bytes(binary)
    temp.chmod(0o755)
    temp.replace(target)
    return target


class Backend:
    def __init__(self, muted=False, external=False, on_authorize=None):
        self.muted = muted
        self.external = external
        self.preferred_volume = 35
        self.device_id = None
        self.on_authorize = on_authorize
        self.process = None
        self.pty = None
        self.master = None
        self.stopped = threading.Event()
        self.changed = threading.Condition()
        self.event = {}
        self.failure = None
        self.failure_at = 0.0
        self.failure_signature = None
        self._base = {}
        self._snapshot_time = 0.0
        self.output_tail = ""
        self._query_tail = ""

    def request(self, request, timeout=2):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            sock.settimeout(timeout)
            sock.connect(("127.0.0.1", paths.PORT))
            sock.send(json.dumps(request).encode())
            data = bytearray()
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 16 * 1024 * 1024:
                    raise RuntimeError("Слишком большой ответ локального движка")
            result = json.loads(data)
            if "Err" in result:
                raise RuntimeError(bytes(result["Err"]).decode(errors="replace"))
            payload = bytes(result["Ok"])
            return json.loads(payload) if payload else None

    def ready(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.2)
                sock.connect(("127.0.0.1", paths.PORT))
                sock.send(b"")
                return sock.recv(1) == b""
        except OSError:
            return False

    def start(self):
        paths.prepare()
        binary = engine_path()
        # This folder belongs to tspotify; never overwrite a user's other client settings.
        config = f'''client_id = "d420a117a32841c2b3474932e49fb54b"
login_redirect_uri = "http://127.0.0.1:8989/login"
client_port = {paths.PORT}
app_refresh_duration_in_ms = 1000
playback_refresh_duration_in_ms = 0
enable_streaming = "{"Never" if self.external else "Always"}"
enable_media_control = false
pause_on_startup = false
[device]
name = "tspotify"
device_type = "speaker"
volume = 0
bitrate = 160
audio_cache = true
'''
        (paths.BACKEND_CONFIG / "app.toml").write_text(config, encoding="utf-8")
        args = [
            str(binary),
            "--config-folder",
            str(paths.BACKEND_CONFIG),
            "--cache-folder",
            str(paths.BACKEND_CACHE),
        ]
        env = os.environ.copy()
        # The frontend handles authorization URLs once, instead of racing xdg-open/Firefox.
        env["BROWSER"] = "true" if os.name != "nt" else "cmd /c exit"
        if os.name == "nt":
            import subprocess as sp
            from winpty import PtyProcess

            self.pty = PtyProcess.spawn(sp.list2cmdline(args), dimensions=(30, 100), env=env)
        else:
            import fcntl
            import pty
            import struct
            import termios

            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            self.process = subprocess.Popen(
                args, stdin=slave, stdout=slave, stderr=slave, start_new_session=True, env=env
            )
            os.close(slave)
            self.master = master
        threading.Thread(target=self._read, daemon=True, name="spotify-engine-output").start()
        deadline = time.monotonic() + 75
        while not self.ready():
            if time.monotonic() > deadline:
                self.close()
                raise RuntimeError("Движок Spotify не запустился. Проверьте вход: tspotify login")
            if self.process and self.process.poll() is not None:
                raise RuntimeError("Spotify engine завершился при запуске. Выполните tspotify doctor")
            time.sleep(0.15)
        self._snapshot_time = 0
        self._events()

    def _read(self):
        buffer = ""
        while not self.stopped.is_set():
            try:
                chunk = (
                    self.pty.read(4096) if self.pty else os.read(self.master, 8192).decode(errors="replace")
                )
                if not chunk:
                    return
                self.respond_queries(chunk)
                self.output_tail = (self.output_tail + chunk)[-6000:]
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self._line(line)
                if len(buffer) > 200_000:
                    buffer = buffer[-4000:]
            except (OSError, EOFError):
                return

    def respond_queries(self, chunk):
        # A PTY supplies a terminal device, not an emulator. Crossterm asks for
        # cursor position during startup; ignoring CPR makes the engine exit
        # after briefly opening its socket, which looks like a successful start.
        data = self._query_tail + chunk
        for _ in range(data.count("\x1b[6n")):
            if self.pty:
                self.pty.write("\x1b[1;1R")
            elif self.master is not None:
                os.write(self.master, b"\x1b[1;1R")
        self._query_tail = data[-3:]

    def _line(self, line):
        match = re.search(r"Browse to: (https://accounts\.spotify\.com/authorize\?[^\s\x1b]+)", line)
        if match and self.on_authorize:
            self.on_authorize(match.group(1))
        # Runtime logs go to the engine cache, so stream the log too via playback().

    def _events(self):
        files = list(paths.BACKEND_CACHE.glob("spotify-player-*.log"))
        if not files:
            return
        latest = max(files, key=lambda p: p.stat().st_mtime)
        try:
            with latest.open("rb") as stream:
                if self.device_id is None:
                    stream.seek(max(0, latest.stat().st_size - 200000))
                    header = stream.read().decode(errors="replace")
                    matches = list(
                        re.finditer(
                            r"Initializing a new integrated player with device_id=([0-9a-f-]+)", header
                        )
                    )
                    if matches:
                        self.device_id = matches[-1][1]
                stream.seek(max(0, latest.stat().st_size - 18000))
                text = stream.read().decode(errors="replace")
        except OSError:
            return
        matches = list(
            re.finditer(
                r"Got a new player event: (Playing|Paused) \{ playable_id: Track\(TrackId\(\"([^\"]+)\"\)\), position_ms: (\d+)",
                text,
            )
        )
        if matches:
            match = matches[-1]
            signature = text[text.rfind("\n", 0, match.start()) + 1 : match.end()]
            if signature != self.event.get("signature"):
                self.event = {
                    "signature": signature,
                    "track_id": match[2],
                    "playing": match[1] == "Playing",
                    "position": int(match[3]),
                    "at": time.monotonic(),
                }
        failures = list(re.finditer(r"unable to load track.*?spotify:track:([A-Za-z0-9]+)", text, re.I))
        if failures:
            match = failures[-1]
            signature = text[text.rfind("\n", 0, match.start()) + 1 : match.end()]
            if signature != self.failure_signature:
                self.failure_signature = signature
                self.failure = match[1]
                self.failure_at = time.monotonic()

    def wait_playing(self, track_id, since, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._events()
            if (
                self.event.get("track_id") == track_id
                and self.event.get("playing")
                and self.event.get("at", 0) >= since
            ):
                return
            if self.failure == track_id and self.failure_at >= since:
                raise RuntimeError(
                    "Spotify не смог загрузить аудио этого трека. Повторите или выберите другое устройство."
                )
            if self.stopped.wait(0.25):
                break
        raise RuntimeError(
            "Запуск трека не подтверждён: проверьте сеть или выберите настольный Spotify как устройство."
        )

    def playback(self, fallback):
        self._events()
        if time.monotonic() - self._snapshot_time > 5:
            try:
                self._base = self.request({"Get": {"Key": "Playback"}}, timeout=0.7) or {}
                self._snapshot_time = time.monotonic()
            except (OSError, TimeoutError):
                pass
        result = dict(self._base or fallback)
        if self.event and (fallback.get("item") or {}).get("id") == self.event.get("track_id"):
            base_item = self._base.get("item") or {}
            if base_item.get("id") != self.event.get("track_id") or not base_item.get("album"):
                result = dict(fallback)
        if self.event and (result.get("item") or {}).get("id") == self.event.get("track_id"):
            elapsed = time.monotonic() - self.event["at"] if self.event["playing"] else 0
            result.update(
                is_playing=self.event["playing"], progress_ms=self.event["position"] + elapsed * 1000
            )
        return result

    def restart(self):
        self.close()
        self.stopped.clear()
        self.event = {}
        self._base = {}
        self.failure = None
        self.device_id = None
        self.process = None
        self.pty = None
        self.start()
        return True

    def unmute_after_user_volume(self, volume):
        if platform.system() != "Linux" or not shutil.which("pactl"):
            return
        try:
            entries = json.loads(
                subprocess.check_output(["pactl", "--format=json", "list", "sink-inputs"], timeout=3)
            )
            for entry in entries:
                props = entry.get("properties", {})
                if "spotify_player" in props.get("application.process.binary", ""):
                    subprocess.run(["pactl", "set-sink-input-volume", str(entry["index"]), "100%"], timeout=3)
                    subprocess.run(
                        ["pactl", "set-sink-input-mute", str(entry["index"]), "0" if volume else "1"],
                        timeout=3,
                    )
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    def close(self):
        self.stopped.set()
        if self.pty:
            self.pty.close(force=True)
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=4)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=2)
        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass
            self.master = None
