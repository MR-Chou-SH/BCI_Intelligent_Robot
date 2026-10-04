"""NumPy-only, trial-grouped SSVEP classifiers used by the M34 benchmark.

The eTRCA implementation follows the inter-trial covariance generalized
Rayleigh quotient and ensemble template matching described by Nakanishi et
al. The TDCA implementation follows Liu et al.: lag-expand each trial, project
the expansion onto each class's sinusoidal reference subspace, fit a shared
two-dimensional Fisher discriminant, then match projected templates.
"""

from __future__ import annotations

import numpy as np


def centered(x):
    x = np.asarray(x, dtype=np.float64)
    return x - np.mean(x, axis=-1, keepdims=True)


def correlation(a, b):
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    a = a - np.mean(a)
    b = b - np.mean(b)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))


def top_generalized_vectors(sb, sw, component_count, relative_ridge):
    """Solve the symmetric generalized eigenproblem with relative ridge."""
    sb = np.asarray(sb, dtype=np.float64)
    sw = np.asarray(sw, dtype=np.float64)
    sb = 0.5 * (sb + sb.T)
    sw = 0.5 * (sw + sw.T)
    sw_values, sw_vectors = np.linalg.eigh(sw)
    scale = max(float(np.trace(sw)) / max(sw.shape[0], 1), float(np.max(np.abs(sw_values))), 1e-12)
    floor = max(relative_ridge * scale, 1e-12)
    inv_root = (sw_vectors * (1.0 / np.sqrt(np.maximum(sw_values, floor)))) @ sw_vectors.T
    whitened = inv_root @ sb @ inv_root
    eigenvalues, eigenvectors = np.linalg.eigh(0.5 * (whitened + whitened.T))
    count = min(int(component_count), eigenvectors.shape[1])
    vectors = inv_root @ eigenvectors[:, -count:]
    return vectors[:, ::-1], eigenvalues[-count:][::-1]


def fit_etrcca(epochs, labels, component_count=1, relative_ridge=1e-6):
    """Fit ensemble TRCA spatial filters and class templates.

    Args:
        epochs: trials x channels x samples; training split only.
        labels: integer class labels 0..2.
    """
    epochs = np.asarray(epochs, dtype=np.float64)
    labels = np.asarray(labels, dtype=int)
    if epochs.ndim != 3 or len(labels) != len(epochs):
        raise ValueError("epochs must be trials x channels x samples and match labels")
    channels = epochs.shape[1]
    templates = []
    filters = []
    for class_id in range(3):
        class_epochs = centered(epochs[labels == class_id])
        if len(class_epochs) < 2:
            raise ValueError(f"eTRCA needs at least two training trials for class {class_id}")
        q = np.zeros((channels, channels), dtype=np.float64)
        s = np.zeros_like(q)
        for trial in class_epochs:
            q += trial @ trial.T
        for left in range(len(class_epochs)):
            for right in range(left + 1, len(class_epochs)):
                cross = class_epochs[left] @ class_epochs[right].T
                s += cross + cross.T
        vectors, _ = top_generalized_vectors(s, q, component_count, relative_ridge)
        filters.append(vectors)
        templates.append(np.mean(class_epochs, axis=0))
    ensemble = np.concatenate(filters, axis=1)
    return {"filters": ensemble, "templates": np.asarray(templates)}


def predict_etrcca(model, epoch):
    epoch = centered(epoch)
    filters = model["filters"]
    test_features = filters.T @ epoch
    scores = []
    for class_id in range(3):
        template_features = filters.T @ model["templates"][class_id]
        scores.append(correlation(test_features, template_features))
    return np.asarray(scores, dtype=np.float64)


def sinusoidal_reference(frequency_hz, harmonic_count, sampling_rate_hz, sample_count):
    t = np.arange(sample_count, dtype=np.float64) / sampling_rate_hz
    rows = []
    for harmonic in range(1, harmonic_count + 1):
        phase = 2.0 * np.pi * harmonic * frequency_hz * t
        rows.extend((np.sin(phase), np.cos(phase)))
    return np.asarray(rows, dtype=np.float64)


