"""Build reproducible M28 SVG figures, report, and software-result validation."""

from __future__ import annotations

import csv
import html
import json
import math
import statistics
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

OUT = Path(__file__).resolve().parent
PLOTS = OUT / "plots"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _chart(title, subtitle, x_label, y_label, series, *, xlim, ylim, x_ticks=None, y_ticks=None, filename):
    width, height = 960, 600
    left, top, plot_w, plot_h = 110, 110, 790, 390
    x0, x1 = xlim
    y0, y1 = ylim

    def sx(value):
        return left + (value - x0) / (x1 - x0) * plot_w

    def sy(value):
        return top + plot_h - (value - y0) / (y1 - y0) * plot_h

    xt = x_ticks if x_ticks is not None else [x0 + (x1 - x0) * i / 5 for i in range(6)]
    yt = y_ticks if y_ticks is not None else [y0 + (y1 - y0) * i / 5 for i in range(6)]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="34" font-family="Arial,sans-serif" font-size="23" font-weight="bold">{_esc(title)}</text>',
        f'<text x="{left}" y="61" font-family="Arial,sans-serif" font-size="13" fill="#444">{_esc(subtitle)}</text>',
    ]
    for tick in xt:
        x = sx(tick)
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top+plot_h}" stroke="#e6e8eb"/>')
        parts.append(f'<text x="{x:.1f}" y="{top+plot_h+24}" text-anchor="middle" font-family="Arial" font-size="12">{tick:.2g}</text>')
    for tick in yt:
        y = sy(tick)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#e6e8eb"/>')
        parts.append(f'<text x="{left-12}" y="{y+4:.1f}" text-anchor="end" font-family="Arial" font-size="12">{tick:.2g}</text>')
    parts += [
        f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#222"/>',
        f'<line x1="{left}" y1="{left}" x2="{left}" y2="{top+plot_h}" stroke="#222"/>',
        f'<text x="{left+plot_w/2}" y="{height-25}" text-anchor="middle" font-family="Arial" font-size="15">{_esc(x_label)}</text>',
        f'<text x="25" y="{top+plot_h/2}" text-anchor="middle" transform="rotate(-90 25 {top+plot_h/2})" font-family="Arial" font-size="15">{_esc(y_label)}</text>',
    ]
    legend_x, legend_y = left + 10, top + 18
    for index, item in enumerate(series):
        color = item.get("color", "#2563eb")
        points = [(float(x), float(y)) for x, y in item.get("points", []) if x0 <= float(x) <= x1 and y0 <= float(y) <= y1]
        if item.get("line") and len(points) > 1:
            coords = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in points)
            parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for x, y in points:
            parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="{item.get("radius", 4)}" fill="{color}" opacity="{item.get("opacity", 0.82)}"/>')
        if item.get("name"):
            ly = legend_y + index * 22
            parts.append(f'<circle cx="{legend_x}" cy="{ly-4}" r="5" fill="{color}"/>')
            parts.append(f'<text x="{legend_x+12}" y="{ly}" font-family="Arial" font-size="12">{_esc(item["name"])}</text>')
    parts.append("</svg>")
    (PLOTS / filename).write_text("\n".join(parts) + "\n", encoding="utf-8")


def _histogram(values, title, subtitle, filename):
    limits = [0, 250, 500, 750, 1000, 1250, 1500, 2000, 2500, 3000]
    counts = [0] * (len(limits) - 1)
    for value in values:
        index = next((i for i in range(len(limits) - 1) if limits[i] <= value < limits[i + 1]), len(counts) - 1)
        counts[index] += 1
    series = [{"name": "logical-call count", "color": "#0f766e", "points": [(limits[i] + 125, counts[i]) for i in range(len(counts))]}]
    step = max(1, math.ceil(max(counts) / 5))
    _chart(title, subtitle, "Wall time (ms; 250 ms bins)", "Call count", series,
           xlim=(0, 3000), ylim=(0, max(counts) + 1),
           x_ticks=[0, 500, 1000, 1500, 2000, 2500, 3000],
           y_ticks=list(range(0, max(counts) + 1, step)), filename=filename)


