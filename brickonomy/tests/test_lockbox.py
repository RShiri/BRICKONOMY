"""The portfolio must never reach a public Pages site in the clear."""
import base64

import pytest

from brickonomy import lockbox

SECRET = {"html": "<table>76210 Hulkbuster paid 328.00</table>",
          "history": [{"t": "2026-08-01", "v": 15502.0}]}


class TestLockbox:
    def test_round_trip(self):
        blob = lockbox.encrypt(SECRET, "correct horse battery staple")
        assert lockbox.decrypt(blob, "correct horse battery staple") == SECRET

    def test_a_wrong_password_fails_rather_than_returning_rubbish(self):
        """AES-GCM authenticates, so the page can tell 'wrong password' from
        'decrypted into nonsense' — the UI depends on that."""
        blob = lockbox.encrypt(SECRET, "right")
        with pytest.raises(Exception):
            lockbox.decrypt(blob, "wrong")

    def test_the_plaintext_is_not_recoverable_from_the_blob(self):
        blob = lockbox.encrypt(SECRET, "pw")
        raw = base64.b64decode(blob["ciphertext"])
        for giveaway in (b"Hulkbuster", b"328", b"15502"):
            assert giveaway not in raw
        assert "Hulkbuster" not in str(blob)

    def test_each_export_uses_a_fresh_salt_and_nonce(self):
        """Re-exporting the same portfolio must not produce the same file, or
        the diff alone leaks whether anything changed."""
        a = lockbox.encrypt(SECRET, "pw")
        b = lockbox.encrypt(SECRET, "pw")
        assert a["salt"] != b["salt"]
        assert a["nonce"] != b["nonce"]
        assert a["ciphertext"] != b["ciphertext"]
        assert lockbox.decrypt(b, "pw") == SECRET

    def test_iterations_meet_the_owasp_floor(self):
        assert lockbox.encrypt(SECRET, "pw")["iterations"] >= 310_000


class TestExportRefusesToLeak:
    def test_without_a_password_the_page_is_an_empty_stub(
            self, monkeypatch, tmp_path):
        """The fail-safe. The page exists so the site-wide nav link is not
        dead, but it carries no holdings and no history — never a plaintext
        portfolio."""
        from brickonomy.export import export
        monkeypatch.delenv(lockbox.ENV_VAR, raising=False)
        out = tmp_path / "site"
        export(str(out), "ILS", quiet=True)
        text = (out / "portfolio.html").read_text(encoding="utf-8")
        assert "Not published in this snapshot" in text
        assert "ciphertext" not in text
        assert "Millennium Falcon" not in text, "no holding may appear"
        assert not (out / "api" / "portfolio" / "history.json").exists()

    def test_with_a_password_the_page_holds_no_plaintext(
            self, monkeypatch, tmp_path):
        from brickonomy.export import export
        monkeypatch.setenv(lockbox.ENV_VAR, "s3cret")
        out = tmp_path / "site"
        export(str(out), "ILS", quiet=True)
        page = (out / "portfolio.html")
        assert page.exists()
        text = page.read_text(encoding="utf-8")
        assert "s3cret" not in text, "the password must not ship with the page"
        assert "ciphertext" in text and "iterations" in text
        # The unencrypted history file must still be absent.
        assert not (out / "api" / "portfolio" / "history.json").exists()
