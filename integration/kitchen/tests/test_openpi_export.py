"""OpenPI staging helpers; full replay/encode is exercised on real episodes."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from integration.kitchen.vla.openpi_export import image_stats, selected_rows


class OpenPIExportTests(unittest.TestCase):
    def test_image_stats_use_lerobot_channel_layout(self):
        frames = [np.zeros((4, 4, 3), np.uint8), np.full((4, 4, 3), 255, np.uint8)]
        stats = image_stats(frames)
        self.assertEqual(np.asarray(stats['mean']).shape, (3, 1, 1))
        self.assertEqual(stats['count'], [2])
        np.testing.assert_allclose(np.asarray(stats['mean']).ravel(), [.5, .5, .5])
        np.testing.assert_allclose(np.asarray(stats['max']).ravel(), [1., 1., 1.])

    def test_per_case_keeps_manifest_order(self):
        rows = [{'case_id': c, 'trajectory': f'{c}{i}'} for c in 'ab' for i in range(3)]
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'manifest.json').write_text(json.dumps(rows))
            self.assertEqual([r['trajectory'] for r in selected_rows(temporary, 2)], ['a0', 'a1', 'b0', 'b1'])
            self.assertEqual(len(selected_rows(temporary, None)), 6)


if __name__ == '__main__':
    unittest.main()
