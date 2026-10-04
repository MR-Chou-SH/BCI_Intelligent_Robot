#!/usr/bin/env python3
"""Generate deterministic, dependency-free M36 SVG figures and the central table."""

import csv
import html
import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PLOTS = ROOT / "plots"
COLORS = {
    "M35_CONSERVATIVE": "#526579",
    "M36_MULTI_VIEW_RULE": "#d1495b",
    "M36_LOGISTIC_SAFE_GATE": "#00798c",
    "M36_LOGISTIC_VIEW_GUARDED": "#edae49",
    "EEG-only": "#293241",
    "Oracle multi-view": "#7b2cbf",
    "Stored M33 through view guard": "#5a9367",
}
METHODS = (
    ("M35_CONSERVATIVE", "FIXED", "M35 conservative"),
    ("M36_MULTI_VIEW_RULE", "SAFE-STRICT", "M36 multi-view rule"),
    ("M36_LOGISTIC_SAFE_GATE", "SAFE-STRICT", "M36 logistic"),
    ("M36_LOGISTIC_VIEW_GUARDED", "SAFE-STRICT", "M36 logistic + view guard"),
)


def read_csv(name):
    with (ROOT / name).open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def f(row, key, default=None):
    value = row.get(key, "")
    return default if value in (None, "", "None") else float(value)


def write_csv(path, rows):
    if not rows:
        raise ValueError("refusing to write a headerless empty table: {}".format(path.name))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def esc(value):
    return html.escape(str(value), quote=True)


def fmt_tick(value, style):
    if style == "percent":
        return "{:.0f}%".format(value * 100)
    if style == "ms":
        return "{:.0f}".format(value * 1000)
    if style == "seconds":
        return "{:.2f}".format(value)
    if style == "integer":
        return str(int(round(value)))
    return "{:.2f}".format(value)


def line_chart(filename, title, xlabel, ylabel, series, xlim, ylim, xstyle="number", ystyle="number", refs=(), note=""):
    width, height = 1080, 720
    left, right, top, bottom = 110, 40, 85, 165
    plot_w, plot_h = width - left - right, height - top - bottom
    x0, x1 = xlim
    y0, y1 = ylim
    if x1 <= x0 or y1 <= y0:
        raise ValueError("invalid plot range")
    sx = lambda x: left + (x - x0) / (x1 - x0) * plot_w
    sy = lambda y: top + (1.0 - (y - y0) / (y1 - y0)) * plot_h
    body = [
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="{}" y="42" text-anchor="middle" font-family="Arial,sans-serif" font-size="25" font-weight="700" fill="#17212b">{}</text>'.format(width / 2, esc(title)),
    ]
    for i in range(6):
        y = y0 + (y1 - y0) * i / 5
        py = sy(y)
        body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#e5e9ed"/>'.format(left, py, width - right, py))
        body.append('<text x="{}" y="{}" text-anchor="end" dominant-baseline="middle" font-family="Arial,sans-serif" font-size="13" fill="#52606d">{}</text>'.format(left - 12, py, fmt_tick(y, ystyle)))
    for i in range(6):
        x = x0 + (x1 - x0) * i / 5
        px = sx(x)
        body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#f0f2f4"/>'.format(px, top, px, top + plot_h))
        body.append('<text x="{}" y="{}" text-anchor="middle" font-family="Arial,sans-serif" font-size="13" fill="#52606d">{}</text>'.format(px, top + plot_h + 24, fmt_tick(x, xstyle)))
    for x, label, color in refs:
        if x0 <= x <= x1:
            px = sx(x)
            body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="2" stroke-dasharray="7 5"/>'.format(px, top, px, top + plot_h, color))
            body.append('<text x="{}" y="{}" transform="rotate(-90 {} {})" text-anchor="end" font-family="Arial,sans-serif" font-size="12" fill="{}">{}</text>'.format(px - 5, top + 9, px - 5, top + 9, color, esc(label)))
    body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#263238" stroke-width="1.5"/>'.format(left, top + plot_h, width - right, top + plot_h))
    body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#263238" stroke-width="1.5"/>'.format(left, top, left, top + plot_h))
    for item in series:
        points = [(x, y) for x, y in item["points"] if x0 <= x <= x1 and y0 <= y <= y1]
        if len(points) >= 2:
            coords = " ".join("{:.2f},{:.2f}".format(sx(x), sy(y)) for x, y in points)
            dash = ' stroke-dasharray="8 5"' if item.get("dash") else ""
            body.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="2.6"{} stroke-linejoin="round" stroke-linecap="round"/>'.format(coords, item["color"], dash))
        for x, y in points:
            body.append('<circle cx="{:.2f}" cy="{:.2f}" r="4.1" fill="{}" stroke="#ffffff" stroke-width="1.2"/>'.format(sx(x), sy(y), item["color"]))
    body.append('<text x="{}" y="{}" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#17212b">{}</text>'.format(left + plot_w / 2, height - 96, esc(xlabel)))
    body.append('<text x="28" y="{}" transform="rotate(-90 28 {})" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#17212b">{}</text>'.format(top + plot_h / 2, top + plot_h / 2, esc(ylabel)))
    if note:
        body.append('<text x="{}" y="{}" text-anchor="middle" font-family="Arial,sans-serif" font-size="12" fill="#53616c">{}</text>'.format(width / 2, height - 80, esc(note)))
    legend_y = height - 26
    cols = 4
    cell_w = (width - 100) / cols
    for index, item in enumerate(series):
        col, row = index % cols, index // cols
        lx, ly = 55 + col * cell_w, legend_y - row * 22
        dash = ' stroke-dasharray="7 4"' if item.get("dash") else ""
        body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="3"{} />'.format(lx, ly - 4, lx + 30, ly - 4, item["color"], dash))
        body.append('<text x="{}" y="{}" font-family="Arial,sans-serif" font-size="12" fill="#29343b">{}</text>'.format(lx + 38, ly, esc(item["name"])))
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" viewBox="0 0 {} {}">{}</svg>'.format(width, height, width, height, "".join(body))
    (PLOTS / filename).write_text(svg, encoding="utf-8")


