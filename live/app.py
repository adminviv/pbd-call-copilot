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
import os
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


def _start_log() -> None:
    """A small log on this computer (~/.pbd-copilot/app.log) so failures are never silent.
    It records what happened and every error, never the words of a call."""
    import logging
    import logging.handlers
    import os
    folder = Path.home() / ".pbd-copilot"
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / "app.log", maxBytes=1_000_000, backupCount=1,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # Packaged windowed apps can have no console: printing must never crash a thread.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    sys.excepthook = lambda t, v, tb: logging.critical("crash", exc_info=(t, v, tb))
    threading.excepthook = lambda a: logging.error("background task failed: %s", a.thread.name if a.thread else "",
                                                   exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


_start_log()
import logging  # noqa: E402
log = logging.getLogger("copilot")
import webview  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import copilot  # noqa: E402
import gcal  # noqa: E402
from session import CallSession  # noqa: E402

APP_VERSION = "dev"   # the release build writes the real number here
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

    def sign_in(self) -> dict:
        """Google sign-in (calendar, read-only) in the browser; returns the result for the panel."""
        try:
            email = gcal.sign_in()
        except Exception as e:
            return {"ok": False, "message": f"Sign-in didn't finish: {e}"}
        self._app.refresh_meetings()
        return {"ok": True, "email": email}

    def sign_out(self) -> None:
        gcal.sign_out()
        self._app.refresh_meetings()

    def refresh_meetings(self) -> None:
        self._app.refresh_meetings()

    def save_key(self, key: str) -> str:
        err = copilot.save_key(key)
        if not err:
            self._app.window.load_url(str(HERE / "ui" / "panel.html"))
        return err


class CopilotApp:
    def __init__(self, test_wav=None, report=None, offline=False):
        self.test_wav = test_wav
        self.report = report          # self-test: write every event here, then quit
        self.offline = offline
        self.recorded = []
        self.session = None
        self.window = None
        self.starred = []
        self.toggle_lock = threading.Lock()
        self.meetings = []            # from the signed-in Google calendar(s)
        self.meetings_lock = threading.Lock()
        self.ui_lock = threading.Lock()
        self.ui_ready = False
        self.ui_backlog = []

    # -- events from the session -> the panel ----------------------------
    def emit(self, event: dict) -> None:
        kind = event.get("type")
        if kind in ("status", "error", "warning", "ok"):
            log.info("%s %s", kind, event.get("state") or event.get("message", ""))
        elif kind == "meetings":
            log.info("meetings: signed_in=%s count=%d active=%s error=%s", bool(event.get("account")),
                     len(event.get("items", [])), bool(event.get("active")), event.get("error", ""))
        elif kind == "suggestions":
            log.info("suggestions: %d card(s)", len(event.get("cards", [])))
        if self.report:
            self.recorded.append({"at": time.time(), "phase": self.phase, **event})
            if event.get("type") == "status" and event.get("state") == "off" and self.phase == "run":
                threading.Thread(target=self._finish_self_test, daemon=True).start()
        if not self.window:
            return
        with self.ui_lock:
            if not self.ui_ready:          # window still loading: show it as soon as it can
                self.ui_backlog.append(event)
                return
        self._show(event)

    def _show(self, event: dict) -> None:
        try:
            self.window.evaluate_js(f"window.onCopilotEvent({json.dumps(event)})")
        except Exception:
            pass   # a screen hiccup must never stop the listening itself

    def _page_loaded(self) -> None:
        with self.ui_lock:
            self.ui_ready = True
            backlog, self.ui_backlog = self.ui_backlog, []
        for event in backlog:
            if event.get("type") != "level":   # old sound levels are meaningless now
                self._show(event)

    def toggle(self) -> None:
        """Only ever called by Andrew (switch or hotkey). On -> off works at any stage."""
        log.info("switch pressed (currently %s)", "on" if self.session and self.session.running else "off")
        with self.toggle_lock:
            if self.session and self.session.running:
                self.session.stop()
                self.session = None
            elif not (copilot.has_key() or self.offline):
                # never fail silently: say why nothing happens
                self.emit({"type": "error", "message":
                           "There is no PBD key on this computer, so it can't start. Ask Genevieve for the key."})
            elif not self.test_wav and not self.current_meeting():
                self.emit({"type": "error", "message": self.why_no_meeting()})
            else:
                meeting = gcal.meeting_context(self.current_meeting()) if not self.test_wav else ""
                self.session = CallSession(self.emit, test_wav=self.test_wav, offline=self.offline,
                                           meeting=meeting)
                self.session.start()
                if self.report and not hasattr(self, "first_session_started"):
                    self.first_session_started = self.session.started

    # -- calendar: the switch only works during a confirmed meeting ----------
    def current_meeting(self):
        with self.meetings_lock:
            return gcal.active_meeting(self.meetings)

    def why_no_meeting(self) -> str:
        if not gcal.client_config():
            return "Calendar sign-in isn't set up in this version of the app. Ask Genevieve for the latest version."
        if not gcal.TOKEN.exists():
            return "Sign in with Google first, so the Copilot can see your meetings."
        return ("No confirmed meeting right now. The Copilot works during confirmed meetings on your "
                "calendar, from 10 minutes before the start to 15 minutes after the end.")

    def refresh_meetings(self) -> None:
        """Reload the calendar and tell the panel; switch off if the meeting has ended."""
        try:
            email = gcal.signed_in_email()
            items = gcal.fetch_events() if email else []
            error = ""
        except Exception as e:
            log.exception("loading the calendar failed")
            email, items, error = None, [], f"Couldn't load your calendar: {type(e).__name__}"
        with self.meetings_lock:
            self.meetings = items
        active = self.current_meeting()
        self.emit({"type": "meetings", "configured": bool(gcal.client_config()), "account": email or "",
                   "items": items, "active": active["id"] if active else "", "error": error})
        if not self.test_wav and self.session and self.session.running and not active:
            self.toggle()   # the confirmed meeting's window closed: stop listening
            self.emit({"type": "warning", "message": "The meeting has ended, so the Copilot switched off."})

    def watch_calendar(self) -> None:
        while True:
            self.refresh_meetings()
            time.sleep(60)

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
        log.info("window ready (version %s)", APP_VERSION)
        self.emit({"type": "version", "version": APP_VERSION})
        # each start-up step on its own, so one failing can't silently stop the others
        for step in (self.hide_from_screen_share,
                     lambda: threading.Thread(target=self.hotkey, name="hotkey", daemon=True).start(),
                     lambda: None if self.test_wav else
                     threading.Thread(target=self.watch_calendar, name="calendar", daemon=True).start()):
            try:
                step()
            except Exception:
                log.exception("start-up step failed")
        if self.report:
            guard = threading.Timer(900, self._finish_self_test, kwargs={"timed_out": True})
            guard.daemon = True   # must not keep the app open after the test
            guard.start()
        if self.report:
            threading.Thread(target=self._report_connections, name="netcheck", daemon=True).start()
        if self.test_wav:
            self.started_at = time.time()
            self.toggle()

    # -- self-test (run by the release build on a real Windows/Mac machine) --
    def _report_connections(self) -> None:
        """Prove the PACKAGED app can make every secure connection it needs, and has its
        Google sign-in client. (A packaging fault once broke all Google calls silently.)"""
        import net
        result = net.check()
        result["calendar_client"] = bool(gcal.client_config())
        self.emit({"type": "netcheck", **result})

    phase = "run"

    def _finish_self_test(self, timed_out: bool = False) -> None:
        """After the test call: check that switching off mid-start really stops
        everything, then write the report and close the app."""
        if self.phase != "run":
            return
        self.phase = "off-check"
        if not timed_out:
            self.session = None
            self.toggle()            # on ...
            time.sleep(0.05)
            self.toggle()            # ... and straight off again, while it is still starting
            time.sleep(20)           # nothing may appear after the Off
        Path(self.report).write_text(json.dumps({
            "timed_out": timed_out, "offline": self.offline, "started_at": self.started_at,
            "session_started": getattr(self, "first_session_started", None), "events": self.recorded,
        }, indent=1))
        self.window.destroy()
        time.sleep(1)
        os._exit(0)   # self-test only: quit even if a background thread is still busy

    def run(self) -> None:
        page = "panel.html" if (copilot.has_key() or self.offline) else "setup.html"   # first launch asks for the key
        self.window = webview.create_window(
            "PBD Call Copilot", str(HERE / "ui" / page), js_api=Api(self),
            width=760, height=1000, min_size=(480, 600), background_color="#061D28",
        )
        self.window.events.loaded += self._page_loaded
        webview.start(self.on_loaded)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-wav", help="replay this .wav as if it were a live call")
    ap.add_argument("--self-test", metavar="REPORT", help="with --test-wav: record all events to REPORT and quit")
    ap.add_argument("--no-claude", action="store_true", help="self-test audio and speech-to-text only")
    a = ap.parse_args()
    CopilotApp(a.test_wav, report=a.self_test, offline=a.no_claude).run()
