import io

import httpx
import numpy as np
import pytest
from PIL import Image

from tspotify import art
from tspotify.audio import Audio


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://i.scdn.co/image/a", True),
        ("https://image-cdn-fa.spotifycdn.com/image/a", True),
        ("https://mosaic.scdn.co/abc", True),
        ("http://127.0.0.1/secret", False),
        ("https://scdn.co.attacker.example/a", False),
        ("file:///etc/passwd", False),
    ],
)
def test_cover_source_validation(url, allowed):
    assert art.allowed_image(url) is allowed


def test_cover_is_resized_and_downloaded_once(monkeypatch):
    output = io.BytesIO()
    Image.new("RGB", (12, 12), (255, 80, 20)).save(output, "PNG")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=output.getvalue())

    monkeypatch.setattr(art, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    art.cover.cache_clear()
    image = art.cover("https://i.scdn.co/image/test", 24, 24)
    assert len(image) == 24 and len(image[0]) == 24
    assert art.cover("https://i.scdn.co/image/test", 24, 24) is image
    assert len(calls) == 1


def test_logo_is_a_symmetric_circle_with_three_dark_arcs():
    image = art.logo()
    assert len(image) == 80 and len(image[0]) == 80
    assert image[0][0] == (0, 0, 0)
    center = [sum(image[y][40]) > 100 for y in range(80)]
    dark_runs = sum(not center[i] and center[i - 1] for i in range(1, 78))
    assert dark_runs >= 3


def test_real_fft_distinguishes_silence_and_a_low_frequency_tone():
    audio = Audio.__new__(Audio)
    import threading

    audio.enabled = True
    audio.stop = threading.Event()
    audio.n = 2048
    audio.window = np.hanning(audio.n).astype(np.float32)
    edges = np.geomspace(3, audio.n // 2 - 1, 33).astype(int)
    audio.bands = [(int(edges[i]), max(int(edges[i]) + 1, int(edges[i + 1]))) for i in range(32)]
    audio.update(np.zeros(audio.n, dtype=np.float32))
    assert max(audio.bars) == 0
    audio.update(0.6 * np.sin(2 * np.pi * 250 * np.arange(audio.n) / 32000))
    assert max(audio.bars) > 0.6
    assert np.argmax(audio.bars) < 16
