"""Optional KDE global F7-F12 bindings, active only while the client is running."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from . import paths

COMPONENTS = {"volume-down": (7, 9, 11), "volume-up": (8, 10, 12)}


def call(method, *args):
    result = subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.kde.kglobalaccel",
            "--object-path",
            "/kglobalaccel",
            "--method",
            "org.kde.KGlobalAccel." + method,
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode:
        raise RuntimeError("KDE не принял сочетания клавиш: " + result.stderr[:160])
    return result.stdout


def enabled():
    try:
        return bool(
            json.loads((paths.CONFIG / "global-shortcuts.json").read_text(encoding="utf-8")).get("enabled")
        )
    except (OSError, ValueError, AttributeError):
        return False


def set_active(active):
    if not shutil.which("gdbus"):
        return False
    if active:
        for action, functions in COMPONENTS.items():
            component = f"tspotify-{action}.desktop"
            for number in functions:
                code = 0x01000030 + number - 1
                available = "true" in call("isGlobalShortcutAvailable", str(code), component).lower()
                owner = call("action", str(code)) if not available else ""
                if not available and (f"'{component}'" not in owner or "'_launch'" not in owner):
                    raise RuntimeError(f"F{number} уже используется другим системным сочетанием")
    for action, functions in COMPONENTS.items():
        component = f"tspotify-{action}.desktop"
        identity = json.dumps([component, "_launch", "tspotify", action])
        if active:
            call("doRegister", identity)
        keys = [0x01000030 + number - 1 for number in functions] if active else []
        # KGlobalAccel: SetPresent=2, NoAutoloading=4. Never steal conflicts.
        call("setShortcut", identity, json.dumps(keys), "6")
    return True


def install(remove=False):
    if remove:
        set_active(False)
        paths.private_json(paths.CONFIG / "global-shortcuts.json", {"enabled": False})
        return
    if os.name == "nt" or not shutil.which("gdbus"):
        raise RuntimeError(
            "Автоматическая настройка доступна в KDE; для других ОС используйте tspotify control"
        )
    call("isGlobalShortcutAvailable", str(0x01000036), "tspotify-volume-down.desktop")
    executable = shutil.which("tspotify")
    if not executable:
        raise RuntimeError("Команда tspotify должна быть в PATH")
    directory = Path.home() / ".local/share/applications"
    directory.mkdir(parents=True, exist_ok=True)
    for action in COMPONENTS:
        desktop = directory / f"tspotify-{action}.desktop"
        quoted = (
            executable.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("%", "%%")
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=tspotify "
            + action
            + "\n"
            + f'Exec="{quoted}" control {action}\n'
            + "NoDisplay=true\nTerminal=false\nStartupNotify=false\nX-KDE-GlobalAccel-CommandShortcut=true\n",
            encoding="utf-8",
        )
    builder = shutil.which("kbuildsycoca6")
    if builder:
        subprocess.run(
            [builder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True
        )
    paths.private_json(paths.CONFIG / "global-shortcuts.json", {"enabled": True})
    # Existing older processes have no control endpoint: register on next startup.
    if (paths.DATA / "control.json").exists():
        set_active(True)