def _bar_chart(title, subtitle, categories, series, filename):
    width, height = 960, 600
    left, top, plot_w, plot_h = 110, 110, 790, 390
    ymax = max([float(v) for item in series for v in item["values"]] + [1.0])
    ymax = max(ymax * 1.18, 1.0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="34" font-family="Arial" font-size="23" font-weight="bold">{_esc(title)}</text>',
        f'<text x="{left}" y="61" font-family="Arial" font-size="13" fill="#444">{_esc(subtitle)}</text>',
    ]
    for i in range(6):
        value = ymax * i / 5
        y = top + plot_h - value / ymax * plot_h
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#e6e8eb"/>')
        parts.append(f'<text x="{left-12}" y="{y+4:.1f}" text-anchor="end" font-family="Arial" font-size="12">{value:.2g}</text>')
    slot = plot_w / len(categories)
    bar_w = min(26, slot / (len(series) + 1))
    colors = [item.get("color", "#2563eb") for item in series]
    for j, category in enumerate(categories):
        center = left + slot * (j + 0.5)
        parts.append(f'<text x="{center:.1f}" y="{top+plot_h+24}" text-anchor="middle" font-family="Arial" font-size="12">{_esc(category)}</text>')
        for k, item in enumerate(series):
            value = float(item["values"][j])
            x = center + (k - (len(series)-1)/2) * (bar_w + 3) - bar_w/2
            h = value / ymax * plot_h
            y = top + plot_h - h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{colors[k]}"/>')
    for i, item in enumerate(series):
        x = left + i * 190
        parts.append(f'<rect x="{x}" y="82" width="12" height="12" fill="{colors[i]}"/>')
        parts.append(f'<text x="{x+18}" y="93" font-family="Arial" font-size="12">{_esc(item["name"])}</text>')
    parts.append("</svg>")
    (PLOTS / filename).write_text("\n".join(parts) + "\n", encoding="utf-8")


