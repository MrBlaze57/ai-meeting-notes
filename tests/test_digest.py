import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/ai-meeting-notes/server"))
from core import digest


class DigestCompatibilityTest(unittest.TestCase):
    def test_sha256_attachments_without_python311_file_digest(self):
        # Known SHA-256 vectors, including a file larger than a read buffer.
        vectors = [
            (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
            (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
            (b"a" * 1_000_000, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"),
        ]
        with tempfile.TemporaryDirectory() as temp, patch.dict(hashlib.__dict__):
            hashlib.__dict__.pop("file_digest", None)
            path = Path(temp) / "attachment.bin"
            for data, expected in vectors:
                with self.subTest(byte_size=len(data)):
                    path.write_bytes(data)
                    self.assertEqual(digest(path), expected)


if __name__ == "__main__":
    unittest.main()
