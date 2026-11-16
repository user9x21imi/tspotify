import io
import tarfile
import time
import zipfile
from types import SimpleNamespace

import pytest

from tspotify import paths
from tspotify.backend import ASSETS, Backend, asset_for, unpack_engine


@pytest.mark.parametrize(
    "system,machine,expected",
    [
        ("Linux", "x86_64", "x86_64-unknown-linux-gnu.tar.gz"),
        ("Linux", "arm64", "aarch64-unknown-linux-gnu.tar.gz"),
        ("Darwin", "arm64", "aarch64-apple-darwin.tar.gz"),
        ("Darwin", "x86_64", "x86_64-apple-darwin.tar.gz"),
        ("Windows", "AMD64", "x86_64-pc-windows-msvc.zip"),
    ],
)
def test_platform_download_matrix(system, machine, expected):
    assert asset_for(system, machine)[0] == expected


def test_unknown_platform_fails_clearly():
    with pytest.raises(RuntimeError, match="Нет проверенной сборки"):
        asset_for("Plan9", "mips")


def test_every_download_has_a_pinned_sha256():
    assert len(ASSETS) == 5
    for _, digest in ASSETS.values():
        assert len(bytes.fromhex(digest)) == 32


def test_tar_installer_reads_only_the_regular_executable_without_writing_paths(tmp_path):
    before = set(tmp_path.rglob("*"))
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        info = tarfile.TarInfo("../../spotify_player")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"valid"))
        extra = tarfile.TarInfo("../../malicious-file")
        extra.size = 3
        tar.addfile(extra, io.BytesIO(b"bad"))
    assert unpack_engine(data.getvalue(), "linux.tar.gz") == b"valid"
    assert set(tmp_path.rglob("*")) == before


def test_archive_symlink_cannot_substitute_for_engine():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        info = tarfile.TarInfo("spotify_player")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(RuntimeError):
        unpack_engine(data.getvalue(), "linux.tar.gz")


def test_windows_archive_selects_executable():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("bin/spotify_player.exe", b"windows binary")
        archive.writestr("README", b"ignore")
    assert unpack_engine(data.getvalue(), "windows.zip") == b"windows binary"


def test_hidden_terminal_answers_fragmented_cursor_query_once():
    backend = Backend()
    replies = []
    backend.pty = SimpleNamespace(write=replies.append)
    backend.respond_queries("frame\x1b[")
    backend.respond_queries("6n")
    backend.respond_queries("more output")
    assert replies == ["\x1b[1;1R"]


def test_audio_error_cannot_confirm_playback():
    backend = Backend(muted=True)
    backend.failure = "track"
    backend.failure_at = time.monotonic() + 1
    with pytest.raises(RuntimeError, match="не смог загрузить"):
        backend.wait_playing("track", since=time.monotonic(), timeout=0.1)


def test_old_playing_event_cannot_confirm_new_command():
    backend = Backend(muted=True)
    backend.event = {"track_id": "track", "playing": True, "at": time.monotonic() - 20}
    with pytest.raises(RuntimeError, match="не подтверждён"):
        backend.wait_playing("track", since=time.monotonic(), timeout=0.05)


def test_log_reader_distinguishes_new_event_with_identical_track_and_position():
    target = paths.BACKEND_CACHE / "spotify-player-test.log"
    target.write_text(
        '2026-01-01T00:00:00 Got a new player event: Playing { playable_id: Track(TrackId("abc")), position_ms: 0 }\n'
    )
    backend = Backend()
    backend._events()
    first = backend.event["signature"]
    target.write_text(
        target.read_text()
        + '2026-01-01T00:01:00 Got a new player event: Playing { playable_id: Track(TrackId("abc")), position_ms: 0 }\n'
    )
    backend._events()
    assert backend.event["signature"] != first


def test_restarted_engine_uses_latest_device_id_in_same_minute_log():
    target = paths.BACKEND_CACHE / "spotify-player-repeated.log"
    target.write_text(
        "Initializing a new integrated player with device_id=1111-aaaa\n"
        "Initializing a new integrated player with device_id=2222-bbbb\n"
    )
    backend = Backend()
    backend._events()
    assert backend.device_id == "2222-bbbb"
