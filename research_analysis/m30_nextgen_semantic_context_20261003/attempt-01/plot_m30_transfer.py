"""Generate dependency-free SVG summaries for the M30 transfer experiment."""

from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent
PLOTS = OUT / "plots"
COVERAGE = ("C65", "C85", "C100")
LAMBDAS = (0.0, 0.5, 1.0, 1.5, 2.0)
OPS = ("FAST", "MEDIUM", "CONSERVATIVE")


def _read_csv(name: str) -> list[dict[str, str]]:
    with (OUT / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_once(name: str, svg: str) -> None:
    path = PLOTS / name
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(svg)


def _text(x: float, y: float, value: Any, *, size: int = 13, anchor: str = "middle",
          fill: str = "#222", weight: str = "normal", rotate: int | None = None) -> str:
    transform = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" '
            f'font-family="Arial, sans-serif" fill="{fill}" font-weight="{weight}"{transform}>'
            f'{html.escape(str(value))}</text>')


def _rgb_interp(low: tuple[int, int, int], high: tuple[int, int, int], ratio: float) -> str:
    ratio = min(1.0, max(0.0, ratio))
    rgb = tuple(round(low[index] + (high[index] - low[index]) * ratio) for index in range(3))
    return "#%02x%02x%02x" % rgb


def _heatmap(field: str, filename: str, title: str, *, is_count: bool = False, maximum: float | None = None) -> None:
    rows = [row for row in _read_csv("coverage_lambda_eeg_matrix.csv")]
    values: dict[tuple[str, float, str], float | None] = {}
    for row in rows:
        raw = row.get(field, "")
        value = float(raw) if raw not in {"", "None", "null"} else None
        values[(row["operatingPoint"], float(row["lambdaCtx"]), row["coverageOperatingPoint"])] = value
    data = [value for value in values.values() if value is not None]
    scale_max = maximum if maximum is not None else (max(data, default=1.0) or 1.0)
    width, height = 1140, 625
    bits = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fff"/>',
            _text(width / 2, 38, title, size=23, weight="bold"),
            _text(width / 2, 62, "Rows: lambda_ctx   Columns: calibrated coverage operating point", size=13, fill="#555")]
    for panel, op in enumerate(OPS):
        left = 76 + panel * 355
        top = 112
        cell_w, cell_h = 92, 69
        bits.append(_text(left + 150, 94, op, size=17, weight="bold"))
        for col, coverage in enumerate(COVERAGE):
            bits.append(_text(left + 95 + col * cell_w, top - 12, coverage, size=13, weight="bold"))
        for r, lam in enumerate(reversed(LAMBDAS)):
            cy = top + r * cell_h
            bits.append(_text(left + 35, cy + 41, f"{lam:g}", anchor="end"))
            for c, coverage in enumerate(COVERAGE):
                cx = left + 45 + c * cell_w
                value = values.get((op, lam, coverage))
                ratio = 0.0 if value is None else (float(value) / scale_max if scale_max else 0.0)
                if is_count:
                    color = _rgb_interp((255, 246, 239), (177, 35, 24), ratio)
                    label = "—" if value is None else f"{int(value)}"
                else:
                    color = _rgb_interp((239, 246, 250), (25, 96, 138), ratio)
                    label = "—" if value is None else f"{float(value):.3f}"
                bits.append(f'<rect x="{cx}" y="{cy}" width="{cell_w-5}" height="{cell_h-5}" rx="4" fill="{color}" stroke="#d0d6da"/>')
                bits.append(_text(cx + (cell_w - 5) / 2, cy + 38, label, size=14,
                                  fill="#111" if ratio < 0.72 else "#fff", weight="bold"))
    bits.append(_text(width / 2, height - 18,
                      "M25 frozen 100 ms evidence grid; historical transfer simulation; not prospective human EEG", size=12, fill="#555"))
    bits.append("</svg>")
    _write_once(filename, "".join(bits))


