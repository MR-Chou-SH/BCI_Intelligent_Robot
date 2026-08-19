import unittest

import numpy as np

from eeg.decoder.cca import predict, references
from eeg.decoder.config import DecoderConfig
from eeg.decoder.pipeline import evaluate_predictions
from eeg.decoder.fbcca import FbccaConfig, FilterBand, apply_filter_band, predict_fbcca, validate_config
from eeg.decoder.characterization import WINDOW_GRID_SECONDS, validate_window_grid


class DecoderTests(unittest.TestCase):
    def test_config_has_fixed_three_second_window(self):
        config = DecoderConfig()
        self.assertEqual(500, config.onset_guard_samples)
        self.assertEqual(3000, config.analysis_sample_count)

    def test_reference_shape_and_synthetic_prediction(self):
        config = DecoderConfig()
        refs = references(config.target_frequencies_hz, config.harmonic_count,
                          config.decoder_sampling_rate_hz, config.analysis_sample_count)
        self.assertEqual((6, 3000), refs[0].shape)
        signal = refs[1][:2] + 0.01 * np.random.default_rng(7).standard_normal((2, 3000))
        index, scores = predict(signal, refs)
        self.assertEqual(1, index)
        self.assertEqual(3, len(scores))

    def test_constant_signal_has_finite_deterministic_scores(self):
        config = DecoderConfig()
        refs = references(config.target_frequencies_hz, config.harmonic_count,
                          config.decoder_sampling_rate_hz, config.analysis_sample_count)
        first = predict(np.ones((2, 3000)), refs)
        second = predict(np.ones((2, 3000)), refs)
        self.assertEqual(first, second)
        self.assertTrue(all(np.isfinite(score) for score in first[1]))

    def test_evaluator_detects_intentionally_wrong_prediction(self):
        trials = [
            {"trueClass": "target_left", "predictedClass": "target_left"},
            {"trueClass": "target_center", "predictedClass": "target_right"},
            {"trueClass": "target_right", "predictedClass": "target_right"},
        ]
        result = evaluate_predictions(trials)
        self.assertEqual((2, 3), (result["correct"], result["total"]))
        self.assertLess(result["accuracy"], 1.0)
        self.assertEqual(1, result["confusionMatrix"]["target_center"]["target_right"])

    def test_fbcca_filter_is_shape_preserving_and_deterministic(self):
        samples = np.random.default_rng(2).standard_normal((2, 3000))
        band = FbccaConfig().filter_bands[0]
        first = apply_filter_band(samples, band, 1000.0, 500)
        second = apply_filter_band(samples, band, 1000.0, 500)
        self.assertEqual(samples.shape, first.shape)
        np.testing.assert_allclose(first, second)

    def test_fbcca_rejects_invalid_nyquist_band(self):
        config = FbccaConfig(filter_bands=(FilterBand(4, 6, 490, 600),))
        with self.assertRaises(ValueError):
            validate_config(config, 1000.0)

    def test_fbcca_synthetic_three_frequency_prediction(self):
        config = DecoderConfig()
        t = np.arange(config.analysis_sample_count) / config.decoder_sampling_rate_hz
        for index, frequency in enumerate(config.target_frequencies_hz):
            signal = np.vstack([np.sin(2 * np.pi * frequency * t),
                                np.cos(2 * np.pi * frequency * t)])
            predicted, fused, subbands = predict_fbcca(signal, config.target_frequencies_hz,
                                                        config.harmonic_count,
                                                        config.decoder_sampling_rate_hz)
            self.assertEqual(index, predicted)
            self.assertEqual(3, len(fused))
            self.assertEqual(3, len(subbands))

    def test_characterization_window_grid_validation(self):
        self.assertEqual(WINDOW_GRID_SECONDS, validate_window_grid())
        with self.assertRaises(ValueError):
            validate_window_grid((0.5, 0.5))
        with self.assertRaises(ValueError):
            validate_window_grid((1.0, 0.5))


if __name__ == "__main__":
    unittest.main()