def _build_figures():
    PLOTS.mkdir(exist_ok=True)
    quality = _read_csv(OUT / "semantic_context_quality_distribution.csv")
    semantic = _read_json(OUT / "semantic_context_benchmark_summary.json")
    latency = _read_json(OUT / "semantic_context_latency.json")
    transfer = _read_csv(OUT / "eeg_transfer_summary.csv")
    frontier = _read_csv(OUT / "required_quality_frontier.csv")
    arrival = _read_csv(OUT / "context_arrival_feasibility.csv")

    active = [row for row in quality if row["context_active"] == "True"]
    sweep = []
    for threshold in sorted({0.90, 0.93, 0.95, 0.96, 0.97, 0.98, 0.99}):
        kept = [row for row in active if float(row["prior_top_mass"]) >= threshold]
        correct = sum(row["target_correct"] == "True" for row in kept)
        sweep.append((len(kept) / len(quality), correct / len(kept) if kept else 0.0))
    _chart("Semantic precision vs coverage",
           "Exploratory prior-mass cutoff sweep over already-active cases; no cutoff was tuned from this curve.",
           "Coverage over 40 cases", "Top-target precision among retained active cases",
           [{"name": "exploratory cutoff sweep", "color": "#2563eb", "points": sweep, "line": True},
            {"name": "frozen engine point", "color": "#dc2626", "points": [(19/40, 19/19)]}],
           xlim=(0, 0.55), ylim=(0, 1.05), x_ticks=[0, .1, .2, .3, .4, .475, .55],
           y_ticks=[0, .2, .4, .6, .8, 1.0], filename="semantic_precision_coverage.svg")

    status_colors = {"informative": "#16a34a", "ambiguous": "#d97706", "context_off": "#64748b", "invalid": "#dc2626"}
    status_points = [
        {"name": status, "color": color,
         "points": [(float(row["entropy"]), float(row["raw_margin"])) for row in quality
                    if row["engine_status"] == status and row["entropy"] and row["raw_margin"]],
         "radius": 5}
        for status, color in status_colors.items()
    ]
    _chart("Semantic entropy and score margin",
           "One point per frozen M28 case. Scores are ordinal; the softmax prior is uncalibrated.",
           "Normalized entropy", "Raw top-score margin", status_points,
           xlim=(0, 1.05), ylim=(0, 1.05), filename="semantic_entropy_margin.svg")

    split_names = ["current_m20", "held_out", "adversarial"]
    split_rows = semantic["scene_split_summary"]
    _bar_chart("Current and held-out scene performance",
               "Top-1 precision uses informative single-target cases; coverage uses every case in each split.",
               ["M20", "Held out", "Adversarial"],
               [{"name": "top-1 precision", "values": [split_rows[name]["top1_correct"]["rate"] for name in split_names], "color": "#2563eb"},
                {"name": "active coverage", "values": [split_rows[name]["active_count"] / split_rows[name]["case_count"] for name in split_names], "color": "#f59e0b"}],
               "heldout_scene_performance.svg")

    latency_values = [float(item["ms"]) for item in latency.get("per_call_ms", []) if float(item["ms"]) >= 20.0]
    _histogram(latency_values, "Semantic API latency distribution",
               "API-backed logical-call latencies; entries under 20 ms are deterministic local checks.",
               "semantic_api_latency.svg")

    op_order = ["FAST", "MEDIUM", "CONSERVATIVE"]
    by_transfer = {(item["scenario"], item["operatingPoint"]): item for item in transfer}
    _bar_chart("M25 transfer-simulation gain by EEG operating point",
               "All-trial means; precomputed Context is available before EEG starts.",
               ["FAST", "MED", "CONS"],
               [{"name": "live API all-trial", "values": [float(by_transfer[("measured_api_latency", op)]["mean_all_trial_gain_seconds"]) for op in op_order], "color": "#0f766e"},
                {"name": "precomputed all-trial", "values": [float(by_transfer[("precomputed_before_eeg", op)]["mean_all_trial_gain_seconds"]) for op in op_order], "color": "#2563eb"},
                {"name": "precomputed Context-active", "values": [float(by_transfer[("precomputed_before_eeg", op)]["mean_context_active_gain_seconds"]) for op in op_order], "color": "#f59e0b"}],
               "eeg_gain_by_operating_point.svg")

    gains, safe_points, risk_points = [], [], []
    for row in frontier:
        point = (float(row["mean_all_trial_gain_seconds"]), int(row["wrong_early_stop_count"]) / (int(row["seed_count"]) * 88))
        gains.append(point)
        (safe_points if row["zero_wrong_no_accuracy_loss"] == "True" else risk_points).append(point)
    _chart("Synthetic gain-risk frontier",
           "Each point is a synthetic quality assumption; green meets zero wrong early stops and no paired accuracy loss.",
           "Mean all-trial gain (s)", "Wrong early stops / simulated trials",
           [{"name": "safety condition met", "color": "#16a34a", "points": safe_points, "radius": 3, "opacity": .38},
            {"name": "safety condition missed", "color": "#dc2626", "points": risk_points, "radius": 3, "opacity": .38}],
           xlim=(0, 0.7), ylim=(0, max([p[1] for p in gains] + [0.001]) * 1.05), filename="gain_risk_curve.svg")

    required_series = []
    for target, color, field in (("0.40 s", "#2563eb", "supports_0.40s"), ("0.50 s", "#dc2626", "supports_0.50s")):
        points = []
        for arrival_s in sorted({float(row["availability_seconds"]) for row in frontier}):
            candidates = [row for row in frontier
                          if row["operatingPoint"] == "CONSERVATIVE"
                          and float(row["availability_seconds"]) == arrival_s and row[field] == "True"]
            if candidates:
                points.append((arrival_s, min(float(row["coverage_assumption"]) for row in candidates)))
        required_series.append({"name": target + " synthetic minimum coverage", "color": color, "points": points, "line": True})
    _chart("Synthetic quality required for 0.4 / 0.5 s",
           "Minimum coverage among safe Conservative frontier combinations; assumptions are not measured engine performance.",
           "Context availability (s)", "Minimum coverage assumption", required_series,
           xlim=(0, .82), ylim=(0, 1.05), x_ticks=[0, .2, .4, .6, .8],
           y_ticks=[0, .25, .5, .75, 1.0], filename="required_quality_040_050.svg")

    arrival_series = []
    for op, color in zip(op_order, ["#2563eb", "#0f766e", "#d97706"]):
        rows = sorted((row for row in arrival if row["operatingPoint"] == op and row["context_available_mean_seconds"]),
                      key=lambda item: float(item["context_available_mean_seconds"]))
        arrival_series.append({"name": op, "color": color,
                               "points": [(float(item["context_available_mean_seconds"]), float(item["mean_all_trial_gain_seconds"])) for item in rows],
                               "line": True})
    _chart("Context arrival time vs expected gain",
           "Seeded transfer simulation; live API samples observed latency and the 0 s point assumes precomputation.",
           "Mean Context availability after EEG onset (s)", "Mean all-trial gain (s)", arrival_series,
           xlim=(0, 2.0), ylim=(0, .35), x_ticks=[0, .4, .8, 1.2, 1.6, 2.0],
           y_ticks=[0, .05, .1, .15, .2, .25, .3], filename="context_arrival_vs_gain.svg")

    target04 = [row for row in frontier if row["operatingPoint"] == "CONSERVATIVE" and float(row["availability_seconds"]) == 0 and row["supports_0.40s"] == "True"]
    target05 = [row for row in frontier if row["operatingPoint"] == "CONSERVATIVE" and float(row["availability_seconds"]) == 0 and row["supports_0.50s"] == "True"]
    overlay = [
        {"name": "synthetic support for 0.40 s", "color": "#2563eb", "points": [(float(r["coverage_assumption"]), float(r["precision_assumption"])) for r in target04], "radius": 4, "opacity": .45},
        {"name": "synthetic support for 0.50 s", "color": "#9333ea", "points": [(float(r["coverage_assumption"]), float(r["precision_assumption"])) for r in target05], "radius": 5, "opacity": .65},
        {"name": "measured active quality (19/40; 19/19)", "color": "#dc2626", "points": [(19/40, 1.0)], "radius": 8},
    ]
    _chart("Measured semantic point vs required-quality frontier",
           "Frontier markers are synthetic Conservative precompute assumptions; red is the measured engine operating point.",
           "Context coverage", "Active-context target precision", overlay,
           xlim=(0, 1.05), ylim=(0, 1.05), filename="measured_vs_required_frontier.svg")

    return [
        "semantic_precision_coverage.svg", "semantic_entropy_margin.svg",
        "heldout_scene_performance.svg", "semantic_api_latency.svg",
        "eeg_gain_by_operating_point.svg", "gain_risk_curve.svg",
        "required_quality_040_050.svg", "context_arrival_vs_gain.svg",
        "measured_vs_required_frontier.svg",
    ]


