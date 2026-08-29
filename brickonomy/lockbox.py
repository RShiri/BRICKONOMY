"""Password-protect a page in the static export.

GitHub Pages serves files, nothing else, and this repository is public — so a
JavaScript "enter the password" prompt would protect nothing at all. Anyone can
read the served HTML, or the same file in the repo, without ever meeting the
prompt.

So the page is genuinely encrypted instead. What gets published is ciphertext:
AES-256-GCM, with the key derived from the password by PBKDF2-HMAC-SHA256.
Without the password the file is noise, whoever downloads it. The browser
derives the same key with WebCrypto and decrypts in the page.

The password is never written to disk, never committed, and is read from
BRICKONOMY_PORTFOLIO_PASSWORD at export time.

Caveats worth knowing:
  * The strength of this is the strength of the password. PBKDF2 at 310k
    iterations makes guessing expensive, not impossible — a dictionary word is
    still a dictionary word.
  * Whoever has the password can, of course, save the decrypted page.
  * Changing the password means re-running the export; old published files stay
    readable with the old one until overwritten.
"""
import base64
import json
import os
import secrets

PBKDF2_ITERATIONS = 310_000        # OWASP's 2023 floor for PBKDF2-HMAC-SHA256
ENV_VAR = "BRICKONOMY_PORTFOLIO_PASSWORD"


def get_password():
    """The configured password, or None when protection is not enabled."""
    return os.environ.get(ENV_VAR) or None


def encrypt(payload: dict, password: str) -> dict:
    """Encrypt a JSON-serialisable payload. Returns the fields the page needs.

    Salt and nonce are fresh per export, so re-exporting the same portfolio
    does not produce the same ciphertext.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=PBKDF2_ITERATIONS).derive(password.encode("utf-8"))
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)

    b64 = lambda raw: base64.b64encode(raw).decode("ascii")
    return {"salt": b64(salt), "nonce": b64(nonce), "ciphertext": b64(ciphertext),
            "iterations": PBKDF2_ITERATIONS}


def decrypt(blob: dict, password: str) -> dict:
    """Inverse of `encrypt`, so a test can prove the two agree. Raises on a
    wrong password (AES-GCM authenticates, it does not return garbage)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    raw = lambda s: base64.b64decode(s)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=raw(blob["salt"]),
                     iterations=blob["iterations"]).derive(password.encode("utf-8"))
    plaintext = AESGCM(key).decrypt(raw(blob["nonce"]), raw(blob["ciphertext"]), None)
    return json.loads(plaintext)
