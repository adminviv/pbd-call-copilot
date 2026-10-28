"""In-app updates: "Update available" -> one click -> the new version replaces this one.

Each published release carries version.txt ("1.0.<n>") and checksums.txt (SHA-256 of
every download). The app compares versions, downloads the file for this computer, checks
its SHA-256, then hands over to a tiny script that waits for the app to close, puts the
new version in place (Windows: the installer, same folder; Mac: swaps the .app) and
opens it again. Never two copies.
"""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional

import net

log = logging.getLogger("copilot")
REPO = "adminviv/pbd-call-copilot"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
FOLDER = Path.home() / ".pbd-copilot" / "update"


def asset_name() -> str:
    if sys.platform.startswith("win"):
        return "PBD-Call-Copilot-Setup.exe"
    return "PBD-Call-Copilot-Mac-AppleSilicon.zip" if platform.machine() == "arm64" else "PBD-Call-Copilot-Mac-Intel.zip"


def _number(version: str) -> int:
    m = re.fullmatch(r"\s*1\.0\.(\d+)\s*", version or "")
    return int(m.group(1)) if m else -1


def _text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "PBD-Call-Copilot"})
    with urllib.request.urlopen(req, timeout=20, context=net._CONTEXT) as r:
        return r.read().decode("utf-8", "replace")


def check(current: str) -> Optional[Dict]:
    """The newer release for this computer, or None (also None for source/dev runs)."""
    if _number(current) < 0 or not getattr(sys, "frozen", False):
        return None
    release = net.get(LATEST, timeout=20)
    files = {a["name"]: a for a in release.get("assets", [])}
    if "version.txt" not in files or asset_name() not in files:
        return None
    latest = _text(files["version.txt"]["browser_download_url"]).strip()
    if _number(latest) <= _number(current):
        return None
    sums = _text(files["checksums.txt"]["browser_download_url"]) if "checksums.txt" in files else ""
    sha = next((l.split()[0] for l in sums.splitlines() if l.strip().endswith(asset_name())), "")
    a = files[asset_name()]
    return {"version": latest, "url": a["browser_download_url"], "size": a["size"], "sha256": sha}


def download(update: Dict, progress: Callable[[float], None]) -> Path:
    FOLDER.mkdir(parents=True, exist_ok=True)
    target = FOLDER / asset_name()
    digest, done = hashlib.sha256(), 0
    req = urllib.request.Request(update["url"], headers={"User-Agent": "PBD-Call-Copilot"})
    with urllib.request.urlopen(req, timeout=60, context=net._CONTEXT) as r, open(target, "wb") as f:
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            digest.update(block)
            done += len(block)
            progress(min(1.0, done / max(1, update["size"])))
    if not update["sha256"] or digest.hexdigest() != update["sha256"]:
        target.unlink(missing_ok=True)
        raise ValueError("the download didn't match the published checksum")
    return target


def install_and_restart(package: Path) -> None:
    """Start the hand-over script, then the caller must quit the app at once."""
    pid = os.getpid()
    if sys.platform.startswith("win"):
        exe = sys.executable   # ...\PBD Call Copilot\PBD Call Copilot.exe
        script = Path(tempfile.gettempdir()) / "pbd-copilot-update.cmd"
        script.write_text(
            "@echo off\r\n"
            f":wait\r\ntasklist /FI \"PID eq {pid}\" | find \"{pid}\" >nul && (timeout /t 1 >nul & goto wait)\r\n"
            f"\"{package}\" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n"
            f"start \"\" \"{exe}\"\r\n", encoding="ascii")
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=0x08000000 | 0x00000008)  # no window, detached
        return
    app = Path(sys.executable).resolve().parents[2]          # .../PBD Call Copilot.app
    staging = FOLDER / "new"
    script = FOLDER / "install.sh"
    script.write_text(
        "#!/bin/bash\n"
        f"while kill -0 {pid} 2>/dev/null; do sleep 1; done\n"
        f"rm -rf '{staging}' && mkdir -p '{staging}' && ditto -x -k '{package}' '{staging}' || exit 1\n"
        f"NEW=$(ls -d '{staging}'/*.app | head -1); [ -d \"$NEW\" ] || exit 1\n"
        f"rm -rf '{app}' && mv \"$NEW\" '{app}' && xattr -dr com.apple.quarantine '{app}' 2>/dev/null\n"
        f"rm -f '{package}'; open '{app}'\n")
    script.chmod(0o700)
    subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