def multi_panel_bars(filename, title, categories, series, panels, note=""):
    width, height = 1320, 780
    body = ['<rect width="100%" height="100%" fill="#ffffff"/>',
            '<text x="{}" y="38" text-anchor="middle" font-family="Arial,sans-serif" font-size="24" font-weight="700" fill="#17212b">{}</text>'.format(width / 2, esc(title))]
    panel_top, panel_h, panel_w, gap = 82, 500, 585, 70
    for panel_index, panel in enumerate(panels):
        left = 75 + panel_index * (panel_w + gap)
        right = left + panel_w - 20
        top, bottom = panel_top, panel_top + panel_h
        lo, hi = panel["ylim"]
        sy = lambda y: top + (1 - (y - lo) / (hi - lo)) * panel_h
        body.append('<text x="{}" y="{}" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" font-weight="600" fill="#27343d">{}</text>'.format((left + right) / 2, top - 18, esc(panel["title"])))
        for j in range(6):
            v = lo + (hi - lo) * j / 5
            py = sy(v)
            body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#e7ebef"/>'.format(left, py, right, py))
            body.append('<text x="{}" y="{}" text-anchor="end" dominant-baseline="middle" font-family="Arial,sans-serif" font-size="11" fill="#52606d">{}</text>'.format(left - 8, py, fmt_tick(v, panel.get("style", "percent"))))
        group_w = (right - left) / max(1, len(categories))
        bar_w = min(14, group_w / (len(series) + 1))
        for category_index, category in enumerate(categories):
            center = left + (category_index + 0.5) * group_w
            label = category.replace("_", "\n")
            body.append('<text x="{}" y="{}" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" fill="#37434b">{}</text>'.format(center, bottom + 18, esc(category)))
            for series_index, item in enumerate(series):
                value = panel["values"].get((category, item["key"]))
                if value is None:
                    continue
                x = center - len(series) * bar_w / 2 + series_index * bar_w
                y = sy(max(lo, min(hi, value)))
                zero_y = sy(lo)
                bar_height = max(1, zero_y - y)
                body.append('<rect x="{}" y="{}" width="{}" height="{}" fill="{}" opacity="0.88"><title>{}: {}={:.3f}</title></rect>'.format(x, y, bar_w - 2, bar_height, item["color"], esc(category), esc(item["label"]), value))
        body.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#263238"/>'.format(left, bottom, right, bottom))
    legend_y = height - 100
    cell_w = 240
    for index, item in enumerate(series):
        x = 160 + index * cell_w
        body.append('<rect x="{}" y="{}" width="14" height="14" fill="{}"/>'.format(x, legend_y, item["color"]))
        body.append('<text x="{}" y="{}" font-family="Arial,sans-serif" font-size="12" fill="#263238">{}</text>'.format(x + 21, legend_y + 12, esc(item["label"])))
    if note:
        body.append('<text x="{}" y="{}" text-anchor="middle" font-family="Arial,sans-serif" font-size="12" fill="#53616c">{}</text>'.format(width / 2, height - 45, esc(note)))
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" viewBox="0 0 {} {}">{}</svg>'.format(width, height, width, height, "".join(body))
    (PLOTS / filename).write_text(svg, encoding="utf-8")


def primary_map(rows):
    return {(r["track"], r["groupingScheme"], r["method"], r["safetyOperatingPoint"], r["condition"], r["qTop"]): r for r in rows}


