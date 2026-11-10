"""Stable training-only proprioception/action statistics for almost fixed joints."""
import numpy as np


def stable_stats(values, *, width_state=False):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 16 or not len(values) or not np.isfinite(values).all():
        raise ValueError('Expected finite Nx16 training values')
    # One milliradian for joints; widths in metres differ from opening ratios.
    floor = np.full(16, .001)
    if width_state:
        floor[[7, 15]] = .0001
    return {'mean': values.mean(axis=0), 'std': np.maximum(values.std(axis=0), floor),
            'min': values.min(axis=0), 'max': values.max(axis=0)}
