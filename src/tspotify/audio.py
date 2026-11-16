"""Optional real output capture; playback never depends on a visualizer device."""

import os
import platform
import shutil
import subprocess
import threading

import numpy as np


class Audio:
    def __init__(self):
        self.bars = [0.0] * 32
        self.enabled = True
        self.stop = threading.Event()
        self.process = None
        self.status = "подключение…"
        self.n = 2048
        self.window = np.hanning(self.n).astype(np.float32)
        edges = np.geomspace(3, self.n // 2 - 1, 33).astype(int)
        self.bands = [(int(edges[i]), max(int(edges[i]) + 1, int(edges[i + 1]))) for i in range(32)]
        threading.Thread(target=self.run, daemon=True, name="tspotify-audio-spectrum").start()

    def update(self, data):
        if not self.enabled or self.stop.is_set():
            return
        samples = np.asarray(data, dtype=np.float32)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        if len(samples) != self.n:
            return
        bins = np.abs(np.fft.rfft(samples * self.window)) / self.n
        peaks = np.array([bins[lo:hi].max(initial=0) for lo, hi in self.bands])
        db = 20 * np.log10(np.maximum(peaks, 1e-8))
        self.bars = np.clip((db + 74) / 65, 0, 1).tolist()

    def run(self):
        try:
            if platform.system() == "Linux" and shutil.which("parec") and shutil.which("pactl"):
                sink = subprocess.check_output(["pactl", "get-default-sink"], text=True, timeout=3).strip()
                self.process = subprocess.Popen(
                    [
                        "parec",
                        "--device=" + sink + ".monitor",
                        "--format=float32le",
                        "--rate=32000",
                        "--channels=1",
                        "--latency-msec=60",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self.status = "системный выход"
                while not self.stop.is_set():
                    data = self.process.stdout.read(self.n * 4)
                    if len(data) != self.n * 4:
                        break
                    self.update(np.frombuffer(data, dtype="<f4"))
                return
            import soundcard as sc

            speaker = sc.default_speaker()
            candidates = sc.all_microphones(include_loopback=True)
            device = None
            configured = os.environ.get("TSPOTIFY_LOOPBACK")
            if configured:
                device = next((m for m in candidates if configured.lower() in m.name.lower()), None)
            elif platform.system() == "Windows" and speaker:
                device = sc.get_microphone(speaker.id, include_loopback=True)
            elif platform.system() == "Darwin":
                # Never silently record a user's physical microphone.
                device = next(
                    (m for m in candidates if "blackhole" in m.name.lower() or "loopback" in m.name.lower()),
                    None,
                )
            if device is None:
                self.status = "нет loopback · звук работает без спектра"
                return
            self.status = "системный выход"
            with device.recorder(samplerate=32000, channels=2, blocksize=self.n) as recorder:
                while not self.stop.is_set():
                    self.update(recorder.record(numframes=self.n))
        except (ImportError, OSError, RuntimeError, subprocess.SubprocessError):
            self.status = "захват выхода недоступен · звук работает"

    def close(self):
        self.stop.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
