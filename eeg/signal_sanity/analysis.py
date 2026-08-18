"""Pure, offline ND8 channel sanity analysis; this module never decodes targets."""

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


SUMMARY_FILE = "signal-quality-summary.json"
RAW_FILE = "raw-eeg-packets.jsonl"
METADATA_FILE = "packet-metadata.jsonl"


def _read_jsonl(path):
    records, errors = [], []
    if not path.exists():
        return records, ["missing_file:" + path.name]
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                errors.append("malformed_json:{}:{}".format(path.name, line_number))
                continue
            if not isinstance(value, dict):
                errors.append("non_object_record:{}:{}".format(path.name, line_number))
                continue
            records.append(value)
    return records, errors


def _welch_psd(values, sampling_rate_hz):
    """Deterministic NumPy Welch PSD with a Hann window and 50% overlap."""
    values = np.asarray(values, dtype=float)
    nperseg = min(1024, len(values))
    if nperseg < 8:
        return None, {"method": "welch_numpy", "reason": "fewer_than_8_finite_samples"}
    step = max(1, nperseg // 2)
    starts = list(range(0, len(values) - nperseg + 1, step)) or [0]
    window = np.hanning(nperseg)
    window_energy = float(np.sum(window ** 2))
    spectra = []
    for start in starts:
        segment = values[start:start + nperseg]
        segment = segment - np.mean(segment)
        transformed = np.fft.rfft(segment * window)
        spectra.append((np.abs(transformed) ** 2) / (sampling_rate_hz * window_energy))
    frequencies = np.fft.rfftfreq(nperseg, d=1.0 / sampling_rate_hz)
    return (frequencies, np.mean(spectra, axis=0)), {
        "method": "welch_numpy", "window": "hann", "nperseg": nperseg,
        "overlapSamples": nperseg - step, "segmentCount": len(starts),
        "frequencyResolutionHz": sampling_rate_hz / nperseg,
    }


def _band_power(frequencies, psd, center_hz, half_width_hz):
    mask = (frequencies >= center_hz - half_width_hz) & (frequencies <= center_hz + half_width_hz)
    return float(np.mean(psd[mask])) if np.any(mask) else None


def _spectral_summary(values, sampling_rate_hz, mode):
    result, parameters = _welch_psd(values, sampling_rate_hz)
    if result is None:
        return {"parameters": parameters, "available": False}
    frequencies, psd = result
    limited = (frequencies >= 0.0) & (frequencies <= 100.0)
    resolution = parameters["frequencyResolutionHz"]
    neighborhood = max(0.5, resolution)
    targets = [50.0]
    if mode == "single_ssvep_sanity":
        targets = [9.0, 18.0, 27.0, 50.0]
    powers = {}
    for target in targets:
        power = _band_power(frequencies, psd, target, neighborhood)
        item = {"neighborhoodPower": power, "neighborhoodHalfWidthHz": neighborhood}
        if target in (9.0, 18.0, 27.0):
            lower = _band_power(frequencies, psd, target - 1.5, 0.5)
            upper = _band_power(frequencies, psd, target + 1.5, 0.5)
            background = np.mean([item for item in (lower, upper) if item is not None]) if lower is not None or upper is not None else None
            item["nearbyBackgroundPower"] = float(background) if background is not None else None
            item["stimulusToNearbyBackgroundRatio"] = (power / background if background and power is not None else None)
        powers[str(int(target) if target.is_integer() else target) + "Hz"] = item
    positive = np.where((frequencies > 0) & limited)[0]
    peak = int(positive[np.argmax(psd[positive])]) if len(positive) else None
    return {
        "available": True, "parameters": parameters, "frequencyRangeHz": [0.0, 100.0],
        "peak": ({"frequencyHz": float(frequencies[peak]), "power": float(psd[peak])} if peak is not None else None),
        "neighborhoodPowers": powers,
    }


def _channel_summary(index, values, sampling_rate_hz, mode, continuity_issues):
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    finite_values = values[finite]
    stats = {"sampleCount": int(values.size), "finiteCount": int(np.sum(finite)), "nonFiniteCount": int(np.sum(~finite))}
    reasons = []
    if not len(finite_values):
        stats.update({"min": None, "max": None, "mean": None, "median": None, "standardDeviation": None, "variance": None, "peakToPeak": None, "uniqueValueCount": 0})
        return {"channelIndex": index, "statistics": stats, "constantCandidate": False,
                "placeholderCandidate": False, "clippingCandidate": False, "spectralSummary": {"available": False},
                "quality": "invalid", "reasons": ["no_finite_samples"]}
    minimum, maximum = float(np.min(finite_values)), float(np.max(finite_values))
    peak_to_peak = maximum - minimum
    tolerance = max(1e-12, max(abs(minimum), abs(maximum), 1.0) * 1e-9)
    constant = bool(peak_to_peak <= tolerance)
    unique_count = int(np.unique(finite_values).size)
    min_fraction = float(np.mean(finite_values == minimum))
    max_fraction = float(np.mean(finite_values == maximum))
    clipping = bool(not constant and len(finite_values) >= 10 and (min_fraction >= 0.05 or max_fraction >= 0.05))
    stats.update({"min": minimum, "max": maximum, "mean": float(np.mean(finite_values)),
                  "median": float(np.median(finite_values)), "standardDeviation": float(np.std(finite_values)),
                  "variance": float(np.var(finite_values)), "peakToPeak": float(peak_to_peak),
                  "uniqueValueCount": unique_count})
    if stats["nonFiniteCount"]:
        reasons.append("nonfinite_samples_present")
    if constant:
        reasons.append("constant_or_placeholder_candidate")
    if clipping:
        reasons.append("repeated_extreme_or_clipping_candidate_adc_range_unknown")
    if continuity_issues:
        reasons.append("session_continuity_anomaly_present")
    if stats["nonFiniteCount"] or constant:
        quality = "invalid"
    elif clipping or continuity_issues:
        quality = "degraded"
    else:
        quality = "usable"
    return {"channelIndex": index, "statistics": stats, "constantCandidate": constant,
            "placeholderCandidate": constant, "clippingCandidate": clipping,
            "spectralSummary": _spectral_summary(finite_values, sampling_rate_hz, mode),
            "quality": quality, "reasons": reasons}


def analyze_session(session):
    """Read append-only raw evidence and write a deterministic derived summary."""
    session = Path(session)
    manifest_path = session / "session-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}
    raw_records, errors = _read_jsonl(session / RAW_FILE)
    metadata_records, metadata_errors = _read_jsonl(session / METADATA_FILE)
    errors.extend(metadata_errors)
    mode = manifest.get("validationMode", "unknown")
    sampling_rate_hz = float(manifest.get("samplingRateHz", 1000.0))
    channels = [[] for _ in range(8)]
    expected_sequence = None
    malformed_raw_count = 0
    for record in raw_records:
        samples = record.get("samples")
        sequence = record.get("packetSequence")
        if not isinstance(sequence, int) or not isinstance(samples, list) or len(samples) != 8 or any(not isinstance(item, list) for item in samples):
            errors.append("malformed_raw_packet_record")
            malformed_raw_count += 1
            continue
        lengths = {len(item) for item in samples}
        if not lengths or len(lengths) != 1 or next(iter(lengths)) == 0:
            errors.append("inconsistent_raw_packet_shape:sequence={}".format(sequence))
            malformed_raw_count += 1
            continue
        if expected_sequence is not None and sequence != expected_sequence:
            errors.append("raw_packet_sequence_gap_or_reorder:expected={},observed={}".format(expected_sequence, sequence))
        expected_sequence = sequence + 1
        for index, channel in enumerate(samples):
            try:
                channels[index].extend(float(value) for value in channel)
            except (TypeError, ValueError):
                errors.append("non_numeric_raw_sample:sequence={}:channel={}".format(sequence, index))
                malformed_raw_count += 1
    issue_counts = Counter()
    for record in metadata_records:
        continuity = record.get("continuity", {})
        for issue in continuity.get("issues", []) if isinstance(continuity, dict) else []:
            issue_counts[str(issue)] += 1
    summaries = [_channel_summary(i, values, sampling_rate_hz, mode, dict(issue_counts)) for i, values in enumerate(channels)]
    qualities = [item["quality"] for item in summaries]
    overall = "invalid" if errors or "invalid" in qualities else ("degraded" if "degraded" in qualities else "usable")
    output = {
        "recordType": "m6_1a_signal_quality_summary", "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "session": str(session), "sessionId": manifest.get("sessionId"), "mode": mode,
        "samplingRateHz": sampling_rate_hz, "packetCount": len(raw_records),
        "sampleCountPerChannel": [item["statistics"]["sampleCount"] for item in summaries],
        "continuity": {"metadataPacketCount": len(metadata_records), "issueCounts": dict(issue_counts)},
        "inputErrors": errors, "malformedRawPacketCount": malformed_raw_count, "channels": summaries,
        "overallRecommendation": overall,
        "warnings": ["Quality labels are reproducible engineering checks, not medical EEG quality metrics.",
                     "ADC full-scale range is unknown; clippingCandidate is not hardware saturation proof.",
                     "9-Hz power is spectral evidence only, not SSVEP classification or proof."],
        "timingEvidenceBoundary": {"hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                                   "sampleAnchor": "unverified; no hardware-exact sample index claimed"},
    }
    analysis_dir = session / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    (analysis_dir / SUMMARY_FILE).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
