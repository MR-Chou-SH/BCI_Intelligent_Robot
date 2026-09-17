import math
import unittest

from integration.m13_6_transform_sanity import analyze_frames
from integration.m13_6_usb_visual_acceptance import _synthetic_frame


class M136TransformSanityTests(unittest.TestCase):
    def test_synthetic_trajectory_is_finite_and_bounded(self):
        result = analyze_frames([_synthetic_frame(index, index / 30.0) for index in range(30)], {"source": "synthetic"})
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["finiteValues"])
        self.assertTrue(result["sequenceMonotonic"])
        self.assertTrue(result["quaternionNormValid"])
        self.assertGreaterEqual(result["jointMinRadians"], -math.pi)
        self.assertLessEqual(result["jointMaxRadians"], math.pi)


if __name__ == "__main__":
    unittest.main()