def make_success_table(controlled, oof, oracle, m33_json, m33_rows):
    table = primary_map(controlled)
    out = []
    for track in ("track1_5ch", "track2_common3"):
        oracle_row = next(r for r in oracle["summaries"] if r["track"] == track and r["groupingScheme"] == "acquisitionCampaignGroup" and r["oracleCondition"] == "oracle_multiview_band_harmonic")
        baseline_rows = [r for r in oof if r["track"] == track and r["groupingScheme"] == "acquisitionCampaignGroup"
                         and r["method"] == "M35_CONSERVATIVE" and r["condition"] == "NEUTRAL_CONTEXT" and r["qTop"] == ""]
        by_trial = {r["trialKey"]: r for r in baseline_rows}
        baseline = list(by_trial.values())
        out.append({
            "track": track, "method": "EEG_ONLY", "safetyOperatingPoint": "FROZEN_BASELINE",
            "realEegTrials": len(baseline), "contextReplayScenarios": len(baseline), "applications": 0,
            "coverage": 0.0, "interventionPrecisionUnderWrongContext": "",
            "wrongContextApplications": 0, "wrongContextInducedErrors": 0,
            "meanEvidenceSeconds": sum(float(r["baselineEvidenceSeconds"]) for r in baseline) / len(baseline),
            "overallMeanGainSeconds": 0.0, "appliedTrialMeanGainSeconds": "",
            "fractionEvidenceLE030": sum(float(r["baselineEvidenceSeconds"]) <= 0.30 + 1e-12 for r in baseline) / len(baseline),
            "accuracy": sum(int(r["baselineCorrect"]) for r in baseline) / len(baseline),
            "pairedAccuracyDeltaVsEEGOnly": 0.0, "oracleMeanGainSeconds": oracle_row["meanOracleGainSeconds"], "oracleEfficiency": 0.0,
            "evidenceSource": "one frozen EEG-only decision per real EEG trial",
        })
        candidates = [METHODS[0], METHODS[1], METHODS[2], METHODS[3]]
        for method, safety, _ in candidates:
            cong = table[(track, "acquisitionCampaignGroup", method, safety, "CONGRUENT_CONTEXT", "0.95")]
            inc = table[(track, "acquisitionCampaignGroup", method, safety, "INCONGRUENT_CONTEXT", "0.95")]
            out.append({
                "track": track, "method": method, "safetyOperatingPoint": safety,
                "realEegTrials": int(cong["realEegTrials"]), "contextReplayScenarios": int(cong["contextReplayScenarios"]),
                "applications": int(cong["contextApplications"]), "coverage": float(cong["coveragePerReplayScenario"]),
                "interventionPrecisionUnderWrongContext": inc["scenarioInterventionPrecision"],
                "wrongContextApplications": int(inc["contextApplications"]),
                "wrongContextInducedErrors": int(inc["inducedWrongEarlyStopEvents"]),
                "meanEvidenceSeconds": float(cong["meanEvidenceSeconds"]),
                "overallMeanGainSeconds": float(cong["overallMeanGainSeconds"]),
                "appliedTrialMeanGainSeconds": cong["appliedTrialMeanGainSeconds"],
                "fractionEvidenceLE030": float(cong["fractionTrialsLE030"]),
                "accuracy": float(cong["conservativePerTrialAccuracy"]),
                "pairedAccuracyDeltaVsEEGOnly": float(cong["conservativePairedAccuracyDelta"]),
                "oracleMeanGainSeconds": oracle_row["meanOracleGainSeconds"],
                "oracleEfficiency": float(cong["overallMeanGainSeconds"]) / oracle_row["meanOracleGainSeconds"] if oracle_row["meanOracleGainSeconds"] > 0 else "",
                "evidenceSource": "primary acquisition-campaign OOF; q_top=0.95; wrong-context row tests both wrong targets",
            })
        baseline_accuracy = out[-5]["accuracy"]
        out.append({
            "track": track, "method": "ORACLE_MULTIVIEW_UPPER_BOUND", "safetyOperatingPoint": "GROUND_TRUTH_ORACLE",
            "realEegTrials": oracle_row["realEegTrials"], "contextReplayScenarios": oracle_row["realEegTrials"],
            "applications": round(oracle_row["applicationRate"] * oracle_row["realEegTrials"]),
            "coverage": oracle_row["applicationRate"], "interventionPrecisionUnderWrongContext": "",
            "wrongContextApplications": "", "wrongContextInducedErrors": 0,
            "meanEvidenceSeconds": oracle_row["meanEvidenceSeconds"], "overallMeanGainSeconds": oracle_row["meanOracleGainSeconds"],
            "appliedTrialMeanGainSeconds": oracle_row["meanGainAmongAcceleratedSeconds"],
            "fractionEvidenceLE030": oracle_row["<=300msFraction"], "accuracy": oracle_row["accuracy"],
            "pairedAccuracyDeltaVsEEGOnly": oracle_row["accuracy"] - baseline_accuracy,
            "oracleMeanGainSeconds": oracle_row["meanOracleGainSeconds"], "oracleEfficiency": 1.0,
            "evidenceSource": "upper bound uses ground truth to find early correct EEG top; never a deployable gate",
        })
    stored = [r for r in m33_rows if r["method"] == "M36_LOGISTIC_VIEW_GUARDED" and r["safetyOperatingPoint"] == "SAFE-STRICT"]
    n_scenarios = len(stored)
    n_apps = sum(int(r["contextAppliedByM36Gate"]) for r in stored)
    applied = [r for r in stored if int(r["contextAppliedByM36Gate"])]
    oracle_track2 = next(r for r in oracle["summaries"] if r["track"] == "track2_common3" and r["groupingScheme"] == "acquisitionCampaignGroup" and r["oracleCondition"] == "oracle_multiview_band_harmonic")
    gain = sum(float(r["pairedGainSeconds"]) for r in stored) / n_scenarios if n_scenarios else 0.0
    out.append({
        "track": "track2_common3_stored_M33", "method": "STORED_M33_THROUGH_M36_LOGISTIC_VIEW_GUARD", "safetyOperatingPoint": "SAFE-STRICT",
        "realEegTrials": len({r["trialKey"] for r in stored}), "contextReplayScenarios": n_scenarios,
        "applications": n_apps, "coverage": n_apps / n_scenarios if n_scenarios else 0.0,
        "interventionPrecisionUnderWrongContext": "not estimated: stored real semantic context is replayed, not randomized into both wrong targets",
        "wrongContextApplications": "", "wrongContextInducedErrors": sum(int(r["contextInducedWrongEarlyStop"]) for r in stored),
        "meanEvidenceSeconds": sum(float(r["selectedEvidenceSeconds"]) for r in stored) / n_scenarios,
        "overallMeanGainSeconds": gain,
        "appliedTrialMeanGainSeconds": sum(float(r["pairedGainSeconds"]) for r in applied) / len(applied) if applied else "",
        "fractionEvidenceLE030": sum(float(r["selectedEvidenceSeconds"]) <= 0.30 + 1e-12 for r in stored) / n_scenarios,
        "accuracy": sum(int(r["selectedCorrect"]) for r in stored) / n_scenarios,
        "pairedAccuracyDeltaVsEEGOnly": sum(int(r["selectedCorrect"]) - int(r["baselineCorrect"]) for r in stored) / n_scenarios,
        "oracleMeanGainSeconds": oracle_track2["meanOracleGainSeconds"],
        "oracleEfficiency": gain / oracle_track2["meanOracleGainSeconds"] if oracle_track2["meanOracleGainSeconds"] > 0 else "",
        "evidenceSource": "5,900 stored precomputed-before-EEG semantic scenarios / 59 real EEG trials; repeated seeds are not independent",
    })
    write_csv(ROOT / "primary_success_table.csv", out)
    return out


