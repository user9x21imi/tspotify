# Changelog

## 0.1.0

- Numbered discovery sections: recent contexts/tracks, saved Spotify mixes, new releases, recommendations and saved daily mixes.
- Mouse panel focus, clickable tabs and controls, list selection/double-click and wheel scrolling.
- F3 expanded player with detailed 256×256 artwork and a smooth dotted audio spectrum.
- Re-activate registered Connect devices before retrying 404; recover stopped engines and failed controls, including automatic-device fallback.
- Stable release update checks with explicit confirmation and checksum verification.

- Colored dotted cover art, matching dotted splash, and small playlist/album covers.
- Framed terminal layout, inherited background/transparency and a plain-ASCII fallback.
- Search, playlists, albums, liked tracks, favorite toggling, volume/mute, shuffle and repeat.
- Enter opens a playlist; Enter on a track starts it in its original context.
- Playing is confirmed by a real engine event or remote Spotify state; errors are not success messages.
- Smooth real-audio FFT spectrum with separate attack/release interpolation.
- Persistent panel ordering and cover-side controls.
- Browser PKCE authorization and QR/manual-callback sign-in.
- Automatic selection of an available, already logged-in Spotify Connect device.
- Foreground-owned integrated engine, zero-volume test mode and shutdown on exit.
- Native release builds for Linux x64, macOS arm64/x64 and Windows x64.
- Per-user install scripts, checksum-verified engine downloads, CI tests and private credential storage.

