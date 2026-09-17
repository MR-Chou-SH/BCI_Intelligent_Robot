import tempfile
import unittest
from pathlib import Path

from integration.m13_6_apk_verifier import verify_apk


class M136ApkVerifierTests(unittest.TestCase):
    def test_missing_apk_is_explicit(self):
        result = verify_apk(Path(tempfile.gettempdir()) / "m13_6_missing.apk")
        self.assertEqual(result["status"], "MISSING_APK")
        self.assertFalse(result["exists"])


if __name__ == "__main__":
    unittest.main()
