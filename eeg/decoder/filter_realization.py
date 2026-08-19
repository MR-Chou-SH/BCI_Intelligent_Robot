"""M6.3b current-vs-legacy FBCCA filter realization validation."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal

from .config import DecoderConfig
from .dataset import extract_epochs, load_dataset
from .fbcca import FbccaConfig, apply_filter_band, predict_fbcca, validate_config
from .cca import predict, references
from .pipeline import evaluate_predictions


WINDOWS = (0.5, 1.0, 1.5, 3.0)


def legacy_filter_details(sampling_rate_hz, config=None):
    config = config or FbccaConfig()
    validate_config(config, sampling_rate_hz)
    details = []
    nyquist = sampling_rate_hz / 2.0
    for index, band in enumerate(config.filter_bands):
        wp = [band.pass_low_hz / nyquist, band.pass_high_hz / nyquist]
        ws = [band.stop_low_hz / nyquist, band.stop_high_hz / nyquist]
        order, wn = signal.cheb1ord(wp, ws, 3, 40)
        sos = signal.cheby1(order, 0.5, wn, btype="bandpass", output="sos")
        # scipy.signal.sosfiltfilt's default padlen formula for this SOS layout.
        zeros_at_origin = poles_at_origin = 0
        default_padlen = 3 * (2 * len(sos) + 1 - min(zeros_at_origin, poles_at_origin))
        details.append({"bandIndex": index, "order": int(order), "wn": np.asarray(wn).tolist(),
                        "sosSections": len(sos), "defaultPadlen": default_padlen,
                        "passbandRippleDb": 0.5, "stopbandAttenuationDb": 40.0,
                        "passbandHz": [band.pass_low_hz, band.pass_high_hz],
                        "stopbandHz": [band.stop_low_hz, band.stop_high_hz]})
    return details


def apply_legacy_filter_band(epoch, band, sampling_rate_hz, details=None):
    epoch = np.asarray(epoch, dtype=float)
    if epoch.ndim != 2 or epoch.shape[1] < 8:
        raise ValueError("epoch must be channels by at least 8 samples")
    nyquist = sampling_rate_hz / 2.0
    wp = [band.pass_low_hz / nyquist, band.pass_high_hz / nyquist]
    ws = [band.stop_low_hz / nyquist, band.stop_high_hz / nyquist]
    order, wn = signal.cheb1ord(wp, ws, 3, 40)
    sos = signal.cheby1(order, 0.5, wn, btype="bandpass", output="sos")
    padlen = (3 * (2 * len(sos) + 1)) if details is None else details["defaultPadlen"]
    if epoch.shape[1] <= padlen:
        raise ValueError("legacy filtfilt padlen {} exceeds {} samples".format(padlen, epoch.shape[1]))
    return signal.sosfiltfilt(sos, epoch, axis=1), {"order": int(order), "padlen": int(padlen),
                                                    "sosSections": len(sos),
                                                    "implementation": "Chebyshev-I SOS forward/reverse zero-phase filtering"}


def predict_legacy_fbcca(epoch, frequencies_hz, harmonic_count, sampling_rate_hz, config=None):
    config = config or FbccaConfig()
    details = legacy_filter_details(sampling_rate_hz, config)
    refs = references(frequencies_hz, harmonic_count, sampling_rate_hz, epoch.shape[1])
    per_band = []
    filter_runtime = []
    for band, detail in zip(config.filter_bands, details):
        filtered, runtime = apply_legacy_filter_band(epoch, band, sampling_rate_hz, detail)
        _, scores = predict(filtered, refs)
        per_band.append(scores)
        filter_runtime.append(runtime)
    per_band = np.asarray(per_band, dtype=float)
    fused = config.weights() @ per_band
    return int(np.argmax(fused)), fused.tolist(), per_band.tolist(), filter_runtime


def _response_evidence(sampling_rate_hz, config):
    evidence = []
    details = legacy_filter_details(sampling_rate_hz, config)
    for band, detail in zip(config.filter_bands, details):
        nyquist = sampling_rate_hz / 2.0
        wp = [band.pass_low_hz / nyquist, band.pass_high_hz / nyquist]
        order, wn = signal.cheb1ord([band.pass_low_hz / nyquist, band.pass_high_hz / nyquist],
                                    [band.stop_low_hz / nyquist, band.stop_high_hz / nyquist], 3, 40)
        sos = signal.cheby1(order, 0.5, wn, btype="bandpass", output="sos")
        pass_mid = (band.pass_low_hz + band.pass_high_hz) / 2.0
        sample_hz = [band.stop_low_hz, band.pass_low_hz, band.pass_high_hz, band.stop_high_hz, pass_mid,
                     7.2, 9.0, 12.0, 14.4, 18.0, 21.6, 24.0, 36.0]
        w, h = signal.sosfreqz(sos, worN=8192, fs=sampling_rate_hz)
        gains = {}
        for frequency in sample_hz:
            gains[str(frequency)] = float(20 * np.log10(max(abs(h[np.argmin(abs(w - frequency))]), 1e-15)))
        evidence.append({"band": band.to_dict(), "order": int(order), "sampleGainDb": gains,
                         "responseChecks": {"passbandInteriorAtLeastMinus1Db": gains[str(pass_mid)] >= -1.0,
                                             "stopbandBelowMinus30Db": all(gains[str(f)] <= -30.0 for f in (band.stop_low_hz, band.stop_high_hz))}})
    return evidence


def _row_from_predictions(window, decoder, predictions):
    labels = ("target_left", "target_center", "target_right")
    evaluation = evaluate_predictions(predictions, labels)
    margins = [p["scoreMargin"] for p in predictions]
    return {"windowSeconds": window, "decoder": decoder, "sampleCount": int(window * 1000),
            "correct": evaluation["correct"], "total": evaluation["total"], "accuracy": evaluation["accuracy"],
            "perClass": evaluation["perClass"], "confusionMatrix": evaluation["confusionMatrix"],
            "margin": {"min": min(margins), "median": float(np.median(margins)),
                       "mean": float(np.mean(margins)), "max": max(margins)}, "trials": predictions}


def run_filter_realization_validation(session, output=None):
    config = DecoderConfig()
    fb_config = FbccaConfig()
    if tuple(WINDOWS) != tuple(sorted(WINDOWS)):
        raise ValueError("fixed validation windows must be ascending")
    dataset = load_dataset(session)
    epochs = extract_epochs(dataset, config)
    rows = []
    agreements = []
    for window in WINDOWS:
        sample_count = int(window * config.decoder_sampling_rate_hz)
        current_predictions, legacy_predictions = [], []
        for epoch in epochs:
            signal_epoch = epoch.data[:, :sample_count]
            signal_epoch = signal_epoch - signal_epoch.mean(axis=1, keepdims=True)
            current_index, current_scores, current_subband = predict_fbcca(signal_epoch, config.target_frequencies_hz,
                                                                             config.harmonic_count, config.decoder_sampling_rate_hz, fb_config)
            legacy_index, legacy_scores, legacy_subband, runtime = predict_legacy_fbcca(signal_epoch, config.target_frequencies_hz,
                                                                                         config.harmonic_count, config.decoder_sampling_rate_hz, fb_config)
            common = {"trialIndex": epoch.trial["trialIndex"], "trialId": epoch.trial["trialId"],
                      "trueClass": epoch.trial["targetId"], "trueFrequencyHz": epoch.trial["nominalFrequencyHz"],
                      "sampleRange": {"startInclusive": epoch.start_sample, "stopExclusive": epoch.start_sample + sample_count},
                      "association": {"startEstimatedGlobalSampleIndex": epoch.start_association["estimatedGlobalSampleIndex"],
                                       "segmentId": epoch.start_association["timestampSegmentId"], "mapping": "software-derived estimate"}}
            for name, index, scores, subband, target in (("numpy_rfft", current_index, current_scores, current_subband, current_predictions),
                                                         ("legacy_chebyshev_filtfilt", legacy_index, legacy_scores, legacy_subband, legacy_predictions)):
                values = {**common, "predictedClass": ("target_left", "target_center", "target_right")[index],
                          "predictedFrequencyHz": config.target_frequencies_hz[index],
                          "fusedScores": {str(f): float(s) for f, s in zip(config.target_frequencies_hz, scores)},
                          "subBandScores": [{str(f): float(s) for f, s in zip(config.target_frequencies_hz, band)} for band in subband],
                          "scoreMargin": float(max(scores) - sorted(scores)[-2]),
                          "correct": ("target_left", "target_center", "target_right")[index] == common["trueClass"]}
                if name.startswith("legacy"):
                    values["filterRuntime"] = runtime
                target.append(values)
        rows.extend((_row_from_predictions(window, "numpy_rfft", current_predictions),
                     _row_from_predictions(window, "legacy_chebyshev_filtfilt", legacy_predictions)))
        disagreements = [{"trialIndex": a["trialIndex"], "trialId": a["trialId"], "trueClass": a["trueClass"],
                          "numpyPrediction": a["predictedClass"], "legacyPrediction": b["predictedClass"]}
                         for a, b in zip(current_predictions, legacy_predictions) if a["predictedClass"] != b["predictedClass"]]
        agreements.append({"windowSeconds": window, "agreement": 30 - len(disagreements), "total": 30,
                           "disagreements": disagreements})
    artifact = {"recordType": "m6_3b_fbcca_filter_realization_validation", "generatedUtc": datetime.now(timezone.utc).isoformat(),
                "session": str(dataset["session"]), "sessionId": dataset["manifest"]["sessionId"],
                "windowsSeconds": list(WINDOWS), "selectedChannels": dataset["selected_channels"],
                "sharedConfig": config.to_dict(), "fbccaConfig": fb_config.to_dict(),
                "variants": {"numpy_rfft": "current NumPy rFFT raised-cosine zero-phase with reflection padding",
                             "legacy_chebyshev_filtfilt": "SciPy Chebyshev-I + filtfilt; dynamic cheb1ord"},
                "dependency": {"scipy": signal.__version__ if hasattr(signal, "__version__") else "1.14.1"},
                "legacyFilterDetails": legacy_filter_details(config.decoder_sampling_rate_hz, fb_config),
                "frequencyResponseEvidence": _response_evidence(config.decoder_sampling_rate_hz, fb_config),
                "results": rows, "predictionAgreement": agreements,
                "warnings": ["single_session_30_trials", "no_cross_session_or_online_validation",
                             "sample_association_is_software_derived_estimate",
                             "variants_are_not_numerically_equivalent_filters"],
                "evidenceBoundary": {"hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                                     "sampleAnchor": "unverified", "association": "software-derived estimate",
                                     "nominalStimulusFrequenciesOpticallyVerified": False}}
    if output:
        Path(output).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact
