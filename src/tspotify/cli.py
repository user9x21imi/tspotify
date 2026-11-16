"""Portable entry point, first-run login and lifecycle ownership."""

import argparse
import contextlib
import locale
import os
import platform
import shutil
import sys
import webbrowser
from urllib.parse import urlsplit

from . import __version__, auth, paths
from .api import Spotify, SpotifyError
from .backend import Backend, engine_path


@contextlib.contextmanager
def single_instance():
    path = paths.DATA / "client.lock"
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("tspotify уже открыт. Вернитесь в существующее окно.") from None
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("tspotify уже открыт. Вернитесь в существующее окно.") from None
        yield
    finally:
        stream.close()


def engine_authorize(url, qr=False):
    print("\nSpotify просит отдельно разрешить встроенное воспроизведение.", flush=True)
    if qr:
        import httpx
        import qrcode

        image = qrcode.QRCode(border=2)
        image.add_data(url)
        image.make(fit=True)
        image.print_ascii(invert=True)
        pasted = input("После входа на телефоне вставьте полный адрес возврата: ").strip()
        parsed = urlsplit(pasted)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 8989:
            raise ValueError("Ожидается адрес возврата http://127.0.0.1:8989/…")
        httpx.get(pasted, timeout=5)
    else:
        print("Если браузер не открылся, перейдите по этой ссылке:\n" + url, flush=True)
        webbrowser.open(url, new=2)


def doctor():
    print("+-- tspotify doctor -----------------------------------------+")
    print("Version:", __version__)
    print("OS:", platform.system(), platform.machine())
    print("Python:", platform.python_version())
    print("Terminal:", os.environ.get("TERM", "Windows console"))
    print("Encoding:", sys.stdout.encoding)
    print("Engine:", engine_path(install=False) or "not installed; first launch downloads it")
    print("Data:", paths.DATA)
    print("Config:", paths.CONFIG)
    print("Login:", "saved" if paths.TOKEN_FILE.exists() else "required")
    print(
        "Spectrum:",
        "PulseAudio/PipeWire"
        if shutil.which("parec")
        else "WASAPI loopback"
        if os.name == "nt"
        else "optional loopback input (e.g. BlackHole)",
    )
    print("Fonts: use your normal monospace font; --ascii disables Unicode artwork.")


def parser():
    value = argparse.ArgumentParser(prog="tspotify", description="Spotify in your terminal, drawn with dots.")
    value.add_argument("--version", action="version", version="tspotify " + __version__)
    value.add_argument("--ascii", action="store_true", help="portable ASCII artwork and borders")
    value.add_argument("--no-splash", action="store_true", help="skip the two-second dotted splash")
    value.add_argument("--muted", action="store_true", help="start the integrated engine at zero volume")
    value.add_argument(
        "--device", default="auto", help="auto, integrated, or a Spotify Connect device name/ID"
    )
    sub = value.add_subparsers(dest="command")
    login_parser = sub.add_parser("login", help="authorize Spotify in a browser or via QR")
    login_parser.add_argument(
        "--qr", action="store_true", help="show QR; paste the callback URL from your phone"
    )
    sub.add_parser("doctor", help="show diagnostics without printing tokens")
    sub.add_parser("devices", help="list available Spotify Connect devices")
    sub.add_parser("install-engine", help="download and verify the pinned streaming engine")
    return value


def run(args):
    paths.prepare()
    if args.command == "doctor":
        doctor()
        return
    if args.command == "install-engine":
        print(engine_path())
        return
    if args.command == "login":
        auth.login(qr=args.qr)
        return
    try:
        auth.access_token()
    except auth.LoginRequired:
        auth.login()
    api = Spotify()
    backend = None
    try:
        if args.command == "devices":
            for device in api.devices():
                print(("* " if device.get("is_active") else "  ") + device["name"] + "  " + device["id"])
            return
        with single_instance():
            try:
                selected = api.select_device(args.device)
            except (SpotifyError, OSError):
                if args.device not in ("auto", "integrated"):
                    raise
                selected = None
            if selected:
                print("Spotify Connect:", selected["name"], flush=True)
            if not api.external:
                backend = Backend(muted=args.muted, on_authorize=engine_authorize)
                api.backend = backend
                print("Подключаю Spotify. Первый запуск может занять несколько секунд…", flush=True)
                backend.start()
            # Import curses only for the UI: doctor, installation and OAuth work without a TTY.
            import curses
            from . import ui

            ui.API = api
            ui.ASCII_ONLY = args.ascii
            ui.NO_SPLASH = args.no_splash
            locale.setlocale(locale.LC_ALL, "")
            curses.wrapper(lambda screen: ui.App(screen).run())
    finally:
        # Stop our own stream even when an exception or Ctrl+C closes the interface.
        # An already-running desktop Spotify is a separate, user-owned device.
        if backend:
            backend.close()
        api.close()


def main(argv=None):
    os.umask(0o077)
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    try:
        run(args)
    except KeyboardInterrupt:
        print("\ntspotify закрыт.")
    except (RuntimeError, OSError, ValueError) as error:
        print("\ntspotify: " + str(error), file=sys.stderr)
        print("Диагностика: tspotify doctor", file=sys.stderr)
        raise SystemExit(1) from None
