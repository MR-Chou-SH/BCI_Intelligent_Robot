"""Pure NumPy Standard CCA classifier; no supervised fitting is performed."""

import numpy as np


def references(frequencies_hz, harmonic_count, sampling_rate_hz, sample_count):
    t = np.arange(sample_count, dtype=float) / sampling_rate_hz
    return [np.vstack([f(t) for harmonic in range(1, harmonic_count + 1)
                       for f in (lambda x, h=harmonic, freq=freq: np.sin(2*np.pi*h*freq*x),
                                 lambda x, h=harmonic, freq=freq: np.cos(2*np.pi*h*freq*x))])
            for freq in frequencies_hz]


def _corr_max(x, y):
    x = x - x.mean(axis=1, keepdims=True)
    y = y - y.mean(axis=1, keepdims=True)
    sx = x @ x.T
    sy = y @ y.T
    cxy = x @ y.T
    ex, vx = np.linalg.eigh(sx)
    ey, vy = np.linalg.eigh(sy)
    if ex[-1] <= 1e-12 or ey[-1] <= 1e-12:
        return 0.0
    invx = vx[:, -1::-1] @ np.diag(1.0 / np.sqrt(np.maximum(ex[::-1], 1e-12))) @ vx[:, -1::-1].T
    invy = vy[:, -1::-1] @ np.diag(1.0 / np.sqrt(np.maximum(ey[::-1], 1e-12))) @ vy[:, -1::-1].T
    return float(np.clip(np.linalg.svd(invx @ cxy @ invy, compute_uv=False)[0], 0.0, 1.0))


def predict(epoch, refs):
    scores = [_corr_max(epoch, reference) for reference in refs]
    index = int(np.argmax(scores))
    return index, scores