def delay_expand(epoch, delay_count=5, delay_step_samples=4):
    """Append zero-padded delayed copies without consuming future samples."""
    epoch = centered(epoch)
    channels, sample_count = epoch.shape
    copies = []
    for delay_index in range(delay_count + 1):
        offset = delay_index * delay_step_samples
        shifted = np.zeros((channels, sample_count), dtype=np.float64)
        if offset == 0:
            shifted[:] = epoch
        elif offset < sample_count:
            shifted[:, : sample_count - offset] = epoch[:, offset:]
        copies.append(shifted)
    return np.concatenate(copies, axis=0)


def reference_projection(frequency_hz, harmonic_count, sampling_rate_hz, sample_count):
    reference = sinusoidal_reference(frequency_hz, harmonic_count, sampling_rate_hz, sample_count)
    orthonormal, _ = np.linalg.qr(reference.T, mode="reduced")
    return orthonormal


def tdca_augmented(epoch, projection, delay_count=5, delay_step_samples=4):
    delayed = delay_expand(epoch, delay_count, delay_step_samples)
    projected = (delayed @ projection) @ projection.T
    return np.concatenate((delayed, projected), axis=0)


def fit_tdca(
    epochs,
    labels,
    frequencies_hz=(7.2, 9.0, 12.0),
    sampling_rate_hz=1000.0,
    harmonic_count=3,
    delay_count=5,
    delay_step_samples=4,
    component_count=4,
    relative_ridge=0.001,
):
    """Fit shared TDCA discriminant directions using training trials only."""
    epochs = np.asarray(epochs, dtype=np.float64)
    labels = np.asarray(labels, dtype=int)
    if epochs.ndim != 3 or len(labels) != len(epochs):
        raise ValueError("epochs must be trials x channels x samples and match labels")
    sample_count = epochs.shape[-1]
    projections = [
        reference_projection(frequency, harmonic_count, sampling_rate_hz, sample_count)
        for frequency in frequencies_hz
    ]
    transformed = np.stack(
        [tdca_augmented(epoch, projections[int(label)], delay_count, delay_step_samples)
         for epoch, label in zip(epochs, labels)],
        axis=0,
    )
    class_templates = np.stack(
        [np.mean(transformed[labels == class_id], axis=0) for class_id in range(3)],
        axis=0,
    )
    grand_mean = np.mean(transformed, axis=0)
    between = np.concatenate(
        [(class_templates[class_id] - grand_mean) / np.sqrt(3.0) for class_id in range(3)],
        axis=1,
    )
    within = np.concatenate(
        [(trial - class_templates[int(label)]) / np.sqrt(float(len(epochs)))
         for trial, label in zip(transformed, labels)],
        axis=1,
    )
    sb = between @ between.T
    sw = within @ within.T
    filters, eigenvalues = top_generalized_vectors(sb, sw, component_count, relative_ridge)
    return {
        "filters": filters,
        "templates": class_templates,
        "projections": projections,
        "frequenciesHz": list(frequencies_hz),
        "harmonicCount": int(harmonic_count),
        "samplingRateHz": float(sampling_rate_hz),
        "delayCount": int(delay_count),
        "delayStepSamples": int(delay_step_samples),
        "eigenvalues": eigenvalues.tolist(),
    }


def predict_tdca(model, epoch):
    delayed = delay_expand(epoch, model["delayCount"], model["delayStepSamples"])
    scores = []
    for class_id, projection in enumerate(model["projections"]):
        projected = (delayed @ projection) @ projection.T
        augmented = np.concatenate((delayed, projected), axis=0)
        test_features = model["filters"].T @ augmented
        template_features = model["filters"].T @ model["templates"][class_id]
        scores.append(correlation(test_features, template_features))
    return np.asarray(scores, dtype=np.float64)
