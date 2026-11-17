import curses
import time
from types import SimpleNamespace

import pytest

from tspotify import paths, ui


class Window:
    def __init__(self, height=48, width=140):
        self.size = height, width
        self.calls = []

    def getmaxyx(self):
        return self.size

    def addnstr(self, y, x, text, limit, style=0):
        assert 0 <= y < self.size[0] and 0 <= x < self.size[1]
        self.calls.append((y, x, str(text)[:limit]))

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.fixture
def app(monkeypatch):
    for name in ("start_color", "use_default_colors", "init_pair", "curs_set"):
        monkeypatch.setattr(curses, name, lambda *args: None)
    monkeypatch.setattr(curses, "has_colors", lambda: True)
    monkeypatch.setattr(curses, "color_pair", lambda x: x)
    monkeypatch.setattr(curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(curses, "COLOR_PAIRS", 65536, raising=False)
    monkeypatch.setattr(ui, "ART", paths.CACHE)
    monkeypatch.setattr(ui, "CONFIG", paths.CONFIG / "layout.json")
    monkeypatch.setattr(ui, "ASCII_ONLY", False)
    monkeypatch.setattr(
        ui, "Audio", lambda: SimpleNamespace(bars=[0.0] * 32, enabled=True, status="test", close=lambda: None)
    )
    track = {"id": "t", "name": "Track", "type": "track", "track": True, "artists": [{"name": "Artist"}]}

    def cached(name, default):
        return {
            "Playlists": [{"Playlist": {"id": "p", "name": "Playlist"}}],
            "SavedAlbums": [{"id": "a", "name": "Album"}],
            "SavedTracks": {"spotify:track:t": track},
        }.get(name, default)

    monkeypatch.setattr(ui, "cached", cached)
    calls = []

    def record(name, result=None):
        def fn(*args):
            calls.append((name, args))
            return result

        return fn

    fake = SimpleNamespace(
        start=record("start", "playing"),
        start_context=record("start_context", "playing"),
        control=record("control", "done"),
        context=record("context", {"name": "Mix", "tracks": [track], "context_uri": "spotify:playlist:p"}),
        toggle_favorite=record("favorite", False),
        library=lambda section: (
            section,
            [("playlist", {"id": "p", "name": "Playlist"})]
            if section == "Плейлисты"
            else [("album", {"id": "a", "name": "Album"})],
        ),
        search=record("search", {"tracks": [track]}),
        backend=SimpleNamespace(restart=lambda: True),
        playback=lambda: {"item": track, "device": {"volume_percent": 0}},
    )
    monkeypatch.setattr(ui, "API", fake)
    monkeypatch.setattr(ui, "thumbnail", lambda *args: [[(80, 180, 100)] * 24 for _ in range(24)])
    value = ui.App(Window())
    value.playback = {"item": track, "device": {"volume_percent": 0}}
    value.test_calls = calls
    yield value
    value.pool.shutdown(wait=True, cancel_futures=True)
    value.art_pool.shutdown(wait=True, cancel_futures=True)


def settle(app):
    for _ in range(100):
        app.drain()
        if not app.pending:
            return
        time.sleep(0.002)
    pytest.fail("background UI work did not settle")


def test_enter_opens_playlist_then_plays_selected_track_in_context(app):
    app.key("\n")
    settle(app)
    assert app.section == "Mix" and app.items[0][0] == "track"
    assert isinstance(app.items[0][1], dict)  # Spotify's track:true must not become a boolean row.
    app.key("\n")
    settle(app)
    assert app.test_calls[-1][0] == "start"
    assert app.test_calls[-1][1][0]["_context_uri"] == "spotify:playlist:p"


def test_back_restores_library_selection(app):
    app.key("\n")
    settle(app)
    app.key(curses.KEY_LEFT)
    assert app.section == "Плейлисты" and app.items[0][0] == "playlist"


def test_search_focus_unicode_query_and_results(app):
    app.key("/")
    app.key("Музыка")
    app.key("\n")
    settle(app)
    assert app.test_calls[-1] == ("search", ("Музыка",))
    assert app.items[0][0] == "track"
    app.key("\t")
    assert app.editing
    app.key("\x1b")
    assert not app.editing


@pytest.mark.parametrize(
    "key,command", [(" ", "play-pause"), ("n", "next"), ("p", "previous"), ("s", "shuffle"), ("r", "repeat")]
)
def test_playback_keys_route_to_checked_control(app, key, command):
    app.key(key)
    settle(app)
    assert app.test_calls[-1] == ("control", (command, None))


def test_volume_limits_mute_and_restore(app):
    app.volume = 0
    app.key("-")
    settle(app)
    assert app.test_calls[-1] == ("control", ("volume", "0"))
    app.volume = 100
    app.key("+")
    settle(app)
    assert app.test_calls[-1] == ("control", ("volume", "100"))
    app.volume = 40
    app.key("m")
    settle(app)
    app.key("m")
    settle(app)
    assert app.volume == 40


def test_favorite_button_uses_server_confirmed_toggle(app):
    app.key("f")
    settle(app)
    assert app.test_calls[-1] == ("favorite", ("t",))
    assert "spotify:track:t" not in app.saved_tracks


def test_non_ascii_favorite_names_are_saved_as_utf8(app, monkeypatch):
    import json

    app.playback["item"]["name"] = "Тихая музыка"
    monkeypatch.setattr(ui.API, "toggle_favorite", lambda *args: True)
    app.key("f")
    settle(app)
    value = json.loads((ui.ART / "favorites.json").read_text(encoding="utf-8"))
    assert value["spotify:track:t"]["name"] == "Тихая музыка"


@pytest.mark.parametrize("key,section", [("1", "Плейлисты"), ("2", "Любимые"), ("3", "Альбомы")])
def test_library_tabs(app, key, section):
    app.key(key)
    assert app.section == section and app.items


def test_layout_reorder_cover_side_and_saving(app):
    app.key("L")
    app.key("J")
    app.key("c")
    app.key("\n")
    assert app.order[0] == "player" and app.cover_right and not app.layout_editor
    assert ui.CONFIG.exists()


def test_help_and_exit(app):
    app.key("?")
    assert app.help
    app.key("\x1b")
    assert not app.help
    app.key(curses.KEY_F1)
    assert app.help
    app.key("?")
    app.key("q")
    assert not app.running


def test_full_playlist_shortcut_preserves_selected_item(app):
    app.key("P")
    settle(app)
    assert app.test_calls[-1][0] == "start_context"
    assert app.test_calls[-1][1][0] == "playlist"


def test_command_failure_is_displayed_instead_of_success(app, monkeypatch):
    def fail(*args):
        raise RuntimeError("test network failure")

    monkeypatch.setattr(ui.API, "control", fail)
    app.key(" ")
    settle(app)
    assert "test network failure" in app.notice


def test_hiding_spectrum_and_cover_style_toggle(app):
    app.key("L")
    app.key("v")
    app.key("\n")
    assert not app.show_spectrum
    original = app.art_mode
    app.key("a")
    assert app.art_mode != original


@pytest.mark.parametrize(
    "height,width", [(24, 80), (25, 78), (30, 78), (40, 100), (48, 140), (80, 160), (18, 60)]
)
def test_layout_and_live_animation_survive_terminal_resize(app, height, width):
    app.render()
    settle(app)
    app.s.size = (height, width)
    app.render()
    app.animate()


def test_thumbnail_jobs_only_fetch_visible_items_once(app):
    app.render()
    settle(app)
    assert app.thumbnails.get("playlist:p")
    before = len(app.thumbnails)
    app.render()
    settle(app)
    assert len(app.thumbnails) == before


def test_ascii_font_fallback_uses_ascii_art_and_box_characters(app, monkeypatch):
    monkeypatch.setattr(ui, "ASCII_ONLY", True)
    app.art_mode = "ascii"
    app.render()
    assert any("+" in text for _, _, text in app.s.calls)


def test_spectrum_interpolates_instead_of_jumping_to_new_target(app):
    app.audio.bars = [1.0] * 32
    app.smooth_time = time.monotonic() - 0.03
    app.spectrum(1, 1, 4, 100)
    assert 0 < app.smooth_bars[0] < 0.5
    old = app.smooth_bars[0]
    app.audio.bars = [0.0] * 32
    app.smooth_time = time.monotonic() - 0.03
    app.spectrum(1, 1, 4, 100)
    assert old * 0.8 < app.smooth_bars[0] < old


def test_private_playlist_cover_uses_authenticated_library_image_url(app, monkeypatch):
    calls = []
    monkeypatch.setattr(ui, "cover", lambda url, *args: calls.append(url) or [[(1, 2, 3)]])
    monkeypatch.setattr(
        ui, "thumbnail", lambda *args: pytest.fail("public oEmbed not needed for private artwork")
    )
    app.load_thumbnail("playlist", {"id": "private", "images": [{"url": "https://i.scdn.co/image/private"}]})
    assert calls == ["https://i.scdn.co/image/private"]


def test_library_refresh_clamps_selection_when_remote_list_shrinks(app):
    settle(app)
    app.items = [("playlist", {"id": "p", "name": "One"}), ("playlist", {"id": "gone", "name": "Removed"})]
    app.selected = 1
    app.events.put(("library:Плейлисты", ("Плейлисты", [("playlist", {"id": "p", "name": "One"})]), None))
    app.drain()
    assert app.selected == 0
    app.key("\n")
    settle(app)


@pytest.mark.parametrize(
    "key,expected",
    [
        (curses.KEY_F7, 35),
        (curses.KEY_F8, 45),
        (curses.KEY_F9, 35),
        (curses.KEY_F10, 45),
        (curses.KEY_F11, 35),
        (curses.KEY_F12, 45),
    ],
)
@pytest.mark.parametrize("mode", ["normal", "editing", "help", "layout_editor"])
def test_function_volume_keys_work_in_all_input_modes(app, key, expected, mode):
    app.volume = 40
    if mode != "normal":
        setattr(app, mode, True)
    app.query = "keep search"
    app.key(key)
    settle(app)
    assert app.volume == expected
    assert app.test_calls[-1] == ("control", ("volume", str(expected)))
    assert app.query == "keep search"
    if mode != "normal":
        assert getattr(app, mode)


@pytest.mark.parametrize(
    "key,section",
    [
        ("4", "Недавние"),
        ("5", "Ваши миксы"),
        ("6", "Новые релизы"),
        ("7", "Рекомендации"),
        ("8", "Миксы дня"),
    ],
)
def test_discovery_sections_are_available_from_number_keys(app, key, section):
    app.key(key)
    settle(app)
    assert app.section == section


@pytest.mark.parametrize("size", [(24, 80), (48, 140), (70, 180)])
def test_fullscreen_artwork_metadata_spectrum_and_return(app, size):
    app.s.size = size
    selected = app.selected
    app.key(curses.KEY_F3)
    assert app.fullscreen
    app.art = [[(150, 180, 200)] * 32 for _ in range(32)]
    app.render()
    assert any("NOW PLAYING" in text for _, _, text in app.s.calls)
    assert any("Track" in text for _, _, text in app.s.calls)
    assert app.spectrum_area[2] > 0
    app.animate()
    app.key("\x1b")
    assert not app.fullscreen and app.selected == selected


def mouse_at(app, monkeypatch, x, y, state):
    monkeypatch.setattr(curses, "getmouse", lambda: (0, x, y, 0, state))
    app.key(curses.KEY_MOUSE)


def test_mouse_can_select_and_double_click_open_playlist(app, monkeypatch):
    app.render()
    y, x, _, _, _, _ = next(hit for hit in app.hits if hit[4] == "row")
    mouse_at(app, monkeypatch, x, y, curses.BUTTON1_CLICKED)
    assert app.selected == 0
    assert not any(call[0] == "context" for call in app.test_calls)
    mouse_at(app, monkeypatch, x, y, curses.BUTTON1_DOUBLE_CLICKED)
    settle(app)
    assert app.test_calls[-1][0] == "context"


def test_mouse_can_open_discovery_tab_and_expand_player(app, monkeypatch):
    app.render()
    y, x, *_ = next(hit for hit in app.hits if hit[4:] == ("key", "4"))
    mouse_at(app, monkeypatch, x, y, curses.BUTTON1_CLICKED)
    settle(app)
    assert app.section == "Недавние"
    app.render()
    y, x, *_ = next(hit for hit in app.hits if hit[4:] == ("key", curses.KEY_F3))
    mouse_at(app, monkeypatch, x, y, curses.BUTTON1_CLICKED)
    assert app.fullscreen


def test_mouse_hover_focuses_panel_without_starting_audio(app, monkeypatch):
    app.render()
    y, x, *_ = next(hit for hit in app.hits if hit[4:] == ("panel", "search"))
    mouse_at(app, monkeypatch, x + 1, y + 1, curses.REPORT_MOUSE_POSITION)
    assert app.focus_panel == "search"
    assert not app.test_calls


def test_mouse_volume_bar_can_set_zero_without_playing(app, monkeypatch):
    app.render()
    y, x, *_ = next(hit for hit in app.hits if hit[4] == "volume")
    mouse_at(app, monkeypatch, x, y, curses.BUTTON1_CLICKED)
    settle(app)
    assert app.test_calls[-1] == ("control", ("volume", "0"))


def test_help_can_scroll_in_compact_terminal(app):
    app.s.size = (24, 80)
    app.help = True
    app.render()
    app.key(curses.KEY_DOWN)
    app.render()
    assert app.help_offset == 1
