"""
Tests for AES-256 encryption, decryption, and key management.
"""

from pathlib import Path
import tempfile
import unittest

from kedge.crypto.gpg import CryptoEngine


class TestCryptoEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.tmp_dir.name)
        self.key_file = self.test_root / "test.key"
        self.src_file = self.test_root / "payload.tar.gz"
        self.enc_file = self.test_root / "payload.tar.gz.gpg"
        self.dec_file = self.test_root / "decrypted.tar.gz"

        # Create dummy payload
        self.payload_content = b"Kedge Encrypted State Payload Test 1234567890" * 100
        self.src_file.write_bytes(self.payload_content)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_key_generation(self):
        key_path = CryptoEngine.ensure_key_file(str(self.key_file))
        self.assertTrue(key_path.is_file())
        key_data = key_path.read_text().strip()
        self.assertGreaterEqual(len(key_data), 32)

    def test_encryption_and_decryption_round_trip(self):
        key_path = CryptoEngine.ensure_key_file(str(self.key_file))

        # Encrypt
        ok, enc_msg = CryptoEngine.encrypt(self.src_file, self.enc_file, key_path)
        self.assertTrue(ok, f"Encryption failed: {enc_msg}")
        self.assertTrue(self.enc_file.is_file())
        self.assertNotEqual(self.enc_file.read_bytes(), self.payload_content)

        # Decrypt
        ok, dec_msg = CryptoEngine.decrypt(self.enc_file, self.dec_file, key_path)
        self.assertTrue(ok, f"Decryption failed: {dec_msg}")
        self.assertTrue(self.dec_file.is_file())
        self.assertEqual(self.dec_file.read_bytes(), self.payload_content)

    def test_decryption_with_invalid_key_fails(self):
        key_path = CryptoEngine.ensure_key_file(str(self.key_file))
        CryptoEngine.encrypt(self.src_file, self.enc_file, key_path)

        # Create different key
        bad_key = self.test_root / "bad.key"
        bad_key.write_text("completely_different_passphrase_xyz\n")

        ok, dec_msg = CryptoEngine.decrypt(self.enc_file, self.dec_file, bad_key)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
