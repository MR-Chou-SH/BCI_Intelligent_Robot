import json
import unittest

from integration.m13_6_visual_readiness import readiness_report


class M136VisualReadinessTests(unittest.TestCase):
    def test_static_readiness_passes_without_hardware(self):
        report = readiness_report()
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["stream"]["selectionTcpPort"], 11001)
        self.assertEqual(report["stream"]["udpPort"], 11002)
        self.assertEqual(report["stream"]["tcpPort"], 11002)
        self.assertEqual(report["stream"]["transportModes"], ["auto", "udp", "tcp"])
        self.assertFalse(report["hardware"]["questOperated"])
        self.assertNotIn("obj_", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
