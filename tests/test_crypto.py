import base64
import gzip
import json
from pathlib import Path
import sys
import unittest

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import export_data


class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"schema": 1, "allowedUsers": ["test-account"], "secret": "private-metric-123"}
        self.password = "test-credential-not-for-production"
        self.salt = bytes(range(32))

    def decrypt(self, envelope, password):
        salt = base64.b64decode(envelope["salt"])
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=envelope["iterations"]).derive(password.encode())
        raw = AESGCM(key).decrypt(base64.b64decode(envelope["iv"]),
                                 base64.b64decode(envelope["ciphertext"]), export_data.AAD)
        return json.loads(gzip.decompress(raw))

    def test_round_trip_and_no_plaintext(self):
        envelope = export_data.encrypted(self.payload, self.password, self.salt)
        self.assertEqual(self.decrypt(envelope, self.password), self.payload)
        text = json.dumps(envelope)
        for private in (self.password, "test-account", "private-metric-123"):
            self.assertNotIn(private, text)

    def test_wrong_password_rejected(self):
        envelope = export_data.encrypted(self.payload, self.password, self.salt)
        with self.assertRaises(InvalidTag):
            self.decrypt(envelope, "incorrect")

    def test_tampering_rejected(self):
        envelope = export_data.encrypted(self.payload, self.password, self.salt)
        ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
        ciphertext[0] ^= 1
        envelope["ciphertext"] = base64.b64encode(ciphertext).decode()
        with self.assertRaises(InvalidTag):
            self.decrypt(envelope, self.password)

    def test_same_key_has_fresh_nonce_per_export(self):
        first = export_data.encrypted(self.payload, self.password, self.salt)
        second = export_data.encrypted(self.payload, self.password, self.salt)
        self.assertNotEqual(first["iv"], second["iv"])
        self.assertNotEqual(first["ciphertext"], second["ciphertext"])
        self.assertEqual(first["salt"], second["salt"])

    def test_previous_snapshot_boundary_is_read_from_authenticated_payload(self):
        envelope = export_data.encrypted({**self.payload, "generatedAt": 1234.5}, self.password, self.salt)
        self.assertEqual(export_data.snapshot_time(envelope, self.password), 1234.5)
        with self.assertRaises(InvalidTag):
            export_data.snapshot_time(envelope, "incorrect")

    def test_sample_deduplicates_occupancy_and_drops_pid(self):
        state = {"last_success_at": 123, "current": {"owners": {"g": {"42:u": {"uid": 42}}},
                 "processes": [{"gpu_uuid": "g", "pid": 99}, {"gpu_uuid": "g", "pid": 100}]},
                 "users": {"42:u": {"gpu_seconds": 10, "shared_gpu_seconds": 2}},
                 "observed_physical_seconds": 10, "busy_physical_seconds": 6,
                 "unobserved_physical_seconds": 0,
                 "unattributed_physical_seconds": 0}
        point = export_data.point(state)
        self.assertEqual(point["busy"], 1)
        self.assertEqual(point["busySeconds"], 6)
        self.assertTrue({"cards", "totals", "shared"}.isdisjoint(point))
        self.assertNotIn("42:u", json.dumps(point))
        self.assertNotIn("pid", json.dumps(point))


if __name__ == "__main__":
    unittest.main()
