"""Opt-in real-account transport verification. Always silent; never saves preferences."""

import argparse
import json
import time

from tspotify import paths
from tspotify.api import Spotify, track_from_entry
from tspotify.backend import Backend
from tspotify.cli import single_instance

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--muted", action="store_true", required=True, help="confirm silent playback test")
parser.add_argument("--jam", action="store_true", help="also create a real Jam invitation (no messages sent)")
args = parser.parse_args()
preference = paths.CONFIG / "session.json"
before = preference.read_bytes() if preference.exists() else None
backend = Backend(muted=True)
api = Spotify()
api.session_muted = True
results = {}


def wait(predicate, label, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = backend.read_native_state()
        if predicate(state):
            return state
        time.sleep(0.04)
    # Do not print state: it can contain a private Jam invitation.
    raise AssertionError(label)


def duration(track):
    return track.get("duration_ms") or track.get("duration", {}).get("secs", 0) * 1000


with single_instance():
    try:
        backend.start()
        backend.observe_connect = False
        api.backend, api.device_id = backend, backend.device_id
        cache = json.loads((paths.BACKEND_CACHE / "SavedTracks_cache.json").read_text())
        tracks = [track_from_entry(x) for x in (cache.values() if isinstance(cache, dict) else cache)]
        tracks = [t for t in tracks if t and len(t["id"]) == 22][8:11]
        assert len(tracks) == 3 and all(duration(t) > 5000 for t in tracks), "Need three cached tracks"
        api.remember_tracks(tracks)
        uris = ["spotify:track:" + t["id"] for t in tracks]
        api.start({**tracks[0], "_queue_uris": uris})
        wait(
            lambda s: s.get("is_playing") and (s.get("next_uris") or [None])[0] == uris[1],
            "Actual next track",
        )
        results["next_label_matches_queue"] = True
        api.control("seek", duration(tracks[0]) - 1300)
        wait(
            lambda s: s.get("is_playing") and s.get("track_uri") == uris[1], "Natural transition at track end"
        )
        results["natural_transition"] = True
        api.control("next")
        wait(lambda s: s.get("is_playing") and s.get("track_uri") == uris[2], "Manual next")
        results["manual_next"] = True
        api.control("seek", duration(tracks[2]) - 1300)
        wait(lambda s: s.get("queue_ended") and not s.get("is_playing"), "Finite queue end")
        api.playback()
        api.control("play-pause")
        wait(
            lambda s: (
                s.get("is_playing") and s.get("track_uri") == uris[0] and s.get("position_ms", 99999) < 5000
            ),
            "Play restarts ended queue",
        )
        results["play_after_queue_end"] = True
        api.start({**tracks[2], "_queue_uris": uris})
        api.control("seek", duration(tracks[2]) - 1300)
        wait(lambda s: s.get("queue_ended") and not s.get("is_playing"), "Finite queue end again")
        api.control("next")
        state = wait(
            lambda s: s.get("is_playing") and s.get("track_uri") == uris[0], "Next restarts ended queue"
        )
        results["next_after_queue_end"] = True
        assert state.get("volume_percent", 0) == 0
        results["audio_volume"] = 0
        if args.jam:
            jam = api.start_jam()
            results["jam_real_invite_returned"] = bool(jam.get("url"))
            results["jam_members_count"] = len(jam.get("members") or [])
        api.control("pause")
    finally:
        backend.close()
        api.close()
        after = preference.read_bytes() if preference.exists() else None
        assert before == after, "Silent verification changed saved preferences"
        results["preferences_unchanged"] = True
print(json.dumps(results, indent=2))