def _gain_lines() -> None:
    rows = [row for row in _read_csv("coverage_lambda_eeg_matrix.csv")]
    width, height = 1200, 625
    colors = ("#777777", "#2878b5", "#2a9d72", "#d08a20", "#ae4e9b")
    bits = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fff"/>', _text(width/2, 38, "M30 Context-active gain by EEG operating point", size=23, weight="bold"),
            _text(width/2, 62, "Precomputed q_page; 100 seeds; gain measured only on trials with an earlier Context stop", size=13, fill="#555")]
    for panel, op in enumerate(OPS):
        left, top, chart_w, chart_h = 90 + panel * 365, 120, 280, 360
        bits.append(_text(left + chart_w/2, 99, op, size=16, weight="bold"))
        for tick in range(6):
            val = tick * 0.12
            y = top + chart_h - val / 0.6 * chart_h
            bits.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+chart_w}" y2="{y:.1f}" stroke="#e4e8eb"/>')
            bits.append(_text(left - 12, y + 4, f"{val:.2f}", size=11, anchor="end", fill="#555"))
        for index, coverage in enumerate(COVERAGE):
            x = left + (index + 0.5) * chart_w / 3
            bits.append(_text(x, top + chart_h + 22, coverage, size=12))
        for li, lam in enumerate(LAMBDAS):
            points = []
            for index, coverage in enumerate(COVERAGE):
                row = next(r for r in rows if r["operatingPoint"] == op and r["coverageOperatingPoint"] == coverage
                           and float(r["lambdaCtx"]) == lam)
                raw = row.get("meanContextActiveGainSeconds", "")
                value = float(raw) if raw else None
                x = left + (index + 0.5) * chart_w / 3
                y = top + chart_h - (0.0 if value is None else min(0.6, value) / 0.6 * chart_h)
                points.append((x, y, value))
            coords = " ".join(f"{x:.1f},{y:.1f}" for x,y,_ in points)
            bits.append(f'<polyline points="{coords}" fill="none" stroke="{colors[li]}" stroke-width="2.5"/>')
            for x,y,value in points:
                bits.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{colors[li]}"/>')
    legend_x, legend_y = 335, 535
    for index, lam in enumerate(LAMBDAS):
        x = legend_x + index * 112
        bits.append(f'<line x1="{x}" y1="{legend_y}" x2="{x+24}" y2="{legend_y}" stroke="{colors[index]}" stroke-width="3"/>')
        bits.append(_text(x+31, legend_y+4, f"lambda={lam:g}", size=11, anchor="start"))
    bits.extend([_text(width/2, height-18, "λ=0 is the exact EEG-only baseline. Positive λ values overlap at the 100 ms replay resolution.", size=12, fill="#555"), "</svg>"])
    _write_once("12_eeg_operating_point_gain.svg", "".join(bits))


def _m28_compare() -> None:
    rows = _read_csv("m28_vs_m30_comparison.csv")
    selected = {row["m30CoverageOperatingPoint"]: row for row in rows if row["eegOperatingPoint"] == "FAST"}
    labels = ["M28*", *COVERAGE]
    precision = [float(rows[0]["M28OverallActivePrecision"]), *[float(selected[c]["M30HeldOutActivePrecision"]) for c in COVERAGE]]
    participation = [float(rows[0]["M28ActiveCoverageAll"]), *[float(selected[c]["M30HeldOutOverallParticipation"]) for c in COVERAGE]]
    width, height = 1050, 590
    bits = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fff"/>', _text(width/2, 38, "M28 vs M30 held-out semantic coverage trade-off", size=22, weight="bold"),
            _text(width/2, 64, "M28*: full 40-case curated set; M30: 46 held-out sequential decisions (designs differ)", size=13, fill="#555")]
    chart_left, chart_top, chart_w, chart_h = 110, 115, 820, 330
    for tick in range(6):
        val = tick / 5
        y = chart_top + chart_h - val * chart_h
        bits.append(f'<line x1="{chart_left}" y1="{y}" x2="{chart_left+chart_w}" y2="{y}" stroke="#e4e8eb"/>')
        bits.append(_text(chart_left-12, y+4, f"{val:.1f}", size=11, anchor="end", fill="#555"))
    cell = chart_w / len(labels)
    for i, label in enumerate(labels):
        xbase = chart_left + i*cell + 34
        for metric, value, color in (("precision", precision[i], "#2878b5"), ("participation", participation[i], "#e48a2a")):
            offset = 0 if metric == "precision" else 50
            bh = value * chart_h
            bits.append(f'<rect x="{xbase+offset}" y="{chart_top+chart_h-bh}" width="38" height="{bh:.1f}" fill="{color}"/>')
            bits.append(_text(xbase+offset+19, chart_top+chart_h-bh-7, f"{value:.2f}", size=10))
        bits.append(_text(chart_left+i*cell+cell/2, chart_top+chart_h+24, label, size=12))
    bits.append(f'<rect x="370" y="500" width="16" height="16" fill="#2878b5"/>')
    bits.append(_text(392, 513, "active Context precision", size=12, anchor="start"))
    bits.append(f'<rect x="575" y="500" width="16" height="16" fill="#e48a2a"/>')
    bits.append(_text(597, 513, "overall participation", size=12, anchor="start"))
    bits.append(_text(width/2, height-18, "Coverage rises in M30, while held-out active precision falls; this does not establish generalization quality.", size=12, fill="#555"))
    bits.append("</svg>")
    _write_once("13_m28_vs_m30_coverage.svg", "".join(bits))


