"""Lock the private knowledge base with the PBD key, for the app build.

    python3 tools/lock_knowledge.py | gh secret set PBD_KNOWLEDGE_LOCKED

Reads ~/.pbd-copilot/knowledge.md and the PBD key from ~/.pbd.env (never printed);
prints only the locked file, base64-encoded.
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kbcrypt  # noqa: E402
import orgkb  # noqa: E402

key = orgkb._pbd_key()
if not key:
    sys.exit("No PBD key in ~/.pbd.env")
text = (Path.home() / ".pbd-copilot" / "knowledge.md").read_text(encoding="utf-8")
blob = kbcrypt.lock(text, key)
assert kbcrypt.unlock(blob, key) == text
sys.stdout.write(base64.b64encode(blob).decode())