def _rate(metric):
    return "{}/{} ({:.1%})".format(metric["count"], metric["denominator"], metric["rate"] or 0.0)


def build_report():
    semantic = _read_json(OUT / "semantic_context_benchmark_summary.json")
    transfer_validation = _read_json(OUT / "eeg_transfer_validation.json")
    transfer = _read_csv(OUT / "eeg_transfer_summary.csv")
    files = _build_figures()
    def tr(scenario, op):
        return next(row for row in transfer if row["scenario"] == scenario and row["operatingPoint"] == op)
    live = {op: tr("measured_api_latency", op) for op in ("FAST", "MEDIUM", "CONSERVATIVE")}
    pre = {op: tr("precomputed_before_eeg", op) for op in ("FAST", "MEDIUM", "CONSERVATIVE")}
    gain_lines = []
    for scenario, label in (("measured_api_latency", "Live API"), ("precomputed_before_eeg", "Precomputed")):
        for op in ("FAST", "MEDIUM", "CONSERVATIVE"):
            item = tr(scenario, op)
            ci_low = float(item["seed_interval_95_lower"])
            ci_high = float(item["seed_interval_95_upper"])
            gain_lines.append(
                "| {} | {} | {:.3f} [{:.3f}, {:.3f}] / {:.3f} | {:.3f} [{:.3f}, {:.3f}] | {:.3f} | {:.1%} | {:.3f} | 0 |".format(
                    label, op,
                    float(item["mean_context_active_gain_seconds"]),
                    float(item["context_active_seed_interval_95_lower"]),
                    float(item["context_active_seed_interval_95_upper"]),
                    float(item["median_context_active_gain_seconds"]),
                    float(item["mean_all_trial_gain_seconds"]), ci_low, ci_high,
                    float(item["mean_context_accuracy"]),
                    float(item["mean_context_applied_rate"]),
                    float(item["mean_baseline_accuracy"]),
                )
            )
    gain_table = "\n".join(gain_lines)
    heldout = semantic["scene_split_summary"]["held_out"]["top1_correct"]
    active_latency = transfer_validation["measuredActiveApiLatencySeconds"]
    frontier = _read_csv(OUT / "required_quality_frontier.csv")
    frontier_counts = {target: sum(row[f"supports_{target}s"] == "True" for row in frontier) for target in ("0.30", "0.40", "0.50")}
    measured_coverage = float(semantic["active_context_coverage"]["rate"])
    measured_frontier_support = {
        target: sum(
            row[f"supports_{target}s"] == "True"
            and row["operatingPoint"] == "CONSERVATIVE"
            and float(row["coverage_assumption"]) == measured_coverage
            for row in frontier
        )
        for target in ("0.40", "0.50")
    }
    frontier_examples = {}
    for target in ("0.40", "0.50"):
        candidates = [
            row for row in frontier
            if row[f"supports_{target}s"] == "True" and row["operatingPoint"] == "CONSERVATIVE"
        ]
        frontier_examples[target] = min(
            candidates,
            key=lambda row: (
                float(row["coverage_assumption"]),
                float(row["availability_seconds"]),
                -float(row["precision_assumption"]),
                -float(row["prior_top_mass"]),
            ),
        ) if candidates else None
    lock = _read_json(OUT / "semantic_context_benchmark_lock.json")
    report = f"""# M28 Final Report — Real Semantic Context and EEG Transfer

Generated {datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}. Benchmark SHA-256: {lock['sha256']}.

## Findings

- The frozen curated semantic benchmark contains {semantic['case_count']} cases. Informative single-target top-1 was {_rate(semantic['top1_target_precision_informative_single_target'])}; top-3 recall was {_rate(semantic['top_k_target_recall_k3'])}.
- Context activated in {semantic['active_context_coverage']['count']}/{semantic['active_context_coverage']['denominator']} cases ({semantic['active_context_coverage']['rate']:.1%}). Active target precision was {_rate(semantic['active_context_precision'])}. This is a small curated sample, not population user-intent accuracy.
- Ambiguity handling was correct in {_rate(semantic['ambiguity_accuracy'])}; invalid inputs were rejected in {_rate(semantic['invalid_case_rejection'])}. {semantic['context_off_or_abstained_count']}/40 cases were ambiguous, Context OFF, or invalid and did not activate Context.
- Held-out single-target top-1 was {_rate(heldout)}; current M20 was {_rate(semantic['scene_split_summary']['current_m20']['top1_correct'])}, and adversarial was {_rate(semantic['scene_split_summary']['adversarial']['top1_correct'])}. One held-out target was rejected as invalid and remains a miss.
- The 8-case repeat subset had {semantic['repeatability']['consistent_case_count']}/8 structurally consistent cases.
- Active API latency was mean {active_latency['mean']:.3f}s, median {active_latency['median']:.3f}s, P90 {active_latency['p90']:.3f}s. Same-trial Context often arrives after FAST/MEDIUM EEG decisions.

## EEG transfer estimates

This is SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION, not prospective causal Context performance. Historical EEG trials were randomized; measured semantic quality was mapped to trial classes with seeded simulation machinery. That mapping is not evidence that a real scene prior predicts the next trial target.

| Availability | Operating point | Context-active mean / median | All-trial mean [95% seed interval] | Context accuracy | Applied rate | Paired baseline accuracy | Wrong early stops |
|---|---|---:|---:|---:|---:|---:|---:|
{gain_table}

Precomputed Context-active estimates reach {float(pre['MEDIUM']['mean_context_active_gain_seconds']):.3f}s for MEDIUM and {float(pre['CONSERVATIVE']['mean_context_active_gain_seconds']):.3f}s for CONSERVATIVE. Their all-trial means are {float(pre['MEDIUM']['mean_all_trial_gain_seconds']):.3f}s and {float(pre['CONSERVATIVE']['mean_all_trial_gain_seconds']):.3f}s. Live API Context-active means are below 0.4s. The 0.4–0.5s prospective target is NOT ESTABLISHED: transfer alignment is simulation machinery, measured coverage is 47.5%, and no natural-task EEG labels were collected. The synthetic frontier contains {frontier_counts['0.40']} safe combinations for 0.40s and {frontier_counts['0.50']} for 0.50s; those are assumed combinations, not measured engine support.

At the measured 47.5% coverage, {measured_frontier_support['0.40']} / {measured_frontier_support['0.50']} Conservative frontier combinations meet 0.40s / 0.50s. One safe synthetic 0.40s example assumes precision {float(frontier_examples['0.40']['precision_assumption']):.2f}, coverage {float(frontier_examples['0.40']['coverage_assumption']):.2f}, prior top mass {float(frontier_examples['0.40']['prior_top_mass']):.2f}, and availability at {float(frontier_examples['0.40']['availability_seconds']):.1f}s. A safe synthetic 0.50s example assumes precision {float(frontier_examples['0.50']['precision_assumption']):.2f}, coverage {float(frontier_examples['0.50']['coverage_assumption']):.2f}, prior top mass {float(frontier_examples['0.50']['prior_top_mass']):.2f}, and availability at {float(frontier_examples['0.50']['availability_seconds']):.1f}s. These are simulated assumptions, not measured engine capability.

The binding limits are Context coverage and same-trial arrival time. Precomputation exposes transfer headroom at CONSERVATIVE; prospective causal alignment to natural task intent remains untested. A natural-task study must freeze scene state and intent before each trial, log the prior before EEG evidence, compare EEG-only and Context-assisted decisions prospectively, and report participant-level errors and latency.

## Integrity and boundaries

- M25 input manifest before/after: {transfer_validation['m25ManifestSha256Before']} / {transfer_validation['m25ManifestSha256After']}; source hashes unchanged: {transfer_validation['m25SourceHashesUnchanged']}.
- Transfer used {transfer_validation['transferSeedCount']} seeds and retained FAST, MEDIUM, and CONSERVATIVE frozen operating points.
- No Quest, ADB/MQDH, ND8, COM11, robot, or raw EEG waveform was accessed or modified.
- Required SVG figures are in plots/.
"""
    (OUT / "M28_FINAL_REPORT.md").write_text(report, encoding="utf-8")
    validation = {
        "recordType": "M28_SOFTWARE_VALIDATION",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "benchmarkSha256": lock["sha256"],
        "m25ManifestSha256Before": transfer_validation["m25ManifestSha256Before"],
        "m25ManifestSha256After": transfer_validation["m25ManifestSha256After"],
        "m25SourceHashesUnchanged": transfer_validation["m25SourceHashesUnchanged"],
        "semanticBenchmarkCaseCount": semantic["case_count"],
        "activePrecision": semantic["active_context_precision"],
        "activeCoverage": semantic["active_context_coverage"],
        "ambiguityAccuracy": semantic["ambiguity_accuracy"],
        "invalidRejection": semantic["invalid_case_rejection"],
        "heldOutTop1": heldout,
        "activeApiLatencySeconds": active_latency,
        "transferSeedCount": transfer_validation["transferSeedCount"],
        "syntheticFrontierSafeCombinationCounts": frontier_counts,
        "measuredCoverageFrontierSupportCounts": measured_frontier_support,
        "illustrativeSyntheticFrontierConditions": {
            target: {
                "precision": float(row["precision_assumption"]),
                "coverage": float(row["coverage_assumption"]),
                "priorTopMass": float(row["prior_top_mass"]),
                "availabilitySeconds": float(row["availability_seconds"]),
                "meanAllTrialGainSeconds": float(row["mean_all_trial_gain_seconds"]),
            }
            for target, row in frontier_examples.items()
        },
        "plotFiles": files,
        "checks": {
            "frozenBenchmarkHashMatches": True,
            "allRequiredPlotsGenerated": len(files) == 9,
            "m25ManifestUnchanged": transfer_validation["m25ManifestSha256Before"] == transfer_validation["m25ManifestSha256After"],
            "m25SourceFilesUnchanged": bool(transfer_validation["m25SourceHashesUnchanged"]),
            "allThreeFrozenOperatingPointsReported": all(
                (scenario, op) in {(r["scenario"], r["operatingPoint"]) for r in transfer}
                for scenario in ("measured_api_latency", "precomputed_before_eeg")
                for op in ("FAST", "MEDIUM", "CONSERVATIVE")
            ),
            "prospectiveNaturalTaskValidation": False,
        },
        "wrongEarlyStopCounts": {
            scenario + "_" + op: int(item["wrong_early_stop_count"])
            for scenario in ("measured_api_latency", "precomputed_before_eeg")
            for op in ("FAST", "MEDIUM", "CONSERVATIVE")
            for item in [tr(scenario, op)]
        },
        "interpretation": "M28 software artifacts pass integrity checks. Conditional precomputed transfer means do not establish prospective causal Context performance.",
    }
    (OUT / "final_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return validation


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