def _feasibility() -> None:
    rows = [row for row in _read_csv("coverage_lambda_eeg_matrix.csv") if float(row["lambdaCtx"]) == 1.0]
    labels = [f"{row['coverageOperatingPoint']}\n{row['operatingPoint']}" for row in rows]
    width, height = 1300, 620
    left, top, chart_w, chart_h = 100, 115, 1120, 360
    bits = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fff"/>', _text(width/2, 38, "M30 0.4 / 0.5 s active-gain feasibility", size=22, weight="bold"),
            _text(width/2, 64, "lambda_ctx=1; all plotted conditions fail the paired-accuracy / zero-wrong-early-stop safety gate", size=13, fill="#8d2424")]
    scale_max = 0.65
    for tick in range(7):
        value = tick * 0.1
        y = top + chart_h - value / scale_max * chart_h
        bits.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+chart_w}" y2="{y:.1f}" stroke="#e4e8eb"/>')
        bits.append(_text(left-12, y+4, f"{value:.1f}", size=11, anchor="end", fill="#555"))
    for target, color, label in ((0.4, "#d08a20", "0.4 s"), (0.5, "#a13d8b", "0.5 s")):
        y = top + chart_h - target / scale_max * chart_h
        bits.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+chart_w}" y2="{y:.1f}" stroke="{color}" stroke-width="2" stroke-dasharray="7,5"/>')
        bits.append(_text(left+chart_w-6, y-6, label, size=12, anchor="end", fill=color, weight="bold"))
    cell = chart_w / len(rows)
    for index, row in enumerate(rows):
        active = float(row["meanContextActiveGainSeconds"] or 0.0)
        bar_h = min(scale_max, active) / scale_max * chart_h
        x = left + index*cell + 13
        y = top + chart_h - bar_h
        bits.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell-25:.1f}" height="{bar_h:.1f}" fill="#bd5549"/>')
        bits.append(_text(x+(cell-25)/2, y-5, f"{active:.2f}", size=10))
        wrong = int(float(row["wrongEarlyStopCount"]))
        bits.append(_text(x+(cell-25)/2, top+chart_h+31, labels[index].replace("\n", " / "), size=10))
        bits.append(_text(x+(cell-25)/2, top+chart_h+49, f"wrong={wrong}", size=9, fill="#8d2424"))
    bits.append(_text(width/2, height-20, "Some raw active-gain means cross a target; none meet the combined no-accuracy-loss and zero-error support rule.", size=12, fill="#555"))
    bits.append("</svg>")
    _write_once("14_gain_target_feasibility.svg", "".join(bits))


def run() -> dict[str, Any]:
    PLOTS.mkdir(exist_ok=True)
    _heatmap("meanContextActiveGainSeconds", "09_coverage_lambda_context_active_gain.svg",
             "Coverage × lambda_ctx: Context-active EEG gain (seconds)", maximum=0.65)
    _heatmap("meanAllTrialGainSeconds", "10_coverage_lambda_all_trial_gain.svg",
             "Coverage × lambda_ctx: all-trial EEG gain (seconds)", maximum=0.30)
    _heatmap("wrongEarlyStopCount", "11_coverage_lambda_wrong_early_stops.svg",
             "Coverage × lambda_ctx: wrong early-stop count", is_count=True, maximum=12)
    _gain_lines()
    _m28_compare()
    _feasibility()
    return {"status": "PASS", "plotCount": len(list(PLOTS.glob("*.svg"))),
            "newPlots": ["09_coverage_lambda_context_active_gain.svg", "10_coverage_lambda_all_trial_gain.svg",
                         "11_coverage_lambda_wrong_early_stops.svg", "12_eeg_operating_point_gain.svg",
                         "13_m28_vs_m30_coverage.svg", "14_gain_target_feasibility.svg"]}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))
