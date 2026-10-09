"""PBD Call Copilot - the window Andrew runs on his Windows PC.

    python app.py                 normal use
    python app.py --test-wav F    replay a recording as if live (any OS)

- Never listens on its own: it starts Off every time, and only Andrew turns it on,
  with the switch in the window or Ctrl + Alt + P. Switching off stops the
  microphone immediately, even while it is still starting up.
- The window is excluded from screen sharing on Windows 10 (2004) and later. On a Mac it
  asks not to be captured, but recent macOS versions don't always honor that, so the
  panel tells Andrew to share a single window instead of the whole screen.
- Audio is never saved; the transcript is saved to Documents/PBD Call Copilot.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path


def _unblock_own_files() -> None:
    """Files unzipped from a download carry Windows' "from the internet" mark, and .NET then
    refuses to load the window's Python.Runtime.dll. Clear the mark before the window loads."""
    root = getattr(sys, "_MEIPASS", None)
    if not (root and sys.platform.startswith("win")):
        return
    import os
    for folder, _, files in os.walk(os.path.dirname(root)):
        for name in files:
            try:
                os.remove(os.path.join(folder, name) + ":Zone.Identifier")
            except OSError:
                pass


_unblock_own_files()
import webview  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import copilot  # noqa: E402
from session import CallSession  # noqa: E402

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"



class Api:
    """Methods the panel can call (window.pywebview.api.*)."""

    def __init__(self, app: "CopilotApp"):
        self._app = app

    def toggle(self):
        self._app.toggle()

    def star(self, ask: str):
        self._app.starred.append(ask)

    def save_key(self, key: str) -> str:
        err = copilot.save_key(key)
        if not err:
            self._app.window.load_url(str(HERE / "ui" / "panel.html"))
        return err


class CopilotApp:
    def __init__(self, test_wav=None):
        self.test_wav = test_wav
        self.session = None
        self.window = None
        self.starred = []
        self.toggle_lock = threading.Lock()

    # -- events from the session -> the panel ----------------------------
    def emit(self, event: dict) -> None:
        if self.window:
            self.window.evaluate_js(f"window.onCopilotEvent({json.dumps(event)})")

    def toggle(self) -> None:
        """Only ever called by Andrew (switch or hotkey). On -> off works at any stage."""
        with self.toggle_lock:
            if self.session and self.session.running:
                self.session.stop()
                self.session = None
            elif copilot.has_key():   # not before first-run setup is done
                self.session = CallSession(self.emit, test_wav=self.test_wav)
                self.session.start()

    # -- Windows-only helpers ---------------------------------------------
    def hide_from_screen_share(self) -> None:
        if IS_MAC:
            from AppKit import NSApp
            from PyObjCTools import AppHelper

            def hide():
                for w in NSApp.windows():
                    if w.title() == "PBD Call Copilot":
                        w.setSharingType_(0)  # NSWindowSharingNone
            AppHelper.callAfter(hide)
            return
        if not IS_WINDOWS:
            return
        import ctypes
        WDA_EXCLUDEFROMCAPTURE = 0x11
        hwnd = ctypes.windll.user32.FindWindowW(None, "PBD Call Copilot")
        if hwnd:
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def hotkey(self) -> None:
        try:
            from pynput import keyboard
        except ImportError:
            return
        keyboard.GlobalHotKeys({"<ctrl>+<alt>+p": self.toggle}).run()

    def on_loaded(self) -> None:
        self.hide_from_screen_share()
        threading.Thread(target=self.hotkey, daemon=True).start()
        if self.test_wav:
            self.toggle()

    def run(self) -> None:
        page = "panel.html" if copilot.has_key() else "setup.html"   # first launch asks for the key
        self.window = webview.create_window(
            "PBD Call Copilot", str(HERE / "ui" / page), js_api=Api(self),
            width=760, height=1000, min_size=(480, 600), background_color="#061D28",
        )
        webview.start(self.on_loaded)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-wav", help="replay this .wav as if it were a live call")
    CopilotApp(ap.parse_args().test_wav).run()
