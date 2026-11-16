"""Pooled Spotify requests, explicit failures, cached libraries and verified playback."""

import json
import threading
import time
from urllib.parse import quote

import httpx

from . import auth, paths


class SpotifyError(RuntimeError):
    pass


class RateLimited(SpotifyError):
    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__(f"Spotify ограничил запросы. Повторите через {seconds} сек.")


def track_from_entry(entry):
    if not isinstance(entry, dict):
        return None
    nested = entry.get("track") or entry.get("item")
    track = nested if isinstance(nested, dict) else entry
    return track if track.get("id") and not track.get("is_local", False) else None


class Spotify:
    def __init__(self):
        self.http = httpx.Client(
            http2=True,
            timeout=httpx.Timeout(12, connect=8),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )
        self.rate_until = 0.0
        self.backend = None
        self.device_id = None
        self.external = False
        self.last_playback = {}
        self._last_remote_poll = 0.0
        self._lock = threading.Lock()

    def close(self):
        self.http.close()

    def request(self, method, path, body=None, _retry=0):
        remaining = self.rate_until - time.monotonic()
        if remaining > 0:
            raise RateLimited(max(1, int(remaining)))
        if path.startswith("https://api.spotify.com/v1/"):
            url = path
        elif not path.startswith(("http:", "https:")):
            url = "https://api.spotify.com/v1/" + path.lstrip("/")
        else:
            raise ValueError("Недопустимый адрес API")
        try:
            response = self.http.request(
                method, url, json=body, headers={"Authorization": "Bearer " + auth.access_token()}
            )
        except httpx.TimeoutException:
            raise SpotifyError(
                "Spotify не ответил вовремя. Проверьте соединение; команда не подтверждена."
            ) from None
        except httpx.HTTPError:
            raise SpotifyError("Соединение со Spotify прервано; команда не подтверждена.") from None
        if response.status_code == 429:
            seconds = min(86400, max(1, int(response.headers.get("Retry-After", "30"))))
            self.rate_until = time.monotonic() + seconds
            if method == "GET" and _retry == 0 and seconds <= 3:
                time.sleep(seconds + 0.05)
                return self.request(method, path, body, _retry=1)
            raise RateLimited(seconds)
        if response.status_code == 401:
            raise auth.LoginRequired("Spotify запросил повторный вход: tspotify login")
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                message = ""
            if response.status_code == 403:
                message = message or "Нужен Spotify Premium и разрешение на управление воспроизведением"
            raise SpotifyError(f"Spotify {response.status_code}: {message or 'запрос отклонён'}")
        return response.json() if response.content else None

    def devices(self):
        return (self.request("GET", "me/player/devices") or {}).get("devices", [])

    def select_device(self, preference="auto"):
        if preference == "integrated":
            self.external = False
            self.device_id = None
            return None
        devices = self.devices()
        choices = [x for x in devices if not x.get("is_restricted")]
        if preference not in ("auto", "integrated"):
            match = next((x for x in choices if preference in (x["id"], x["name"])), None)
            if not match:
                raise SpotifyError("Устройство не найдено: " + preference)
        elif preference == "integrated":
            match = None
        else:
            external = [x for x in choices if x.get("name") not in ("tspotify", "spotify-player")]
            match = next((x for x in external if x.get("is_active")), None) or next(iter(external), None)
        if match:
            self.device_id = match["id"]
            self.external = True
        return match

    def playback(self, force=False):
        if not self.external and self.backend:
            try:
                value = self.backend.playback(self.last_playback)
                if value:
                    self.last_playback = value
                    return value
            except (OSError, TimeoutError):
                pass
        if force or time.monotonic() - self._last_remote_poll > 4:
            self.last_playback = self.request("GET", "me/player") or {}
            self._last_remote_poll = time.monotonic()
        return self.last_playback

    def device_query(self):
        if not self.device_id and self.backend and getattr(self.backend, "device_id", None):
            self.device_id = self.backend.device_id
        if not self.device_id and self.backend:
            device = self.backend.playback({}).get("device") or {}
            if device.get("name") == "tspotify":
                self.device_id = device.get("id")
        if not self.device_id:
            devices = self.devices()
            match = next((x for x in devices if x.get("name") == "tspotify"), None)
            if not match:
                match = next((x for x in devices if x.get("is_active")), None)
            if match:
                self.device_id = match["id"]
        if not self.device_id:
            raise SpotifyError(
                "Нет устройства воспроизведения. Откройте Spotify или выберите встроенный плеер."
            )
        return "device_id=" + quote(self.device_id, safe="")

    def start(self, item, context_uri=None):
        track_id = item.get("id")
        if not track_id:
            raise SpotifyError("У трека нет Spotify ID")
        uri = "spotify:track:" + track_id
        album = item.get("album") or {}
        context_uri = context_uri or item.get("_context_uri")
        if not context_uri and album.get("id"):
            context_uri = "spotify:album:" + album["id"]
        body = {"context_uri": context_uri, "offset": {"uri": uri}} if context_uri else {"uris": [uri]}
        # The backend's CLI starts tasks asynchronously and acknowledges them too early.
        # Send directly and require a real Playing event / remote state for this track.
        if self.backend and not self.external:
            self.backend._events()
        started = time.monotonic()
        self.request("PUT", "me/player/play?" + self.device_query(), body)
        if self.backend and not self.external:
            self.backend.wait_playing(track_id, since=started, timeout=25)
            self.last_playback = {
                "item": item,
                "is_playing": True,
                "progress_ms": 0,
                "device": {"id": self.device_id, "name": "tspotify", "volume_percent": 0},
            }
            volume = getattr(self.backend, "preferred_volume", 0)
            if volume > 0 and not getattr(self.backend, "muted", True):
                self.control("volume", volume)
                self.last_playback["device"]["volume_percent"] = volume
            return "Воспроизводится: " + item.get("name", "трек")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            value = self.playback(force=True)
            if value.get("is_playing") and (value.get("item") or {}).get("id") == track_id:
                return "Воспроизводится: " + item.get("name", "трек")
            time.sleep(0.5)
        raise SpotifyError("Spotify принял запрос, но запуск выбранного трека не подтверждён.")

    def control(self, command, value=None):
        device = self.device_query()
        if command == "play-pause":
            command = "pause" if self.playback().get("is_playing") else "play"
        if command == "volume":
            value = max(0, min(100, int(value)))
            self.request("PUT", f"me/player/volume?volume_percent={value}&{device}")
            if self.backend:
                self.backend.preferred_volume = value
                if value > 0:
                    self.backend.muted = False
                self.backend.unmute_after_user_volume(value)
            return f"Громкость: {value}%"
        if command in ("next", "previous"):
            self.request("POST", f"me/player/{command}?{device}")
        elif command in ("play", "pause"):
            self.request("PUT", f"me/player/{command}?{device}")
        elif command == "shuffle":
            value = not self.playback().get("shuffle_state", False)
            self.request("PUT", f"me/player/shuffle?state={str(value).lower()}&{device}")
        elif command == "repeat":
            modes = ["off", "context", "track"]
            current = self.playback().get("repeat_state", "off")
            value = modes[(modes.index(current) + 1) % len(modes)]
            self.request("PUT", f"me/player/repeat?state={value}&{device}")
        else:
            raise ValueError("Неизвестная команда воспроизведения")
        self._last_remote_poll = 0
        return "Команда выполнена"

    def favorite(self, track_id, unlike=False):
        self.request("DELETE" if unlike else "PUT", "me/tracks?ids=" + quote(track_id, safe=""))

    def toggle_favorite(self, track_id):
        saved = self.request("GET", "me/tracks/contains?ids=" + quote(track_id, safe=""))
        unlike = bool(saved and saved[0])
        self.favorite(track_id, unlike=unlike)
        return not unlike

    def library(self, section):
        mapping = {
            "Плейлисты": ("playlist", "me/playlists?limit=50"),
            "Альбомы": ("album", "me/albums?limit=50"),
            "Любимые": ("track", "me/tracks?limit=50"),
        }
        kind, endpoint = mapping[section]
        target = paths.CACHE / f"library-{kind}.json"
        if target.exists() and time.time() - target.stat().st_mtime < 600:
            return section, json.loads(target.read_text(encoding="utf-8"))
        result = []
        page = self.request("GET", endpoint)
        while page:
            for entry in page.get("items", []):
                if not entry:
                    continue
                item = (
                    entry.get("album", entry)
                    if kind == "album"
                    else track_from_entry(entry)
                    if kind == "track"
                    else entry
                )
                if isinstance(item, dict) and item.get("id"):
                    result.append((kind, item))
            page = self.request("GET", page["next"]) if page.get("next") else None
        paths.private_json(target, result)
        return section, result

    def start_context(self, kind, item):
        value = self.context(kind, item["id"], item.get("name", ""))
        tracks = value["tracks"]
        if not tracks:
            raise SpotifyError("В этом плейлисте нет доступных треков")
        return self.start(tracks[0], value["context_uri"])

    def context(self, kind, item_id, name):
        target = paths.CACHE / f"{kind}-{item_id}.json"
        if target.exists() and time.time() - target.stat().st_mtime < 3600:
            value = json.loads(target.read_text(encoding="utf-8"))
            value["context_uri"] = f"spotify:{kind}:{item_id}"
            return value
        if kind == "playlist":
            page = self.request("GET", f"playlists/{item_id}/tracks?limit=100")
        elif kind == "album":
            page = self.request("GET", f"albums/{item_id}/tracks?limit=50")
        elif kind == "artist":
            data = self.request("GET", f"artists/{item_id}/top-tracks")
            value = {
                "name": name,
                "tracks": data.get("tracks", []),
                "context_uri": f"spotify:artist:{item_id}",
            }
            paths.private_json(target, value)
            return value
        else:
            raise SpotifyError("Этот раздел пока не поддерживает список треков")
        tracks = []
        while page:
            for entry in page.get("items", []):
                track = track_from_entry(entry)
                if track:
                    tracks.append(track)
            page = self.request("GET", page["next"]) if page.get("next") else None
        value = {"name": name, "tracks": tracks, "context_uri": f"spotify:{kind}:{item_id}"}
        paths.private_json(target, value)
        return value

    def search(self, query):
        data = self.request("GET", "search?q=" + quote(query) + "&type=track,album,artist,playlist&limit=10")
        return {key: value.get("items", []) for key, value in data.items() if isinstance(value, dict)}
