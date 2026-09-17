import unittest
from unittest.mock import patch

from integration.m13_6_visual_preflight import _same_subnet


class M136VisualPreflightTests(unittest.TestCase):
    def test_same_subnet_uses_quest_prefix(self):
        self.assertTrue(_same_subnet("192.168.43.168", "192.168.43.110", 24))
        self.assertFalse(_same_subnet("192.168.44.168", "192.168.43.110", 24))

    def test_invalid_addresses_fail_closed(self):
        self.assertFalse(_same_subnet("not-an-ip", "192.168.43.110", 24))
        self.assertFalse(_same_subnet("192.168.43.168", "not-an-ip", 24))


if __name__ == "__main__":
    unittest.main()
