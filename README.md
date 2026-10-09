# PBD Call Copilot

A private window that sits beside a Zoom call on Windows. It listens to both sides, turns speech into text on the PC, and every ~75 seconds suggests up to two things to ask next. Each suggestion comes in plain words with what it means for PBD and a verified link to the paper behind it. It never joins the call, and it is hidden from screen sharing.

Download page: https://copilot.pbdproject.org

## How it works

| Step | Where | Code |
|---|---|---|
| Capture both sides of the call (speaker loopback + mic) | PC | `live/audio.py` |
| Speech to text with a science vocabulary (faster-whisper) | PC | `live/transcriber.py` |
| Topic → search the paper corpus → write cards → check claims | Claude | `copilot.py`, `knowledge.py` |
| Check every cited paper exists (NCBI) | NCBI | `verify.py` |
| Panel window, on/off switch, Ctrl+Alt+P (never starts on its own) | PC | `live/app.py`, `live/ui/` |

Audio is never saved. The transcript is saved to `Documents\PBD Call Copilot\transcripts`.

## Private files (never in this repo)

- `%USERPROFILE%\.pbd.env`: the PBD API key. The app asks for it on first launch.
- `%USERPROFILE%\.pbd-copilot\profile.md`: the private briefing about the CEO and PBD's interests.
- `%USERPROFILE%\pbd-corpus-repo\`: the paper indexes (PeroxiOS, PEX10). These are internal; set `COPILOT_DATA` to use another folder. A missing source is skipped.

## Run from source

```
pip install -r live/requirements.txt
python live/app.py                       # normal use (Windows)
python live/app.py --test-wav call.wav   # replay a recording as if live
python live/test_headless.py call.wav    # same, no window, timings printed
```

## Build

Every push to `main` builds the Windows app on GitHub Actions (`.github/workflows/windows-build.yml`) and publishes `PBD-Call-Copilot-Windows.zip` as the latest release. The download page in `docs/` links to it.
