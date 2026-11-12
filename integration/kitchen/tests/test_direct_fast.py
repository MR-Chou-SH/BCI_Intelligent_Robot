"""Direct-fast time compression of planned frame batches."""
import math
import unittest
from types import SimpleNamespace

import numpy as np

from integration.kitchen.planning.motion import ExpertMotion


class Controller:
    def __init__(self, limit=None):
        self.reference = np.zeros(16)
        self.limit = limit

    def prepare_joint_targets(self, values, reference=None):
        values = np.asarray(values, dtype=float)
        if self.limit is None:
            return values.copy()
        return reference + np.clip(values - reference, -self.limit, self.limit)


def expert(factor=4., limit=None, **attributes):
    e = ExpertMotion()
    e.platform = SimpleNamespace(controller=Controller(limit))
    e.time_compression = factor
    e.compression_caps = {}
    for name, value in attributes.items():
        setattr(e, name, value)
    return e


def ramp(n, step=.01):
    return [np.full(16, step * (i + 1)) for i in range(n)]


class DirectFastTests(unittest.TestCase):
    def test_disabled_or_single_frame_batches_are_unchanged(self):
        targets = ramp(9)
        self.assertIs(expert(1.).compress(targets), targets)
        one = ramp(1)
        self.assertIs(expert().compress(one), one)

    def test_batches_are_resampled_with_the_same_end_and_linear_interpolation(self):
        targets = ramp(10)
        out = expert().compress(targets)
        self.assertEqual(len(out), math.ceil(10 / 4))
        np.testing.assert_array_equal(out[-1], targets[-1])
        # times 10/3 and 20/3 along [reference(0), 0.01, ..., 0.10]
        np.testing.assert_allclose(out[0], np.full(16, .01 * 10 / 3))
        np.testing.assert_allclose(out[1], np.full(16, .01 * 20 / 3))

    def test_holds_are_compressed_unless_capped(self):
        hold = [np.zeros(16)] * 8
        self.assertEqual(len(expert().compress(hold)), 2)
        e = expert()
        with e.compression_limit(1.):
            self.assertIs(e.compress(hold), hold)
        self.assertEqual(e.time_compression, 4.)

    def test_target_caps(self):
        targets = ramp(8)
        e = expert(compression_caps={'microwave_start_geom': 1., 'banana': 2.})
        self.assertIs(e.compress(targets, {'target': 'microwave_start_geom', 'phase': 'device'}), targets)
        self.assertEqual(len(e.compress(targets, {'target': 'banana', 'phase': 'carry'})), 4)
        self.assertEqual(len(e.compress(targets, {'target': 'apple', 'phase': 'carry'})), 2)
        self.assertEqual(len(e.compress(targets)), 2)

    def test_less_compression_while_live_limits_would_clip(self):
        targets = ramp(8)
        out = expert(limit=.025).compress(targets)  # 0.01/frame source, 0.025 cap
        self.assertGreaterEqual(min(np.diff(np.vstack([np.zeros(16), *out])[:, 0])), 0)
        self.assertLessEqual(max(np.diff(np.vstack([np.zeros(16), *out])[:, 0])), .025 + 1e-12)
        self.assertGreater(len(out), 2)
        np.testing.assert_array_equal(out[-1], targets[-1])
        exact = ramp(8)
        self.assertIs(expert(limit=.01).compress(exact), exact)


if __name__ == '__main__':
    unittest.main()
