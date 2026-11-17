import json
import time
from types import SimpleNamespace

import httpx
import pytest

from tspotify import auth
from tspotify.api import RateLimited, Spotify, SpotifyError, track_from_entry


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(auth, "access_token", lambda: "test-only-token")
    value = Spotify()
    yield value
    value.close()


def mock(api, handler):
    api.http.close()
    api.http = httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "entry,valid",
    [
        (None, False),
        ({"track": None}, False),
        ({"track": {"id": "abc"}}, True),
        ({"item": {"id": "abc"}}, True),
        ({"id": "abc", "track": True}, True),
        ({"id": "abc", "is_local": True}, False),
        (True, False),
    ],
)
def test_new_and_old_spotify_track_schemas_are_normalized(entry, valid):
    value = track_from_entry(entry)
    assert bool(value) == valid
    if valid:
        assert isinstance(value, dict)


def test_playlist_follows_pagination_and_caches_complete_result(api):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "offset=1" in str(request.url):
            return httpx.Response(200, json={"items": [{"track": {"id": "b", "track": True}}], "next": None})
        return httpx.Response(
            200,
            json={
                "items": [{"track": {"id": "a"}}, {"track": None}],
                "next": "https://api.spotify.com/v1/playlists/p/tracks?offset=1",
            },
        )

    mock(api, handler)
    value = api.context("playlist", "p", "Private mix")
    assert [x["id"] for x in value["tracks"]] == ["a", "b"]
    assert value["context_uri"] == "spotify:playlist:p"
    assert api.context("playlist", "p", "Private mix") == value
    assert len(calls) == 2


def test_rate_limit_blocks_further_requests_instead_of_hammering_spotify(api):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "20"})

    mock(api, handler)
    with pytest.raises(RateLimited):
        api.request("GET", "me")
    with pytest.raises(RateLimited):
        api.request("PUT", "me/player/play")
    assert len(calls) == 1


def test_authorization_is_never_sent_to_other_hosts(api):
    mock(api, lambda request: pytest.fail("network must not be used"))
    with pytest.raises(ValueError):
        api.request("GET", "https://attacker.example/collect")


@pytest.mark.parametrize("saved,method,new_state", [(True, "DELETE", False), (False, "PUT", True)])
def test_favorite_toggle_uses_server_state_not_stale_local_cache(api, saved, method, new_state):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=[saved]) if request.method == "GET" else httpx.Response(204)

    mock(api, handler)
    assert api.toggle_favorite("track") is new_state
    assert calls == ["GET", method]


def test_track_start_preserves_playlist_context_and_waits_for_actual_playing(api):
    bodies = []
    mock(api, lambda req: bodies.append(json.loads(req.content)) or httpx.Response(204))
    waited = []
    api.device_id = "device"
    api.backend = SimpleNamespace(_events=lambda: None, wait_playing=lambda *a, **k: waited.append((a, k)))
    message = api.start({"id": "track", "name": "Song", "_context_uri": "spotify:playlist:mix"})
    assert bodies == [{"context_uri": "spotify:playlist:mix", "offset": {"uri": "spotify:track:track"}}]
    assert waited[0][0] == ("track",)
    assert "Song" in message


def test_accepted_request_is_not_misreported_as_success_if_audio_cannot_load(api):
    mock(api, lambda req: httpx.Response(204))
    api.device_id = "device"

    def failed(*args, **kwargs):
        raise RuntimeError("audio failed")

    api.backend = SimpleNamespace(_events=lambda: None, wait_playing=failed)
    with pytest.raises(RuntimeError, match="audio failed"):
        api.start({"id": "track"})


def test_auto_device_prefers_existing_logged_in_desktop_spotify(api):
    mock(
        api,
        lambda req: httpx.Response(
            200,
            json={
                "devices": [
                    {"id": "ours", "name": "tspotify", "is_active": False},
                    {"id": "desktop", "name": "My MacBook", "is_active": True},
                ]
            },
        ),
    )
    assert api.select_device()["id"] == "desktop"
    assert api.external and api.device_id == "desktop"


def test_integrated_preference_does_not_hijack_existing_device(api):
    mock(api, lambda req: httpx.Response(200, json={"devices": [{"id": "other", "name": "Desktop"}]}))
    assert api.select_device("integrated") is None
    assert not api.external


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_http_failures_are_visible_not_success_messages(api, status):
    mock(api, lambda req: httpx.Response(status, json={"error": {"message": "denied"}}))
    with pytest.raises((SpotifyError, auth.LoginRequired)):
        api.request("PUT", "me/player/play")


def test_control_volume_is_bounded(api):
    urls = []
    mock(api, lambda req: urls.append(str(req.url)) or httpx.Response(204))
    api.device_id = "test"
    api.control("volume", 140)
    api.control("volume", -10)
    assert "volume_percent=100" in urls[0] and "volume_percent=0" in urls[1]


def test_expired_rate_limit_allows_request(api):
    api.rate_until = time.monotonic() - 1
    mock(api, lambda req: httpx.Response(200, json={"ok": True}))
    assert api.request("GET", "me") == {"ok": True}


def test_first_play_waits_for_connect_device_registration(api, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(404, json={"error": {"message": "Device not found"}})
            if len(calls) == 1
            else httpx.Response(204)
        )

    mock(api, handler)
    monkeypatch.setattr("tspotify.api.time.sleep", lambda seconds: None)
    monkeypatch.setattr(api, "devices", lambda: [{"id": "device", "name": "tspotify"}])
    api.device_id = "device"
    api.backend = SimpleNamespace(_events=lambda: None, wait_playing=lambda *a, **k: None)
    assert "Воспроизводится" in api.start({"id": "track"})
    assert len(calls) == 2


def test_missing_connect_registration_restarts_engine_and_uses_new_device(api, monkeypatch):
    urls = []
    restarts = []

    def handler(request):
        urls.append(str(request.url))
        if "old-device" in str(request.url):
            return httpx.Response(404, json={"error": {"message": "Device not found"}})
        return httpx.Response(204)

    mock(api, handler)
    monkeypatch.setattr("tspotify.api.time.sleep", lambda seconds: None)
    backend = SimpleNamespace(device_id="old-device", _events=lambda: None, wait_playing=lambda *a, **k: None)

    def restart():
        restarts.append(True)
        backend.device_id = "new-device"

    backend.restart = restart
    api.backend = backend
    api.device_id = "old-device"
    monkeypatch.setattr(
        api, "devices", lambda: [] if not restarts else [{"id": "new-device", "name": "tspotify"}]
    )
    assert "Воспроизводится" in api.start({"id": "track"})
    assert restarts == [True]
    assert "old-device" in urls[0] and "new-device" in urls[-1]


def test_offline_external_device_is_not_replaced_by_unrequested_local_audio(api):
    mock(api, lambda request: httpx.Response(404, json={"error": {"message": "Device not found"}}))
    api.external = True
    api.device_id = "phone"
    with pytest.raises(SpotifyError):
        api.start({"id": "track"})
