"""Score the frozen M30 benchmark, fit train/dev-only coverage gates, and audit pages."""

from __future__ import annotations

import csv
import html
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from integration.semantic_context_sequence import project_all_pages


OUT = Path(__file__).resolve().parent
BENCHMARK = OUT / "semantic_context_sequence_benchmark_v2.json"
LOCK = OUT / "semantic_context_sequence_benchmark_lock.json"
RESULTS = OUT / "semantic_context_results.jsonl"
REPEATS = OUT / "semantic_context_repeatability.jsonl"
GATES = OUT / "coverage_operating_points.json"
PLOTS = OUT / "plots"
OPS = (("C65", 0.65), ("C85", 0.85), ("C100", 1.0))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_once(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite M30 result: {path.name}")
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_once(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path.name}")
    if path.exists():
        raise FileExistsError(f"refusing to overwrite M30 result: {path.name}")
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _confidence(row: dict[str, Any]) -> float | None:
    scores = row.get("candidateScores") or []
    values = sorted((float(item["semantic_score"]) for item in scores if isinstance(item, dict)
                     and isinstance(item.get("semantic_score"), (int, float))), reverse=True)
    if len(values) < 2:
        return None
    top = values[0]
    return 0.5 * top + 0.5 * (top - values[1])


def _select_threshold(values: list[float], requested: float) -> tuple[float, float]:
    if not values:
        raise ValueError("train/dev has no unique informative semantic predictions for gate calibration")
    if requested >= 1.0:
        threshold = min(values)
    else:
        thresholds = sorted(set(values), reverse=True)
        threshold = min(thresholds, key=lambda t: (abs(sum(value >= t for value in values) / len(values) - requested), -t))
    realized = sum(value >= threshold for value in values) / len(values)
    return threshold, realized


def _entropy(values: list[float]) -> float | None:
    total = sum(values)
    if total <= 0 or not values:
        return None
    normalized = [value / total for value in values]
    if len(normalized) <= 1:
        return 0.0
    return -sum(p * math.log(max(p, 1e-15)) for p in normalized if p > 0) / math.log(len(normalized))


def _safe_rate(n: int, d: int) -> float | None:
    return n / d if d else None


def _metric_rows(rows: list[dict[str, Any]], thresholds: dict[str, float], split_name: str) -> list[dict[str, Any]]:
    selected = list(rows) if split_name == "all" else [row for row in rows if row.get("split") == split_name]
    output = []
    for op_name, _requested in OPS:
        threshold = thresholds[op_name]
        eligible = [row for row in selected if row.get("expectedStatus") == "informative"
                    and len(row.get("acceptableNextTargets") or []) == 1]
        active = [row for row in selected if row.get("predictedStatus") == "informative"
                  and row.get("confidenceScore") is not None and row["confidenceScore"] >= threshold]
        correct = [row for row in active if row.get("expectedStatus") == "informative"
                   and row.get("rankedTopCandidate") in (row.get("acceptableNextTargets") or [])]
        wrong_active = [row for row in active if row not in correct]
        informative = [row for row in selected if row.get("expectedStatus") == "informative"
                       and len(row.get("acceptableNextTargets") or []) == 1]
        top1 = [row for row in informative if row.get("rankedTopCandidate") in row.get("acceptableNextTargets", [])]
        top3 = [row for row in informative if set(row.get("candidateRanking", [])[:3]) & set(row.get("acceptableNextTargets", []))]
        ambiguous = [row for row in selected if row.get("expectedStatus") == "ambiguous"]
        invalid = [row for row in selected if row.get("expectedStatus") == "invalid"]
        completed = [row for row in selected if row.get("expectedStatus") == "context_off"]
        active_entropy = [float(row["normalizedEntropy"]) for row in active if row.get("normalizedEntropy") is not None]
        active_mass = [float(row["priorTopMass"]) for row in active if row.get("priorTopMass") is not None]
        active_margin = [float(row["semanticScoreMargin"]) for row in active if row.get("semanticScoreMargin") is not None]
        relation_ok = [row for row in correct if row.get("relationTypeCorrect") is True]
        output.append({
            "split": split_name, "coverageOperatingPoint": op_name, "confidenceThreshold": threshold,
            "decisionCount": len(selected), "eligibleInformativeCount": len(eligible),
            "activeContextCount": len(active), "activeOnEligibleInformativeCount": sum(row in eligible for row in active),
            "overallParticipation": _safe_rate(len(active), len(selected)),
            "eligibleParticipation": _safe_rate(sum(row in eligible for row in active), len(eligible)),
            "activeContextPrecision": _safe_rate(len(correct), len(active)),
            "nextTargetTop1Accuracy": _safe_rate(len(top1), len(informative)),
            "top3Recall": _safe_rate(len(top3), len(informative)),
            "relationTypeAccuracyAmongCorrectActive": _safe_rate(len(relation_ok), len(correct)),
            "ambiguityHandlingAccuracy": _safe_rate(sum(row.get("predictedStatus") == "ambiguous" for row in ambiguous), len(ambiguous)),
            "invalidRejectionRate": _safe_rate(sum(row.get("predictedStatus") in {"invalid", "context_off"} for row in invalid), len(invalid)),
            "completedTaskRejectionRate": _safe_rate(sum(row.get("predictedStatus") == "context_off" for row in completed), len(completed)),
            "wrongActiveCount": len(wrong_active), "wrongActiveRateAmongAllDecisions": _safe_rate(len(wrong_active), len(selected)),
            "wrongActiveRateAmongEligibleInformative": _safe_rate(sum(row in wrong_active for row in eligible), len(eligible)),
            "meanEntropyWhenActive": statistics.mean(active_entropy) if active_entropy else None,
            "meanPriorTopMassWhenActive": statistics.mean(active_mass) if active_mass else None,
            "meanSemanticScoreMarginWhenActive": statistics.mean(active_margin) if active_margin else None,
        })
    return output


def _svg_bar(path: Path, title: str, labels: list[str], values: list[float], *, y_max: float = 1.0,
             y_label: str = "Rate") -> None:
    width, height = 920, 560
    left, top, plot_w, plot_h = 90, 85, 780, 360
    bar_w = min(90, plot_w / max(len(values), 1) * 0.55)
    bars = []
    for i, (label, value) in enumerate(zip(labels, values)):
        x = left + (i + 0.5) * plot_w / len(values) - bar_w / 2
        h = max(0.0, min(plot_h, value / y_max * plot_h))
        y = top + plot_h - h
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="#3977a8"/>')
        bars.append(f'<text x="{x + bar_w/2:.1f}" y="{top + plot_h + 25}" text-anchor="middle" font-size="13">{html.escape(label)}</text>')
        bars.append(f'<text x="{x + bar_w/2:.1f}" y="{max(top+15,y-7):.1f}" text-anchor="middle" font-size="12">{value:.3f}</text>')
    grids = []
    for step in range(6):
        frac = step / 5
        y = top + plot_h - frac * plot_h
        grids.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#dddddd"/>')
        grids.append(f'<text x="{left-12}" y="{y+4:.1f}" text-anchor="end" font-size="11">{frac*y_max:.2f}</text>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="white"/><text x="460" y="34" text-anchor="middle" font-size="21" font-family="sans-serif">{html.escape(title)}</text>{"".join(grids)}{"".join(bars)}<text x="20" y="270" transform="rotate(-90 20 270)" text-anchor="middle" font-size="14">{html.escape(y_label)}</text><text x="460" y="525" text-anchor="middle" font-size="11">M30 frozen benchmark; descriptive rate</text></svg>'
    _write_once(path, svg)


def _svg_hist(path: Path, title: str, values: list[float], *, bins: int = 12, x_max: float = 1.0) -> None:
    width, height = 920, 560
    left, top, plot_w, plot_h = 90, 85, 780, 360
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, max(0, int(value / x_max * bins)))
        counts[index] += 1
    max_count = max(counts, default=1) or 1
    cell = plot_w / bins
    content = []
    for i, count in enumerate(counts):
        h = count / max_count * plot_h
        x, y = left + i * cell + 2, top + plot_h - h
        content.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell-4:.1f}" height="{h:.1f}" fill="#5b8e7d"/>')
        content.append(f'<text x="{x+cell/2:.1f}" y="{top+plot_h+20}" text-anchor="middle" font-size="10">{(i+1)*x_max/bins:.2f}</text>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="white"/><text x="460" y="34" text-anchor="middle" font-size="21">{html.escape(title)}</text><line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#333"/><line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#333"/>{"".join(content)}<text x="480" y="525" text-anchor="middle" font-size="14">Value</text><text x="25" y="265" transform="rotate(-90 25 265)" text-anchor="middle" font-size="14">Count</text><text x="460" y="65" text-anchor="middle" font-size="12">n={len(values)}</text></svg>'
    _write_once(path, svg)


def run() -> dict[str, Any]:
    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if lock.get("sha256") != __import__("hashlib").sha256(BENCHMARK.read_bytes()).hexdigest():
        raise AssertionError("benchmark lock mismatch")
    raw = _read_jsonl(RESULTS)
    metadata = next((row for row in raw if row.get("recordType") == "m30_run_metadata"), None)
    rows = [row for row in raw if row.get("recordType") == "m30_semantic_context_result" and row.get("callKind") == "primary"]
    if metadata is None or metadata.get("benchmarkSha256") != lock["sha256"] or len(rows) != lock["decision_point_count"]:
        raise AssertionError("primary semantic result set does not match frozen benchmark")
    points_by_key = {(episode["episode_id"], int(point["round_index"])): point
                     for episode in benchmark["episodes"] for point in episode["decision_points"]}
    for row in rows:
        row["confidenceScore"] = _confidence(row)
        row["semanticScoreMargin"] = None
        scores = sorted((float(item["semantic_score"]) for item in row.get("candidateScores", [])
                         if isinstance(item, dict) and isinstance(item.get("semantic_score"), (int, float))), reverse=True)
        if len(scores) > 1:
            row["semanticScoreMargin"] = scores[0] - scores[1]

    calibration = [row for row in rows if row.get("split") in {"train", "dev"}
                   and row.get("expectedStatus") == "informative"
                   and len(row.get("acceptableNextTargets") or []) == 1
                   and row.get("predictedStatus") == "informative"
                   and row.get("confidenceScore") is not None]
    gate_defs = {}
    for op_name, requested in OPS:
        threshold, realized = _select_threshold([float(row["confidenceScore"]) for row in calibration], requested)
        gate_defs[op_name] = {"targetEligibleParticipation": requested, "confidenceThreshold": threshold,
                              "calibrationN": len(calibration), "calibrationRealizedEligibleParticipation": realized,
                              "calibrationSplits": ["train", "dev"],
                              "gate": "predictedStatus == informative AND 0.5*topOrdinalSemanticScore + 0.5*topSecondOrdinalMargin >= threshold"}
    _write_json(GATES, {"recordType": "m30_train_dev_only_coverage_gates", "benchmarkSha256": lock["sha256"],
                        "confidenceScore": "0.5*top ordinal semantic score + 0.5*(top minus second ordinal semantic score); not calibrated",
                        "calibrationRows": len(calibration), "coverageOperatingPoints": gate_defs,
                        "heldOutUsedForGateSelection": False})

    coverage_rows = []
    per_split = {}
    for split in ("train", "dev", "held_out", "all"):
        metrics = _metric_rows(rows if split == "all" else rows, {k: v["confidenceThreshold"] for k, v in gate_defs.items()}, split)
        per_split[split] = metrics
        coverage_rows.extend(metrics)
    _write_csv(OUT / "coverage_precision_summary.csv", coverage_rows)

    family_rows: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []
    for family in sorted({row["sceneFamily"] for row in rows}):
        group = [row for row in rows if row["sceneFamily"] == family]
        for op_name, _ in OPS:
            threshold = gate_defs[op_name]["confidenceThreshold"]
            active = [row for row in group if row.get("predictedStatus") == "informative"
                      and row.get("confidenceScore") is not None and row["confidenceScore"] >= threshold]
            informative = [row for row in group if row.get("expectedStatus") == "informative"
                           and len(row.get("acceptableNextTargets") or []) == 1]
            top1 = sum(row.get("rankedTopCandidate") in row.get("acceptableNextTargets", []) for row in informative)
            correct_active = sum(row.get("expectedStatus") == "informative" and
                                 row.get("rankedTopCandidate") in row.get("acceptableNextTargets", []) for row in active)
            family_rows.append({"sceneFamily": family, "coverageOperatingPoint": op_name, "split": group[0]["split"],
                                "decisionCount": len(group), "eligibleInformativeCount": len(informative),
                                "nextTargetTop1Accuracy": _safe_rate(top1, len(informative)),
                                "activeContextCount": len(active), "activeContextPrecision": _safe_rate(correct_active, len(active)),
                                "overallParticipation": _safe_rate(len(active), len(group)),
                                "wrongActiveCount": len(active)-correct_active,
                                "ambiguityAccuracy": _safe_rate(sum(r.get("predictedStatus")=="ambiguous" for r in group if r.get("expectedStatus")=="ambiguous"), sum(r.get("expectedStatus")=="ambiguous" for r in group))})
    for round_index in sorted({int(row["roundIndex"]) for row in rows}):
        group = [row for row in rows if int(row["roundIndex"]) == round_index]
        informative = [row for row in group if row.get("expectedStatus") == "informative" and len(row.get("acceptableNextTargets") or []) == 1]
        for op_name, _ in OPS:
            threshold = gate_defs[op_name]["confidenceThreshold"]
            active = [row for row in group if row.get("predictedStatus") == "informative" and row.get("confidenceScore") is not None and row["confidenceScore"] >= threshold]
            correct_active = sum(row.get("expectedStatus") == "informative" and row.get("rankedTopCandidate") in row.get("acceptableNextTargets", []) for row in active)
            round_rows.append({"roundIndex": round_index, "coverageOperatingPoint": op_name, "decisionCount": len(group),
                               "eligibleInformativeCount": len(informative), "nextTargetTop1Accuracy": _safe_rate(sum(r.get("rankedTopCandidate") in r.get("acceptableNextTargets", []) for r in informative), len(informative)),
                               "activeContextCount": len(active), "activeContextPrecision": _safe_rate(correct_active, len(active)),
                               "overallParticipation": _safe_rate(len(active), len(group)), "wrongActiveCount": len(active)-correct_active})
    _write_csv(OUT / "semantic_context_per_scene_family.csv", family_rows)
    _write_csv(OUT / "semantic_context_per_round.csv", round_rows)

    page_rows = []
    for row in rows:
        q_global = row.get("qGlobal") or []
        if not q_global:
            continue
        point = points_by_key[(row["episodeId"], int(row["roundIndex"]))]
        accepted = set(row.get("acceptableNextTargets") or [])
        global_top = max(q_global, key=lambda item: (float(item["q"]), -q_global.index(item)))
        global_entropy = _entropy([float(item["q"]) for item in q_global])
        for page_index, page in enumerate(project_all_pages(q_global, 3), 1):
            values = page["q_page"]
            page_top_index = max(range(len(values)), key=lambda idx: (values[idx], -idx))
            page_top_id = page["page_candidate_ids"][page_top_index]
            page_rows.append({"episodeId": row["episodeId"], "roundIndex": row["roundIndex"], "sceneFamily": row["sceneFamily"], "split": row["split"],
                              "pageIndex": page_index, "pageSize": page["page_size"], "pageCandidateIds": json.dumps(page["page_candidate_ids"], ensure_ascii=False),
                              "qPage": json.dumps(values, separators=(",", ":")), "globalTopCandidate": global_top["candidate_id"],
                              "globalTopMass": float(global_top["q"]), "globalEntropy": global_entropy, "pageTopCandidate": page_top_id,
                              "pageTopMass": max(values), "pageEntropy": _entropy(values), "globalTopInPage": global_top["candidate_id"] in page["page_candidate_ids"],
                              "pageTopMatchesGlobalTop": page_top_id == global_top["candidate_id"],
                              "acceptableTargetInPage": bool(accepted.intersection(page["page_candidate_ids"])),
                              "pageTopInAcceptableTargets": page_top_id in accepted,
                              "globalCandidateOrderPreserved": page["global_candidate_order_preserved"]})
    _write_csv(OUT / "page_projection_results.csv", page_rows)

    api_rows = [row for row in rows if int(row.get("attempts", 0)) > 0]
    api = [float(row.get("apiLatencyMs", 0)) for row in api_rows]
    local = [row.get("localMeasurements", {}) for row in rows]
    latency_summary = {
        "recordType": "m30_context_latency_summary", "benchmarkSha256": lock["sha256"], "apiCallN": len(api_rows),
        "apiLatencyMs": {"mean": statistics.mean(api), "median": statistics.median(api), "p90": sorted(api)[math.ceil(.9*len(api))-1], "p95": sorted(api)[math.ceil(.95*len(api))-1], "max": max(api)},
        "logicalPrimaryCallN": len(rows), "callsWithCorrectionRetry": sum(int(row.get("correctionRetries", 0)) > 0 for row in rows),
        "correctionRetryRate": sum(int(row.get("correctionRetries", 0)) > 0 for row in rows)/len(rows),
        "hallucinatedCandidateAttemptCount": sum(bool(row.get("hallucinatedCandidateAttempt")) for row in rows),
        "stateContradictionAttemptCount": sum(bool(row.get("stateContradictionAttempt")) for row in rows),
        "localDeterministicRelationValidationMsMean": statistics.mean(float(item["deterministicRelationValidationMs"]) for item in local if item.get("deterministicRelationValidationMs") is not None),
        "localPriorConstructionMsMean": statistics.mean(float(item["qConstructionReplayMs"]) for item in local if item.get("qConstructionReplayMs") is not None),
        "localAllPageProjectionMsMean": statistics.mean(float(item["allPageProjectionMs"]) for item in local if item.get("allPageProjectionMs") is not None),
        "interpretation": "Measured API wall time is separate from deterministic local relation validation, q construction, and projection replay."
    }
    _write_json(OUT / "context_latency_summary.json", latency_summary)

    overall = {}
    for split in ("train", "dev", "held_out", "all"):
        group = rows if split == "all" else [row for row in rows if row["split"] == split]
        informative = [row for row in group if row.get("expectedStatus") == "informative" and len(row.get("acceptableNextTargets") or []) == 1]
        correct = [row for row in informative if row.get("rankedTopCandidate") in row.get("acceptableNextTargets", [])]
        ambiguous = [row for row in group if row.get("expectedStatus") == "ambiguous"]
        invalid = [row for row in group if row.get("expectedStatus") == "invalid"]
        completed = [row for row in group if row.get("expectedStatus") == "context_off"]
        overall[split] = {"decisionCount": len(group), "eligibleInformativeCount": len(informative),
                          "predictedInformativeCount": sum(row.get("predictedStatus")=="informative" for row in group),
                          "informativeTop1Count": len(correct), "informativeTop1Accuracy": _safe_rate(len(correct), len(informative)),
                          "top3Recall": _safe_rate(sum(bool(set(row.get("candidateRanking", [])[:3]) & set(row.get("acceptableNextTargets", []))) for row in informative), len(informative)),
                          "ambiguityAccuracy": _safe_rate(sum(row.get("predictedStatus")=="ambiguous" for row in ambiguous), len(ambiguous)),
                          "invalidRejectionRate": _safe_rate(sum(row.get("predictedStatus") in {"invalid", "context_off"} for row in invalid), len(invalid)),
                          "completedRejectionRate": _safe_rate(sum(row.get("predictedStatus")=="context_off" for row in completed), len(completed)),
                          "meanEntropy": statistics.mean(float(row["normalizedEntropy"]) for row in group if row.get("normalizedEntropy") is not None),
                          "meanPriorTopMass": statistics.mean(float(row["priorTopMass"]) for row in group if row.get("priorTopMass") is not None)}

    repeats = [row for row in _read_jsonl(REPEATS) if row.get("recordType") == "m30_repeatability_result"]
    summary = {"recordType": "m30_semantic_context_summary", "benchmarkSha256": lock["sha256"], "promptVersion": metadata["promptVersion"],
               "modelId": metadata["modelId"], "episodeCount": len(benchmark["episodes"]), "decisionPointCount": len(rows),
               "splitMetrics": overall, "repeatability": {"n": len(repeats),
                   "sameStatusRate": _safe_rate(sum(bool(row.get("sameStatus")) for row in repeats), len(repeats)),
                   "sameTopCandidateRate": _safe_rate(sum(bool(row.get("sameTopCandidate")) for row in repeats), len(repeats)),
                   "sameFullRankingRate": _safe_rate(sum(bool(row.get("sameFullRanking")) for row in repeats), len(repeats))},
               "gateCalibration": gate_defs, "interpretation": "Curated benchmark semantic performance; not population intent accuracy or prospective EEG evidence."}
    _write_json(OUT / "semantic_context_summary.json", summary)

    PLOTS.mkdir(exist_ok=True)
    heldout_coverage = [row for row in coverage_rows if row["split"] == "held_out"]
    _svg_bar(PLOTS / "01_coverage_vs_semantic_precision.svg", "Held-out Coverage vs Active Context Precision", [r["coverageOperatingPoint"] for r in heldout_coverage], [r["activeContextPrecision"] or 0 for r in heldout_coverage])
    _svg_bar(PLOTS / "02_coverage_vs_wrong_active_rate.svg", "Held-out Coverage vs Wrong-active Context Rate", [r["coverageOperatingPoint"] for r in heldout_coverage], [r["wrongActiveRateAmongAllDecisions"] or 0 for r in heldout_coverage])
    families = [r for r in family_rows if r["coverageOperatingPoint"] == "C100"]
    _svg_bar(PLOTS / "03_scene_family_accuracy.svg", "Next-target Top-1 Accuracy by Scene Family", [r["sceneFamily"] for r in families], [r["nextTargetTop1Accuracy"] or 0 for r in families])
    rounds = [r for r in round_rows if r["coverageOperatingPoint"] == "C100"]
    _svg_bar(PLOTS / "04_round_index_context_quality.svg", "Top-1 Accuracy by Selection Round", [str(r["roundIndex"]) for r in rounds], [r["nextTargetTop1Accuracy"] or 0 for r in rounds])
    _svg_hist(PLOTS / "05_prior_entropy_distribution.svg", "Global Prior Normalized Entropy", [float(row["normalizedEntropy"]) for row in rows if row.get("normalizedEntropy") is not None])
    _svg_hist(PLOTS / "06_prior_top_mass_distribution.svg", "Global Prior Top-mass Distribution", [float(row["priorTopMass"]) for row in rows if row.get("priorTopMass") is not None])
    page_all = len(page_rows)
    _svg_bar(PLOTS / "07_page_projection_effect.svg", "Page Projection Summary", ["top preserved", "target present", "full page"],
             [_safe_rate(sum(r["pageTopMatchesGlobalTop"] for r in page_rows), page_all) or 0,
              _safe_rate(sum(r["acceptableTargetInPage"] for r in page_rows), page_all) or 0,
              _safe_rate(sum(r["pageSize"] == 3 for r in page_rows), page_all) or 0])
    lat = latency_summary["apiLatencyMs"]
    _svg_bar(PLOTS / "08_context_api_latency.svg", "Context API Latency (seconds)", ["median", "p90", "p95"], [lat["median"]/1000, lat["p90"]/1000, lat["p95"]/1000], y_max=max(1.0, lat["p95"]/1000*1.15), y_label="Seconds")

    return {"status": "PASS", "benchmarkSha256": lock["sha256"], "decisionPointCount": len(rows),
            "calibrationCount": len(calibration), "heldOutCoverage": heldout_coverage,
            "heldOutTop1Accuracy": overall["held_out"]["informativeTop1Accuracy"],
            "heldOutRepeatability": summary["repeatability"], "pageProjectionRows": page_all}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
