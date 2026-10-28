"""Checks for kbcrypt.py.  python tests/test_kbcrypt.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kbcrypt  # noqa: E402

TEXT = "# PBD Project knowledge base\n### Ada Example — lab head\n" + "x" * 5000
KEY = "sk-ant-test-key-not-real"
failed = 0


def check(name, ok):
    global failed
    failed += not ok
    print(("PASS  " if ok else "FAIL  ") + name)


def raises(f):
    try:
        f()
        return False
    except ValueError:
        return True


blob = kbcrypt.lock(TEXT, KEY)
check("right key unlocks it", kbcrypt.unlock(blob, KEY) == TEXT)
check("text is not readable in the file", b"Ada Example" not in blob and b"knowledge" not in blob)
check("wrong key is refused", raises(lambda: kbcrypt.unlock(blob, KEY + "x")))
tampered = bytearray(blob); tampered[50] ^= 1
check("a changed file is refused", raises(lambda: kbcrypt.unlock(bytes(tampered), KEY)))
check("two locks of the same text differ", kbcrypt.lock(TEXT, KEY) != blob)
check("junk is refused", raises(lambda: kbcrypt.unlock(b"hello", KEY)))
print(f"\n{6 - failed}/6 checks passed")
sys.exit(1 if failed else 0)
