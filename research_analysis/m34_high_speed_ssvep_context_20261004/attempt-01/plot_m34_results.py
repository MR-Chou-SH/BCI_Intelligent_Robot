"""Render lightweight SVG figures from frozen M34 results."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path


OUT = Path(__file__).resolve().parent
PLOTS = OUT / "plots"
PLOTS.mkdir(exist_ok=True)
COLORS = {"FBCCA": "#1464f4", "eTRCA": "#e07b18", "TDCA": "#23965b"}


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def txt(x, y, value, size=13, fill="#222", anchor="start", weight="normal"):
    return f'<text x="{x}" y="{y}" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{html.escape(str(value))}</text>'


def fixed_accuracy_plot():
    rows = read_csv(OUT / "heldout_fixed_summary.csv")
    width, height = 1080, 530
    body = ['<rect width="100%" height="100%" fill="white"/>']
    body.append(txt(width / 2, 30, "Fixed-window accuracy on frozen heldout sessions", 20, anchor="middle", weight="bold"))
    windows = [0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0]
    for panel_index, session in enumerate(("B2", "S7")):
        left = 75 + panel_index * 510
        top, plot_w, plot_h = 76, 420, 340
        body.append(txt(left + plot_w / 2, 60, "B2 formal heldout" if session == "B2" else "S7 stress heldout", 16, anchor="middle", weight="bold"))
        for tick in range(0, 101, 20):
            y = top + plot_h * (1 - tick / 100)
            body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e5e7eb"/>')
            body.append(txt(left - 10, y + 4, f"{tick}%", 11, anchor="end", fill="#666"))
        body.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#555"/>')
        body.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#555"/>')
        for index, window in enumerate(windows):
            x = left + plot_w * index / (len(windows) - 1)
            body.append(txt(x, top + plot_h + 23, f"{window:g}", 10, anchor="middle", fill="#666"))
        subset = [row for row in rows if row["sessionKey"] == session]
        for decoder, color in COLORS.items():
            points = []
            for row in subset:
                if row["decoder"] != decoder:
                    continue
                idx = windows.index(float(row["evidenceSeconds"]))
                x = left + plot_w * idx / (len(windows) - 1)
                y = top + plot_h * (1 - float(row["accuracy"]))
                points.append((x, y))
            body.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.6" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in points) + '"/>')
            for x, y in points:
                body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.7" fill="{color}"/>')
        body.append(txt(left + plot_w / 2, top + plot_h + 47, "EEG evidence duration (s)", 12, anchor="middle", fill="#555"))
    lx = 410
    for i, decoder in enumerate(COLORS):
        body.append(f'<line x1="{lx + i * 110}" y1="485" x2="{lx + 25 + i * 110}" y2="485" stroke="{COLORS[decoder]}" stroke-width="3"/>')
        body.append(txt(lx + 32 + i * 110, 490, decoder, 12, fill="#444"))
    body.append(txt(width / 2, 520, "Accuracy uses the selected Dev configuration; S7 uses the frozen common-channel subset [2,4,7].", 11, anchor="middle", fill="#666"))
    (PLOTS / "fixed_accuracy_by_window.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="530" viewBox="0 0 1080 530">' + "".join(body) + "</svg>\n", encoding="utf-8")


def dynamic_stop_plot():
    rows = read_csv(OUT / "heldout_dynamic_per_trial.csv")
    windows = [0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0]
    counts = {session: [sum(row["sessionKey"] == session and abs(float(row["evidenceSeconds"]) - value) < 1e-9 for row in rows) for value in windows] for session in ("B2", "S7")}
    width, height = 940, 480
    left, top, plot_w, plot_h = 80, 80, 790, 300
    body = ['<rect width="100%" height="100%" fill="white"/>', txt(width / 2, 32, "Heldout dynamic decision evidence", 20, anchor="middle", weight="bold")]
    max_count = max(max(values) for values in counts.values())
    for tick in range(0, max_count + 1, max(1, max_count // 5)):
        y = top + plot_h * (1 - tick / max_count)
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        body.append(txt(left - 12, y + 4, str(tick), 11, anchor="end", fill="#666"))
    for index, window in enumerate(windows):
        x = left + plot_w * index / len(windows) + 12
        for group_index, session in enumerate(("B2", "S7")):
            bar_w = 24
            y = top + plot_h * (1 - counts[session][index] / max_count)
            h = top + plot_h - y
            color = "#1464f4" if session == "B2" else "#23965b"
            xbar = x + group_index * (bar_w + 3)
            body.append(f'<rect x="{xbar:.1f}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" fill="{color}"/>')
        body.append(txt(x + 26, top + plot_h + 22, f"{window:g}", 11, anchor="middle", fill="#666"))
    body.append(txt(left + plot_w / 2, top + plot_h + 47, "Evidence duration when stopping (s)", 12, anchor="middle", fill="#555"))
    body.append(f'<rect x="340" y="430" width="14" height="14" fill="#1464f4"/>')
    body.append(txt(360, 442, "B2 formal heldout", 12, fill="#444"))
    body.append(f'<rect x="520" y="430" width="14" height="14" fill="#23965b"/>')
    body.append(txt(540, 442, "S7 stress heldout", 12, fill="#444"))
    (PLOTS / "dynamic_stop_distribution.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="940" height="480" viewBox="0 0 940 480">' + "".join(body) + "</svg>\n", encoding="utf-8")


def context_latency_plot():
    summary = json.loads((OUT / "heldout_context_summary.json").read_text(encoding="utf-8"))
    heldout = json.loads((OUT / "heldout_results.json").read_text(encoding="utf-8"))
    dynamic = heldout["dynamic"]
    width, height = 1000, 490
    left, top, plot_w, plot_h = 100, 80, 800, 290
    body = ['<rect width="100%" height="100%" fill="white"/>', txt(width / 2, 32, "Context transfer latency (seeded simulation)", 20, anchor="middle", weight="bold")]
    groups = []
    for session in ("B2", "S7"):
        groups.append((session, "EEG dynamic", float(dynamic[session]["nominalLoggedOnsetRelativeDecisionTimeSeconds"]), 0.0, "#1464f4"))
        pre = summary["ContextResultsBySessionAndAvailability"][session]["precomputed_before_eeg"]
        api = summary["ContextResultsBySessionAndAvailability"][session]["measured_m33_api_latency_sensitivity"]
        groups.append((session, "Context precomputed", float(pre["meanContextDecisionSeconds"]), float(pre["applicationRate"]), "#e07b18"))
        groups.append((session, "Context at measured API latency", float(api["meanContextDecisionSeconds"]), float(api["applicationRate"]), "#777777"))
    max_time = 1.2
    for tick in range(0, 13, 2):
        value = tick / 10
        y = top + plot_h * (1 - value / max_time)
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        body.append(txt(left - 12, y + 4, f"{value:.1f}s", 11, anchor="end", fill="#666"))
    group_centers = [170, 360, 640, 830]
    grouped = {"B2": groups[:3], "S7": groups[3:]}
    for index, session in enumerate(("B2", "S7")):
        center = group_centers[index * 2]
        for j, item in enumerate(grouped[session]):
            _, label, value, application_rate, color = item
            x = center + (j - 1) * 52
            y = top + plot_h * (1 - value / max_time)
            body.append(f'<rect x="{x}" y="{y:.1f}" width="38" height="{top + plot_h - y:.1f}" fill="{color}"/>')
            body.append(txt(x + 19, y - 7, f"{value:.2f}", 10, anchor="middle", fill="#444"))
        body.append(txt(center, top + plot_h + 28, session, 14, anchor="middle", weight="bold"))
        base_x = center - 57
        for j, item in enumerate(grouped[session]):
            body.append(txt(base_x + j * 52, top + plot_h + 49, ["EEG", "Precomputed", "API latency"][j], 9, anchor="middle", fill="#555"))
    body.append(txt(width / 2, 465, "Mean nominal logged-onset-relative decision time. Context trials are randomly aligned to EEG via post-hoc labels; not prospective paired data.", 11, anchor="middle", fill="#666"))
    (PLOTS / "context_latency_comparison.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="490" viewBox="0 0 1000 490">' + "".join(body) + "</svg>\n", encoding="utf-8")


def fixed_itr_plot(metric, filename, title):
    rows = read_csv(OUT / "heldout_fixed_summary.csv")
    width, height = 1080, 530
    body = ['<rect width="100%" height="100%" fill="white"/>']
    body.append(txt(width / 2, 30, title, 20, anchor="middle", weight="bold"))
    windows = [0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0]
    max_y = max(float(row[metric]) for row in rows)
    tick_step = max(1, round(max_y / 5))
    y_max = tick_step * 5
    for panel_index, session in enumerate(("B2", "S7")):
        left = 75 + panel_index * 510
        top, plot_w, plot_h = 76, 420, 340
        body.append(txt(left + plot_w / 2, 60, "B2 formal heldout" if session == "B2" else "S7 stress heldout", 16, anchor="middle", weight="bold"))
        for tick in range(0, y_max + 1, tick_step):
            y = top + plot_h * (1 - tick / y_max)
            body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e5e7eb"/>')
            body.append(txt(left - 10, y + 4, str(tick), 11, anchor="end", fill="#666"))
        body.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#555"/>')
        body.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#555"/>')
        subset = [row for row in rows if row["sessionKey"] == session]
        for decoder, color in COLORS.items():
            points = []
            for row in subset:
                if row["decoder"] != decoder:
                    continue
                idx = windows.index(float(row["evidenceSeconds"]))
                x = left + plot_w * idx / (len(windows) - 1)
                y = top + plot_h * (1 - float(row[metric]) / y_max)
                points.append((x, y))
            body.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.6" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in points) + '"/>')
            for x, y in points:
                body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.7" fill="{color}"/>')
        for index, window in enumerate(windows):
            x = left + plot_w * index / (len(windows) - 1)
            body.append(txt(x, top + plot_h + 23, f"{window:g}", 10, anchor="middle", fill="#666"))
        body.append(txt(left + plot_w / 2, top + plot_h + 47, "EEG evidence duration (s)", 12, anchor="middle", fill="#555"))
    lx = 410
    for i, decoder in enumerate(COLORS):
        body.append(f'<line x1="{lx + i * 110}" y1="485" x2="{lx + 25 + i * 110}" y2="485" stroke="{COLORS[decoder]}" stroke-width="3"/>')
        body.append(txt(lx + 32 + i * 110, 490, decoder, 12, fill="#444"))
    body.append(txt(width / 2, 520, "ITR is a rate estimate; decision-only uses guard + evidence, cycle estimate includes a fixed 9 s cycle.", 11, anchor="middle", fill="#666"))
    (PLOTS / filename).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="530" viewBox="0 0 1080 530">' + "".join(body) + "</svg>\n", encoding="utf-8")


def context_accuracy_safety_plot():
    summary = json.loads((OUT / "heldout_context_summary.json").read_text(encoding="utf-8"))
    heldout = json.loads((OUT / "heldout_results.json").read_text(encoding="utf-8"))
    width, height = 1000, 510
    left, top, plot_w, plot_h = 100, 84, 800, 300
    body = ['<rect width="100%" height="100%" fill="white"/>', txt(width / 2, 32, "Context transfer accuracy and wrong early stops", 20, anchor="middle", weight="bold")]
    for tick in range(0, 101, 20):
        y = top + plot_h * (1 - tick / 100)
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        body.append(txt(left - 12, y + 4, f"{tick}%", 11, anchor="end", fill="#666"))
    body.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#555"/>')
    body.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#555"/>')
    colors = {"EEG dynamic": "#1464f4", "Context precomputed": "#e07b18", "Measured API latency": "#777777"}
    sessions = ("B2", "S7")
    for s_idx, session in enumerate(sessions):
        center = 300 + s_idx * 420
        baseline = heldout["dynamic"][session]["accuracy"]
        pre = summary["ContextResultsBySessionAndAvailability"][session]["precomputed_before_eeg"]
        api = summary["ContextResultsBySessionAndAvailability"][session]["measured_m33_api_latency_sensitivity"]
        items = [
            ("EEG dynamic", baseline, heldout["dynamic"][session]["wrongEarlyStopCount"]),
            ("Context precomputed", pre["contextAccuracy"], pre["wrongEarlyStopCount"]),
            ("Measured API latency", api["contextAccuracy"], api["wrongEarlyStopCount"]),
        ]
        for i, (label, accuracy, wrong) in enumerate(items):
            x = center - 75 + i * 56
            y = top + plot_h * (1 - float(accuracy))
            bar_h = top + plot_h - y
            body.append(f'<rect x="{x}" y="{y:.1f}" width="38" height="{bar_h:.1f}" fill="{colors[label]}"/>')
            body.append(txt(x + 19, y - 16, f"{float(accuracy) * 100:.1f}%", 10, anchor="middle", fill="#444"))
            body.append(txt(x + 19, y - 3, f"wrong {wrong}", 9, anchor="middle", fill="#9a3412"))
        body.append(txt(center, top + plot_h + 25, session, 14, anchor="middle", weight="bold"))
    for i, label in enumerate(colors):
        x = 215 + i * 240
        body.append(f'<rect x="{x}" y="435" width="14" height="14" fill="{colors[label]}"/>')
        body.append(txt(x + 20, 447, label, 11, fill="#444"))
    body.append(txt(width / 2, 485, "Context values are 100-seed historical transfer simulations, not independent human trials. Wrong counts are trial-seed pairs.", 11, anchor="middle", fill="#666"))
    (PLOTS / "context_accuracy_wrong_early_stops.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="510" viewBox="0 0 1000 510">' + "".join(body) + "</svg>\n", encoding="utf-8")


def main():
    fixed_accuracy_plot()
    dynamic_stop_plot()
    context_latency_plot()
    fixed_itr_plot("decisionOnlyItrBitsPerMin", "fixed_itr_decision_only.svg", "Decision-only ITR by fixed EEG evidence duration")
    fixed_itr_plot("m6Like9sCycleItrBitsPerMin", "fixed_itr_m6like_cycle.svg", "M6-like 9 s cycle ITR by fixed EEG evidence duration")
    context_accuracy_safety_plot()
    print("Wrote 6 SVG plots in", PLOTS)


if __name__ == "__main__":
    main()
