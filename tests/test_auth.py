from datetime import datetime, timedelta, timezone

import pytest

from tspotify import auth, paths


def test_callback_accepts_exact_session_and_decodes_code():
    assert auth.parse_callback("/ncspot_login?state=right&code=hello%2Bworld", "right") == "hello+world"


@pytest.mark.parametrize(
    "url",
    [
        "/ncspot_login?state=other&code=secret",
        "/ncspot_login?code=secret",
        "/login?state=right&code=secret",
        "/ncspot_login?state=right&error=access_denied",
        "/ncspot_login?state=right",
        "/favicon.ico",
    ],
)
def test_callback_rejects_other_sessions_denials_and_stray_requests(url):
    with pytest.raises(ValueError):
        auth.parse_callback(url, "right")


def test_fresh_cached_token_never_makes_network_call(monkeypatch):
    paths.private_json(
        paths.TOKEN_FILE,
        {
            "access_token": "private",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        },
    )
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: pytest.fail("unexpected network"))
    assert auth.access_token() == "private"


def test_refresh_retains_refresh_token_if_spotify_omits_it():
    value = auth.save_token({"access_token": "new", "expires_in": 3600}, {"refresh_token": "keep"})
    assert value["refresh_token"] == "keep"
    assert paths.TOKEN_FILE.is_file()


def test_missing_login_is_a_clear_recoverable_error():
    with pytest.raises(auth.LoginRequired, match="tspotify login"):
        auth.access_token()
