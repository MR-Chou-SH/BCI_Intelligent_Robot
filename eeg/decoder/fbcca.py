"""Legacy-informed, NumPy-only filter-bank CCA for the M6 baseline."""

from dataclasses import asdict, dataclass

import numpy as np

from .cca import predict, references


@dataclass(frozen=True)
class FilterBand:
    stop_low_hz: float
    pass_low_hz: float
    pass_high_hz: float
    stop_high_hz: float

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class FbccaConfig:
    filter_bands: tuple = (FilterBand(4.0, 6.0, 90.0, 100.0),
                           FilterBand(10.0, 14.0, 90.0, 100.0),
                           FilterBand(16.0, 22.0, 90.0, 100.0))
    weighting_exponent: float = -1.25
    weighting_offset: float = 0.25
    edge_padding_seconds: float = 0.5

    def weights(self):
        indices = np.arange(1, len(self.filter_bands) + 1, dtype=float)
        return indices ** self.weighting_exponent + self.weighting_offset

    def to_dict(self):
        return {"filterBands": [band.to_dict() for band in self.filter_bands],
                "weightingFormula": "b^exponent + offset, b starts at 1",
                "weightingExponent": self.weighting_exponent,
                "weightingOffset": self.weighting_offset,
                "weights": self.weights().tolist(),
                "filterImplementation": "numpy_rfft_raised_cosine_zero_phase_with_reflection_padding",
                "edgePaddingSeconds": self.edge_padding_seconds,
                "subBandScore": "CCA maximum canonical correlation rho",
                "fusion": "weighted linear sum of rho; legacy-compatible"}


def validate_config(config, sampling_rate_hz):
    nyquist = sampling_rate_hz / 2.0
    if not config.filter_bands:
        raise ValueError("at least one filter band is required")
    for index, band in enumerate(config.filter_bands):
        values = (band.stop_low_hz, band.pass_low_hz, band.pass_high_hz, band.stop_high_hz)
        if not (0.0 < values[0] < values[1] < values[2] < values[3] < nyquist):
            raise ValueError("invalid filter band {} for Nyquist {}".format(index, nyquist))


def _response(frequencies, band):
    response = np.zeros_like(frequencies, dtype=float)
    low = (frequencies > band.stop_low_hz) & (frequencies < band.pass_low_hz)
    response[low] = 0.5 * (1.0 - np.cos(np.pi * (frequencies[low] - band.stop_low_hz) /
                                         (band.pass_low_hz - band.stop_low_hz)))
    response[(frequencies >= band.pass_low_hz) & (frequencies <= band.pass_high_hz)] = 1.0
    high = (frequencies > band.pass_high_hz) & (frequencies < band.stop_high_hz)
    response[high] = 0.5 * (1.0 + np.cos(np.pi * (frequencies[high] - band.pass_high_hz) /
                                          (band.stop_high_hz - band.pass_high_hz)))
    return response


def apply_filter_band(epoch, band, sampling_rate_hz, padding_samples=0):
    """Apply deterministic zero-phase frequency-domain filtering to channels×samples."""
    epoch = np.asarray(epoch, dtype=float)
    if epoch.ndim != 2 or epoch.shape[1] < 8:
        raise ValueError("epoch must be channels by at least 8 samples")
    if padding_samples:
        padded = np.pad(epoch, ((0, 0), (padding_samples, padding_samples)), mode="reflect")
    else:
        padded = epoch
    frequencies = np.fft.rfftfreq(padded.shape[1], d=1.0 / sampling_rate_hz)
    filtered = np.fft.irfft(np.fft.rfft(padded, axis=1) * _response(frequencies, band),
                            n=padded.shape[1], axis=1)
    return filtered[:, padding_samples:-padding_samples] if padding_samples else filtered


def predict_fbcca(epoch, frequencies_hz, harmonic_count, sampling_rate_hz, config=None):
    config = config or FbccaConfig()
    validate_config(config, sampling_rate_hz)
    refs = references(frequencies_hz, harmonic_count, sampling_rate_hz, epoch.shape[1])
    padding = int(round(config.edge_padding_seconds * sampling_rate_hz))
    per_band = []
    for band in config.filter_bands:
        filtered = apply_filter_band(epoch, band, sampling_rate_hz, padding)
        _, scores = predict(filtered, refs)
        per_band.append(scores)
    per_band = np.asarray(per_band, dtype=float)
    fused = config.weights() @ per_band
    return int(np.argmax(fused)), fused.tolist(), per_band.tolist()
