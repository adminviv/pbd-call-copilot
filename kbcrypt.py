"""Lock/unlock PBD's private knowledge base with the PBD key, so it can travel inside
the public app download without anyone else being able to read it.

Standard-library only (the `cryptography` package broke the packaged Mac app before):
- key: PBKDF2-HMAC-SHA256 of the PBD key, 200,000 rounds, random salt -> 32-byte
  encryption key + 32-byte MAC key
- encryption: HMAC-SHA256 in counter mode as the keystream (a PRF-based stream cipher)
- integrity: HMAC-SHA256 over everything (encrypt-then-MAC), checked before decrypting

File layout: b"PBDKB1" | salt (16) | nonce (16) | ciphertext | tag (32)
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import os

MAGIC = b"PBDKB1"
ROUNDS = 200_000


def _keys(secret: str, salt: bytes) -> tuple:
    k = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, ROUNDS, dklen=64)
    return k[:32], k[32:]


def _stream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for block in range(0, len(data), 32):
        pad = hmac.new(key, nonce + (block // 32).to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[block:block + 32]
        out[block:block + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, pad))
    return bytes(out)


def lock(text: str, secret: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(16)
    enc, mac = _keys(secret, salt)
    body = MAGIC + salt + nonce + _stream(enc, nonce, gzip.compress(text.encode("utf-8"), 9))
    return body + hmac.new(mac, body, hashlib.sha256).digest()


def unlock(blob: bytes, secret: str) -> str:
    """The text, or ValueError if the key is wrong or the file was changed."""
    if len(blob) < len(MAGIC) + 64 or not blob.startswith(MAGIC):
        raise ValueError("not a locked knowledge base")
    body, tag = blob[:-32], blob[-32:]
    salt, nonce = body[6:22], body[22:38]
    enc, mac = _keys(secret, salt)
    if not hmac.compare_digest(tag, hmac.new(mac, body, hashlib.sha256).digest()):
        raise ValueError("wrong key, or the file was changed")
    return gzip.decompress(_stream(enc, nonce, body[38:])).decode("utf-8")
