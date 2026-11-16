# Troubleshooting

**A command fails or stays pending.** Errors are shown in the status panel. Spotify may rate-limit
shared application IDs or reject a request. Respect the displayed wait. Check `tspotify devices`,
or use `--device integrated` to avoid selecting an unintended phone/speaker.

**The engine cannot load audio.** Spotify's audio server may be unreachable even while the Web API works.
Check your network and Premium account; use an already-running official Spotify Connect device if available.
Do not confuse an accepted Web API request with confirmed decoded playback.

**No music after muted testing.** `--muted` starts at zero. Raise volume deliberately with `+`.
The Linux stream mute left by a zero-volume test is cleared only by an explicit positive-volume action.

**Dots are missing / squares appear.** Use a Unicode monospace font or `tspotify --ascii`.
No private-use icon font is needed. Windows Terminal is recommended over legacy conhost.

**No spectrum on macOS.** Install/configure an output loopback such as BlackHole, or hide the spectrum with
`L`, then `v`. Playback is independent. Use `TSPOTIFY_LOOPBACK` to select a configured capture input.

**QR callback fails on a phone.** The callback is a loopback address on the computer. Copy the complete
callback address from the phone and paste it in the QR sign-in prompt. Do not send it to anyone.

**Wrong redirect address.** Re-run `tspotify login`. The Web API uses `/ncspot_login`; the separate streaming
identity uses `/login`. A client ID must match the registered callback.

**You closed tspotify but hear music.** The current version stops its integrated engine on every exit.
A separately running desktop Spotify or another Connect device is independent; stop it in that app.
An older prototype process can be identified with your OS process viewer. `tspotify doctor` shows paths,
not credentials.

**Unsigned downloaded binary.** macOS/Windows may request OS approval. The installer never disables
Gatekeeper, SmartScreen, certificate validation or system protections.