def make_safety_sweep(controlled):
    by = primary_map(controlled)
    rows = []
    for track in ("track1_5ch", "track2_common3"):
        for method, safety, label in METHODS:
            for q in ("0.45", "0.50", "0.55", "0.60", "0.65", "0.70", "0.75", "0.80", "0.85", "0.90", "0.95"):
                congruent = by[(track, "acquisitionCampaignGroup", method, safety, "CONGRUENT_CONTEXT", q)]
                incongruent = by[(track, "acquisitionCampaignGroup", method, safety, "INCONGRUENT_CONTEXT", q)]
                wrong_apps = int(incongruent["contextApplications"])
                induced = int(incongruent["inducedWrongEarlyStopEvents"])
                wrong_precision = f(incongruent, "scenarioInterventionPrecision")
                rows.append({
                    "track": track,
                    "method": method,
                    "safetyOperatingPoint": safety,
                    "qTop": float(q),
                    "congruentApplications": int(congruent["contextApplications"]),
                    "congruentCoverage": float(congruent["coveragePerReplayScenario"]),
                    "wrongContextApplications": wrong_apps,
                    "inducedWrongEarlyStops": induced,
                    "wrongContextInterventionPrecision": wrong_precision if wrong_precision is not None else "",
                    "allWrongContextApplicationsCorrect": (wrong_precision == 1.0) if wrong_apps else "",
                    "inducedRiskAmongWrongContextApplications": induced / wrong_apps if wrong_apps else "",
                    "riskEstimable": wrong_apps > 0,
                    "zeroObservedInducedErrors": induced == 0,
                    "interpretation": "observed zero errors; wrong-context risk denominator is zero" if wrong_apps == 0 and induced == 0 else "risk evaluated on applied wrong-context replay scenarios",
                })
    with (ROOT / "q_sweep_safety_frontier.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    common = []
    keys = sorted({(r["method"], r["safetyOperatingPoint"], r["qTop"]) for r in rows})
    for method, safety, q in keys:
        pair = [r for r in rows if r["method"] == method and r["safetyOperatingPoint"] == safety and r["qTop"] == q]
        if len(pair) == 2 and all(r["congruentApplications"] > 0 and r["inducedWrongEarlyStops"] == 0 for r in pair):
            common.append({
                "method": method,
                "safetyOperatingPoint": safety,
                "qTop": q,
                "track1Applications": next(r["congruentApplications"] for r in pair if r["track"] == "track1_5ch"),
                "track1Coverage": next(r["congruentCoverage"] for r in pair if r["track"] == "track1_5ch"),
                "track1WrongContextApplications": next(r["wrongContextApplications"] for r in pair if r["track"] == "track1_5ch"),
                "track1WrongContextPrecision": next(r["wrongContextInterventionPrecision"] for r in pair if r["track"] == "track1_5ch"),
                "track2Applications": next(r["congruentApplications"] for r in pair if r["track"] == "track2_common3"),
                "track2Coverage": next(r["congruentCoverage"] for r in pair if r["track"] == "track2_common3"),
                "track2WrongContextApplications": next(r["wrongContextApplications"] for r in pair if r["track"] == "track2_common3"),
                "track2WrongContextPrecision": next(r["wrongContextInterventionPrecision"] for r in pair if r["track"] == "track2_common3"),
                "crossTrackZeroErrorObserved": True,
                "precisionEstimateSupported": any(r["wrongContextApplications"] > 0 for r in pair),
                "crossTrackNoWrongContextApplications": all(r["wrongContextApplications"] == 0 for r in pair),
                "crossTrackAllObservedWrongContextInterventionsCorrect": all(r["wrongContextApplications"] == 0 or r["wrongContextInterventionPrecision"] == 1.0 for r in pair),
            })
    no_wrong_apps = [r for r in common if r["crossTrackNoWrongContextApplications"]]
    best_no_wrong_apps = max(no_wrong_apps, key=lambda r: r["track1Applications"] + r["track2Applications"]) if no_wrong_apps else None
    summary = {
        "status": "PASS",
        "primaryGrouping": "acquisitionCampaignGroup",
        "qSweepPredeclared": [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95],
        "trackwiseZeroObservedErrorFrontierRows": len(common),
        "crossTrackZeroErrorPoints": common,
        "bestCommonPointWithNoWrongContextApplications": best_no_wrong_apps,
        "interpretation": "Distinguish induced baseline errors from incorrect Context interventions. Zero observed induced errors with no wrong-context applications is not an estimated zero risk; all q points are descriptive predeclared sweep results, not independent held-out operating points.",
    }
    (ROOT / "q_sweep_safety_frontier.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def make_frontier_figures(controlled):
    by = primary_map(controlled)
    tracks = ("track1_5ch", "track2_common3")
    series_specs = []
    for track_index, track in enumerate(tracks):
        for method, safety, label in METHODS:
            name = ("T1 " if track_index == 0 else "T2 ") + label
            series_specs.append((track, method, safety, name, COLORS[method], bool(track_index)))

    figures = []
    for figure_number, metric, ylabel, ystyle, ylim, filename, title in (
        (1, "risk", "Incongruent induced risk among wrong-context applications", "percent", (0.0, 1.0), "fig01_risk_coverage.svg", "Context risk–coverage frontier"),
        (2, "gain", "Overall paired mean evidence gain (s)", "seconds", (0.0, 0.10), "fig02_coverage_overall_gain.svg", "Context coverage versus overall gain"),
        (3, "precision", "Intervention precision under incongruent Context", "percent", (0.0, 1.0), "fig03_precision_coverage.svg", "Wrong-context intervention precision versus coverage"),
        (4, "applied_gain", "Mean gain among congruent applications (s)", "seconds", (0.0, 0.50), "fig04_applied_gain_coverage.svg", "Applied-trial gain versus coverage"),
        (5, "le300", "Fraction of congruent decisions at or below 300 ms", "percent", (0.0, 1.0), "fig05_fast_fraction_coverage.svg", "Fast-decision fraction versus coverage"),
    ):
        plot_series = []
        for track, method, safety, name, color, dash in series_specs:
            points = []
            for q in ("0.45", "0.50", "0.55", "0.60", "0.65", "0.70", "0.75", "0.80", "0.85", "0.90", "0.95"):
                congruent = by.get((track, "acquisitionCampaignGroup", method, safety, "CONGRUENT_CONTEXT", q))
                incongruent = by.get((track, "acquisitionCampaignGroup", method, safety, "INCONGRUENT_CONTEXT", q))
                if not congruent or not incongruent:
                    continue
                x = float(congruent["coveragePerReplayScenario"])
                if metric == "risk":
                    denom = int(incongruent["contextApplications"])
                    if denom == 0:
                        continue
                    y = int(incongruent["inducedWrongEarlyStopEvents"]) / denom
                elif metric == "precision":
                    value = f(incongruent, "scenarioInterventionPrecision")
                    if value is None:
                        continue
                    y = value
                elif metric == "gain":
                    y = float(congruent["overallMeanGainSeconds"])
                elif metric == "applied_gain":
                    value = f(congruent, "appliedTrialMeanGainSeconds")
                    if value is None:
                        continue
                    y = value
                else:
                    y = float(congruent["fractionTrialsLE030"])
                points.append((x, y))
            points.sort()
            plot_series.append({"name": name, "points": points, "color": color, "dash": dash})
        figures.append((filename, plot_series))
        line_chart(filename, title, "Congruent Context application rate / eligible trial", ylabel, plot_series,
                   (0.0, 0.55), ylim, "percent", ystyle,
                   note="Primary acquisition-campaign OOF; q_top swept 0.45–0.95; dashed lines denote Track 2.")
    return [name for name, _ in figures]


def group_figure(group_rows):
    method_specs = [
        ("M35_CONSERVATIVE", "FIXED", "M35", COLORS["M35_CONSERVATIVE"]),
        ("M36_MULTI_VIEW_RULE", "SAFE-STRICT", "M36 rule", COLORS["M36_MULTI_VIEW_RULE"]),
        ("M36_LOGISTIC_SAFE_GATE", "SAFE-STRICT", "M36 logistic", COLORS["M36_LOGISTIC_SAFE_GATE"]),
        ("M36_LOGISTIC_VIEW_GUARDED", "SAFE-STRICT", "View-guard logistic", COLORS["M36_LOGISTIC_VIEW_GUARDED"]),
    ]
    selected = [r for r in group_rows if r["groupingScheme"] == "acquisitionCampaignGroup" and r["qTop"] == "0.95"]
    cats = []
    for track, sessions in (("track1_5ch", ("A", "B1", "B2")), ("track2_common3", ("A", "B1", "B2", "S7"))):
        for session in sessions:
            cat = "T1_" + session if track == "track1_5ch" else "T2_" + session
            if cat not in cats:
                cats.append(cat)
    series = [{"key": m, "label": label, "color": color} for m, _, label, color in method_specs]
    accuracy_values, risk_values = {}, {}
    for track, session in [("track1_5ch", s) for s in ("A", "B1", "B2")] + [("track2_common3", s) for s in ("A", "B1", "B2", "S7")]:
        cat = ("T1_" if track == "track1_5ch" else "T2_") + session
        for method, safety, _, _ in method_specs:
            cong = next((r for r in selected if r["track"] == track and r["sessionKey"] == session and r["method"] == method and r["safetyOperatingPoint"] == safety and r["condition"] == "CONGRUENT_CONTEXT"), None)
            inc = next((r for r in selected if r["track"] == track and r["sessionKey"] == session and r["method"] == method and r["safetyOperatingPoint"] == safety and r["condition"] == "INCONGRUENT_CONTEXT"), None)
            if cong:
                accuracy_values[(cat, method)] = float(cong["scenarioAccuracy"])
            if inc:
                risk_values[(cat, method)] = int(inc["trialsWithAnyInducedWrongStop"]) / int(inc["realEegTrials"])
    multi_panel_bars(
        "fig06_group_accuracy_risk.svg", "Acquisition-group accuracy and induced-error risk at q_top=.95", cats, series,
        [
            {"title": "Congruent-context scenario accuracy", "values": accuracy_values, "ylim": (0.0, 1.0), "style": "percent"},
            {"title": "Wrong-context trials with ≥1 induced stop", "values": risk_values, "ylim": (0.0, 0.6), "style": "percent"},
        ],
        note="Primary acquisition-campaign OOF; risk denominator is real EEG trials within each test group; both wrong targets are replayed.",
    )


def oracle_figure(controlled, oracle):
    by = primary_map(controlled)
    tracks = ("track1_5ch", "track2_common3")
    plot_series = []
    for method, safety, label in METHODS:
        points = []
        for index, track in enumerate(tracks):
            r = by[(track, "acquisitionCampaignGroup", method, safety, "CONGRUENT_CONTEXT", "0.95")]
            points.append((index + 1, float(r["overallMeanGainSeconds"])))
        plot_series.append({"name": label, "points": points, "color": COLORS[method]})
    points = []
    for index, track in enumerate(tracks):
        r = next(x for x in oracle["summaries"] if x["track"] == track and x["groupingScheme"] == "acquisitionCampaignGroup" and x["oracleCondition"] == "oracle_multiview_band_harmonic")
        points.append((index + 1, float(r["meanOracleGainSeconds"])))
    plot_series.append({"name": "Oracle multi-view upper bound", "points": points, "color": COLORS["Oracle multi-view"]})
    line_chart("fig07_oracle_vs_achieved.svg", "Achieved mean gain versus oracle ceiling", "Track (1 = 88 trials / 5 channels; 2 = 118 / common 3)", "Overall paired mean gain (s)", plot_series,
               (0.5, 2.5), (0.0, 0.20), "integer", "seconds", note="q_top=.95; oracle identifies early correct EEG tops with ground truth and is not deployable.")


def get_trace(track, session, trial_id, features):
    result = [r for r in features if r["track"] == track and r["sessionKey"] == session and r["trialId"] == trial_id]
    result.sort(key=lambda r: int(r["windowIndex"]))
    if len(result) != 9:
        raise AssertionError((track, session, trial_id, len(result)))
    return result


def representative_figures(oof, features, failures, reliability, policies):
    eligible = [r for r in oof if r["track"] == "track2_common3" and r["groupingScheme"] == "acquisitionCampaignGroup"
                and r["qTop"] == "0.95" and r["method"] == "M36_LOGISTIC_VIEW_GUARDED" and r["safetyOperatingPoint"] == "SAFE-STRICT"
                and r["condition"] == "CONGRUENT_CONTEXT" and int(r["contextApplied"]) == 1 and float(r["selectedEvidenceSeconds"]) <= 0.30]
    success = sorted(eligible, key=lambda r: (float(r["selectedEvidenceSeconds"]), r["trialKey"]))[0]
    success_trace = get_trace(success["track"], success["sessionKey"], success["trialId"], features)
    times = [float(r["evidenceSeconds"]) for r in success_trace]
    score_names = ("slot 0 / 7.2 Hz", "slot 1 / 9 Hz", "slot 2 / 12 Hz")
    score_colors = ("#00798c", "#d1495b", "#edae49")
    score_points = [{"name": name, "color": score_colors[i], "points": [(times[j], json.loads(row["mainScoresBySlot"])[i]) for j, row in enumerate(success_trace)]} for i, name in enumerate(score_names)]
    base_time = float(success["baselineEvidenceSeconds"])
    stop_time = float(success["selectedEvidenceSeconds"])
    line_chart("fig08_congruent_success_trace.svg", "Successful congruent acceleration: {}".format(success["trialKey"]), "EEG evidence duration (s)", "Frozen FBCCA score", score_points,
               (0.18, 1.02), (0.0, max(1.0, max(p for item in score_points for _, p in item["points"]) * 1.15)), "seconds", "number",
               refs=((stop_time, "Context stop {:.2f}s".format(stop_time), "#d1495b"), (base_time, "EEG-only {:.2f}s".format(base_time), "#526579")),
               note="Context agrees with the raw EEG top; emitted class remains slot {}. This is a retrospective OOF example.".format(success["trueSlotIndex"]))

    reject_candidates = [r for r in oof if r["track"] == "track2_common3" and r["groupingScheme"] == "acquisitionCampaignGroup"
                         and r["qTop"] == "0.95" and r["method"] == "M36_LOGISTIC_VIEW_GUARDED" and r["safetyOperatingPoint"] == "SAFE-STRICT"
                         and r["condition"] == "INCONGRUENT_CONTEXT" and int(r["contextApplied"]) == 0 and int(r["baselineCorrect"]) == 1]
    reject = sorted(reject_candidates, key=lambda r: (float(r["baselineEvidenceSeconds"]), r["trialKey"]))[0]
    reject_trace = get_trace(reject["track"], reject["sessionKey"], reject["trialId"], features)
    prob_map = {(r["track"], r["groupingScheme"], r["outerFoldId"], r["trialKey"], int(r["windowIndex"])): float(r["reliabilityProbability"])
                for r in reliability if r["groupingScheme"] == "acquisitionCampaignGroup"}
    probabilities = [prob_map[(reject["track"], reject["groupingScheme"], reject["outerFoldId"], reject["trialKey"], i)] for i in range(9)]
    policy = next(r for r in policies if r["track"] == reject["track"] and r["groupingScheme"] == reject["groupingScheme"] and r["outerFoldId"] == reject["outerFoldId"] and r["safetyOperatingPoint"] == "SAFE-STRICT")
    threshold = float(policy["selectedReliabilityThreshold"])
    rejection_series = [{"name": "OOF reliability probability", "color": "#00798c", "points": list(zip(times, probabilities))},
                        {"name": "Gate threshold", "color": "#d1495b", "points": [(times[0], threshold), (times[-1], threshold)], "dash": True}]
    tops = ",".join(str(int(r["mainTopSlotIndex"])) for r in reject_trace)
    line_chart("fig09_incongruent_rejection_trace.svg", "Safe wrong-context rejection: {}".format(reject["trialKey"]), "EEG evidence duration (s)", "Reliability probability", rejection_series,
               (0.18, 1.02), (0.0, 1.0), "seconds", "percent", refs=((float(reject["baselineEvidenceSeconds"]), "EEG-only fallback", "#526579"),),
               note="True slot {}; wrong Context slot {}; raw EEG top by update [{}]; threshold {:.3f}; gate did not accelerate.".format(reject["trueSlotIndex"], reject["contextSlotIndex"], tops, threshold))

    failure_candidates = [r for r in failures if r["track"] == "track2_common3" and r["groupingScheme"] == "acquisitionCampaignGroup"
                          and r["method"] == "M36_MULTI_VIEW_RULE" and r["safetyOperatingPoint"] == "SAFE-STRICT" and float(r["qTop"]) == 0.95]
    fail = sorted(failure_candidates, key=lambda r: (float(r["earlyStopEvidenceSeconds"]), r["trialKey"]))[0]
    fail_trace = get_trace(fail["track"], fail["sessionKey"], fail["trialId"], features)
    failure_series = [{"name": name, "color": score_colors[i], "points": [(float(x["evidenceSeconds"]), json.loads(x["mainScoresBySlot"])[i]) for x in fail_trace]} for i, name in enumerate(score_names)]
    line_chart("fig10_wrong_context_failure_trace.svg", "Wrong-context early-stop failure: {}".format(fail["trialKey"]), "EEG evidence duration (s)", "Frozen FBCCA score", failure_series,
               (0.18, 1.02), (0.0, max(1.0, max(p for item in failure_series for _, p in item["points"]) * 1.15)), "seconds", "number",
               refs=((float(fail["earlyStopEvidenceSeconds"]), "wrong stop {:.2f}s".format(float(fail["earlyStopEvidenceSeconds"])), "#d1495b"),
                     (float(fail["baselineEvidenceSeconds"]), "EEG-only {:.2f}s".format(float(fail["baselineEvidenceSeconds"])), "#526579")),
               note="True slot {}; wrong Context {}; raw top at stop {}; baseline later correct. OOF failure, not a hard-coded runtime exception.".format(fail["trueSlotIndex"], fail["wrongContextSlotIndex"], fail["earlyStopPrediction"]))

    examples = {
        "congruent_success": {k: success[k] for k in ("trialKey", "trueSlotIndex", "selectedEvidenceSeconds", "baselineEvidenceSeconds", "method")},
        "safe_incongruent_rejection": {k: reject[k] for k in ("trialKey", "trueSlotIndex", "contextSlotIndex", "baselineEvidenceSeconds", "method")},
        "wrong_context_failure": {k: fail[k] for k in ("trialKey", "trueSlotIndex", "wrongContextSlotIndex", "earlyStopEvidenceSeconds", "baselineEvidenceSeconds", "method")},
    }
    (ROOT / "representative_examples.json").write_text(json.dumps(examples, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ["fig08_congruent_success_trace.svg", "fig09_incongruent_rejection_trace.svg", "fig10_wrong_context_failure_trace.svg"]


def calibration_figure(calibration):
    series = []
    for track, label, color in (("track1_5ch", "Track 1 / five channel", "#00798c"), ("track2_common3", "Track 2 / common three channel", "#d1495b")):
        bins = defaultdict(list)
        for row in calibration:
            if row["track"] == track and row["observedSafeTrueRate"]:
                center = (float(row["probabilityBinLower"]) + float(row["probabilityBinUpper"])) / 2
                bins[center].append((float(row["meanPredictedProbability"]), float(row["observedSafeTrueRate"]), int(row["nTimepoints"])))
        points = []
        for center, rows in sorted(bins.items()):
            n = sum(x[2] for x in rows)
            points.append((sum(x[0] * x[2] for x in rows) / n, sum(x[1] * x[2] for x in rows) / n))
        series.append({"name": label, "color": color, "points": points})
    series.append({"name": "Perfect calibration", "color": "#7d8597", "points": [(0.0, 0.0), (1.0, 1.0),] , "dash": True})
    line_chart("fig11_oof_reliability_calibration.svg", "Nested out-of-fold reliability calibration", "Mean predicted probability", "Observed safeTrue rate", series,
               (0.0, 1.0), (0.0, 1.0), "percent", "percent", note="Groupwise outer-test calibration bins pooled by timepoint count; safeTrue is a future-derived evaluation label, never a feature.")


def shift_figure(shift_rows):
    features = ("topScore", "relativeMargin", "normalizedEntropy", "priorTopFlips", "bandAgreement", "harmonicAgreement", "secondaryAgreement", "bandMinimumMargin")
    plot_series = []
    for track, label, color in (("track1_5ch", "Track 1 / five channel", "#00798c"), ("track2_common3", "Track 2 / common three channel", "#d1495b")):
        points = []
        for index, metric in enumerate(features, 1):
            rows = [r for r in shift_rows if r["track"] == track and abs(float(r["evidenceSeconds"]) - 0.25) < 1e-12 and r[metric + "SmdVsOtherSessions"] not in ("", "None")]
            value = max((abs(float(r[metric + "SmdVsOtherSessions"])) for r in rows), default=0.0)
            points.append((index, value))
        plot_series.append({"name": label, "points": points, "color": color})
    line_chart("fig12_feature_session_shift.svg", "Worst absolute feature shift across sessions at 250 ms", "Feature index (see note)", "Absolute standardized mean difference", plot_series,
               (1.0, 8.0), (0.0, 2.0), "integer", "number",
               note="Index: 1 topScore; 2 relativeMargin; 3 normalizedEntropy; 4 priorTopFlips; 5 bandAgreement; 6 harmonicAgreement; 7 secondaryAgreement; 8 bandMinimumMargin. Standardized across sessions, descriptive only.")


def main():
    PLOTS.mkdir(parents=True, exist_ok=True)
    controlled = read_csv("controlled_context_results.csv")
    oof = read_csv("oof_predictions.csv")
    groups = read_csv("group_specific_results.csv")
    features = read_csv("eeg_trajectory_features.csv")
    failures = read_csv("failure_cases.csv")
    reliability = read_csv("logistic_reliability_oof.csv")
    policies = read_csv("selected_logistic_policies.csv")
    calibration = read_csv("oof_calibration_by_group.csv")
    shifts = read_csv("session_shift_analysis.csv")
    oracle = json.loads((ROOT / "oracle_summary.json").read_text(encoding="utf-8"))
    m33 = json.loads((ROOT / "real_m33_secondary_summary.json").read_text(encoding="utf-8"))
    m33_rows = read_csv("real_m33_secondary_per_seed.csv")

    table_rows = make_success_table(controlled, oof, oracle, m33, m33_rows)
    safety_sweep = make_safety_sweep(controlled)
    frontier = make_frontier_figures(controlled)
    group_figure(groups)
    oracle_figure(controlled, oracle)
    traces = representative_figures(oof, features, failures, reliability, policies)
    calibration_figure(calibration)
    shift_figure(shifts)

    files = sorted(PLOTS.glob("*.svg"))
    assert len(files) == 12, "expected 12 required SVG figures, found {}".format(len(files))
    assert len(table_rows) == 13, "central table must have 13 rows across both tracks and M33 secondary"
    manifest = {
        "status": "PASS",
        "figureCount": len(files),
        "figures": [{"file": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size} for path in files],
        "centralTable": {"file": "primary_success_table.csv", "rows": len(table_rows)},
        "safetySweep": {"file": "q_sweep_safety_frontier.csv", "rows": len(METHODS) * 2 * 11},
        "crossTrackZeroErrorPoints": safety_sweep["crossTrackZeroErrorPoints"],
        "representativeExamples": "representative_examples.json",
        "primaryGrouping": "acquisitionCampaignGroup",
        "qTopForCentralTable": 0.95,
        "contextSweep": [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95],
    }
    (ROOT / "figures_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "figureCount": len(files), "centralTableRows": len(table_rows),
                      "figures": [p.name for p in files]}, sort_keys=True))


if __name__ == "__main__":
    main()
