"""Authenticated localhost commands for desktop shortcuts; only forwards explicit user actions."""

import hmac
import json
import os
import secrets
import socket
import socketserver
import threading

from . import paths

ACTIONS = ("previous", "play-pause", "next", "mute", "volume-down", "volume-up")


class ControlServer:
    def __init__(self, callback):
        self.token = secrets.token_hex(32)
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(1)
                accepted = False
                try:
                    data = json.loads(self.rfile.readline(4097))
                    action = data.get("action")
                    token = data.get("token")
                    if (
                        isinstance(token, str)
                        and hmac.compare_digest(token, owner.token)
                        and action in ACTIONS
                    ):
                        callback(action)
                        accepted = True
                except (OSError, ValueError, AttributeError, TypeError):
                    pass
                try:
                    self.wfile.write(json.dumps({"ok": accepted}).encode() + b"\n")
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        paths.prepare()
        self.metadata = paths.DATA / "control.json"
        paths.private_json(
            self.metadata,
            {
                "port": self.server.server_address[1],
                "pid": os.getpid(),
                "token": self.token,
                "actions": list(ACTIONS),
            },
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.1),
            daemon=True,
            name="tspotify-desktop-controls",
        )
        self.thread.start()
        self.shortcuts_active = False
        self.shortcut_error = ""
        from . import shortcuts

        if shortcuts.enabled():
            try:
                self.shortcuts_active = shortcuts.set_active(True)
            except (RuntimeError, OSError) as error:
                self.shortcut_error = str(error)

    def close(self):
        if self.shortcuts_active:
            from . import shortcuts

            try:
                shortcuts.set_active(False)
            except (OSError, RuntimeError):
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        try:
            data = json.loads(self.metadata.read_text(encoding="utf-8"))
            if data.get("token") == self.token:
                self.metadata.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def send(action):
    if action not in ACTIONS:
        raise ValueError("Неизвестная команда управления")
    try:
        data = json.loads((paths.DATA / "control.json").read_text(encoding="utf-8"))
        port = data["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("port")
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(json.dumps({"action": action, "token": data["token"]}).encode() + b"\n")
            result = json.loads(connection.makefile("rb").readline(4097))
        if not result.get("ok"):
            raise ValueError("rejected")
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("tspotify не запущен или ещё не готов к управлению") from None
