"""M19 Phase-2-only Research Acquisition protocol and software rehearsals.

This module is deliberately separate from the M19 PagedQueueV1 Demo and the
frozen M18 protocol. It reuses M18's causal Context API, append-only continuous
EEG recorder/source models, and M13.7's slot cue contract. It does not decode
EEG or invoke a robot.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import random
import sys
import time
import uuid

from integration.m13_7_golden_session import AudioCueSystem, NullLoggingAudioBackend, PcToneAudioBackend
from integration.m18_unified_acquisition import (
    M18ContextEngine,
    M18LiveContinuousEegSource,
    M18SessionRecorder,
    SyntheticClock,
    SyntheticContinuousEegSource,
    _canonical_json,
    _sha256_bytes,
)


SCHEMA_VERSION = 1
PROTOCOL_VERSION = "m19-phase2-research-acquisition-v1"
SLOTS = (
    {"slotIndex": 0, "targetId": "research-stimulus-yellow", "logicalBlockId": "block_sim_01", "label": "YELLOW", "frequencyHz": 7.2, "colorHex": "#FFD400"},
    {"slotIndex": 1, "targetId": "research-stimulus-blue", "logicalBlockId": "block_sim_02", "label": "BLUE", "frequencyHz": 9.0, "colorHex": "#168BFF"},
    {"slotIndex": 2, "targetId": "research-stimulus-green", "logicalBlockId": "block_sim_03", "label": "GREEN", "frequencyHz": 12.0, "colorHex": "#20C96B"},
)
RESEARCH_TARGET_CUE_TONES_HZ = (660.0, 880.0, 1320.0)
CONDITIONS = ("ALIGNED", "NEUTRAL", "CONFLICT")
CAPTURE_SECONDS = 4.0
NOMINAL_SAMPLING_RATE_HZ = 1000.0
CHANNEL_COUNT = 8
PACKET_SAMPLES = 20
CAPTURE_SAMPLES = int(CAPTURE_SECONDS * NOMINAL_SAMPLING_RATE_HZ)
CONTEXT_HISTORY_WINDOW = 2

PROTOCOL = {
    "recordType": "m19_phase2_only_research_protocol",
    "schemaVersion": SCHEMA_VERSION,
    "protocolVersion": PROTOCOL_VERSION,
    "status": "SOFTWARE_PROTOCOL_FREEZE",
    "mode": "RESEARCH_ACQUISITION",
    "counts": {"FOCUSED": 18, "NATURAL_GAZE": 72, "FORMAL_TOTAL": 90, "SMOKE_QC_MAX": 6},
    "blockLayout": [
        {"blockId": "FOCUSED_01", "group": "FOCUSED", "count": 9},
        {"blockId": "NATURAL_01", "group": "NATURAL_GAZE", "count": 9},
        {"blockId": "NATURAL_02", "group": "NATURAL_GAZE", "count": 9},
        {"blockId": "NATURAL_03", "group": "NATURAL_GAZE", "count": 9},
        {"blockId": "NATURAL_04", "group": "NATURAL_GAZE", "count": 9},
        {"blockId": "FOCUSED_02", "group": "FOCUSED", "count": 9},
        {"blockId": "NATURAL_05", "group": "NATURAL_GAZE", "count": 9},
        {"blockId": "NATURAL_06", "group": "NATURAL_GAZE", "count": 9},
        {"blockId": "NATURAL_07", "group": "NATURAL_GAZE", "count": 9},
        {"blockId": "NATURAL_08", "group": "NATURAL_GAZE", "count": 9},
    ],
    "slots": [dict(item) for item in SLOTS],
    "timing": {
        "triggerDwellSeconds": 1.5,
        "captureSeconds": CAPTURE_SECONDS,
        "captureSamplesAtNominalRate": CAPTURE_SAMPLES,
        "trialZero": "Quest intentional Trigger dwell completion; PC software event; not optical onset",
        "sampleAnchor": "first ND8 packet received at or after trialZero",
        "continuousStream": True,
    },
    "naturalBalance": {
        "frequenciesPerCondition": 24,
        "conditions": {condition: 24 for condition in CONDITIONS},
        "frequencyByConditionCell": 8,
        "perBlockFrequencyByConditionCell": 1,
        "split": {"DEV": 36, "LOCKED_TEST": 36},
        "splitMethod": "seeded balanced cell-wise assignment across all eight blocks; never chronological halves",
    },
    "context": {
        "generator": "M18ContextEngine",
        "candidateIds": [item["logicalBlockId"] for item in SLOTS],
        "historyWindowCompletedCueEvents": CONTEXT_HISTORY_WINDOW,
        "freezeBeforeTargetAssignment": True,
        "targetVisibleToGenerator": False,
        "futureEegVisibleToGenerator": False,
        "futureOutcomeVisibleToGenerator": False,
        "neutralPolicy": "uniform effective prior derived after causal base snapshot is frozen",
        "conditionBlindToParticipant": True,
    },
    "rawFirst": {
        "nominalSamplingRateHz": NOMINAL_SAMPLING_RATE_HZ,
        "channelCount": CHANNEL_COUNT,
        "continuousRawAuthority": True,
        "preserveOnAnalysisFailure": True,
        "uniqueSessionRoot": True,
    },
    "productionBoundary": {
        "decoderChanged": False,
        "robotDispatch": False,
        "physicalQuestOperated": False,
        "physicalNd8Operated": False,
    },
}


class ResearchProtocolError(RuntimeError):
    pass


class StateTransitionError(ResearchProtocolError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_seed(*parts: object) -> int:
    text = "|".join(str(item) for item in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(text).digest()[:4], "big")


def _write_new_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_metadata(path: Path, payload: dict) -> None:
    """Replace only this run's small mutable metadata, never raw artifacts."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


class AppendOnlyJsonl:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence = 0
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        self._sequence = max(self._sequence, int(json.loads(line).get("sequence", -1)) + 1)

    def append(self, record_type: str, **values) -> dict:
        record = {
            "recordType": "m19_research_event",
            "schemaVersion": SCHEMA_VERSION,
            "eventType": str(record_type),
            "sequence": self._sequence,
            "utcTimestamp": _utc_now(),
            "monotonicNs": time.monotonic_ns(),
            **values,
        }
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._sequence += 1
        return record


def _generate_context(
    history: list[str],
    *,
    seed: int,
    ordinal: int,
    condition: str,
    attempt_id: str | None = None,
    generated_at_monotonic_ns: int | None = None,
) -> dict:
    candidates = tuple(dict(item) for item in SLOTS)
    observed_window = list(history[-CONTEXT_HISTORY_WINDOW:])
    generated_ns = ordinal * 2_000_000 + 1 if generated_at_monotonic_ns is None else int(generated_at_monotonic_ns)
    snapshot = M18ContextEngine().generate(
        completed_history=observed_window,
        task_state_id="m19-p2-state-{:03d}".format(len(history)),
        scenario_seed=_stable_seed(seed, ordinal, "context"),
        generated_at_monotonic_ns=generated_ns,
        frozen_at_monotonic_ns=generated_ns + 1,
        candidate_set=candidates,
    ).to_dict()
    snapshot["contextSnapshotId"] = "m19-p2-context-{:03d}{}".format(
        ordinal, "" if attempt_id is None else "-" + attempt_id.rsplit("-attempt-", 1)[-1]
    )
    snapshot["generatedAtUtc"] = _utc_now()
    snapshot["frozenAtUtc"] = _utc_now()
    base_hash = snapshot["snapshotCanonicalSha256"]
    snapshot["recordType"] = "m19_research_context_snapshot"
    snapshot["m19Condition"] = condition
    snapshot["causalHistoryWindow"] = observed_window
    snapshot["causalBaseSnapshotCanonicalSha256"] = base_hash
    if condition == "NEUTRAL":
        snapshot["priorVector"] = [1.0 / 3.0] * 3
        snapshot["contextTop"] = [item["logicalBlockId"] for item in SLOTS]
        snapshot["entropy"] = math.log(3.0, 2.0)
        snapshot["normalizedEntropy"] = 1.0
        snapshot["contextInformativeness"] = "NEUTRAL_UNIFORM_PRECOMMITTED_POLICY"
    canonical_values = {
        key: value for key, value in snapshot.items()
        if key not in ("snapshotCanonicalSha256", "recordType", "schemaVersion")
    }
    snapshot["snapshotCanonicalSha256"] = _sha256_bytes(_canonical_json(canonical_values).encode("utf-8"))
    snapshot["targetVisibleToGenerator"] = False
    snapshot["futureEegVisibleToGenerator"] = False
    snapshot["futureOutcomeVisibleToGenerator"] = False
    return snapshot


def _context_top_slot(snapshot: dict) -> int | None:
    top = snapshot.get("contextTop") or []
    if len(top) != 1:
        return None
    return next((item["slotIndex"] for item in SLOTS if item["logicalBlockId"] == top[0]), None)


def _build_natural_block(block_id: str, block_index: int, history: list[str], ordinal: int, seed: int) -> tuple[list[dict], list[str]]:
    cells = [(slot, condition) for slot in range(3) for condition in CONDITIONS]
    rng = random.Random(_stable_seed(seed, block_id, "cell-order"))
    rng.shuffle(cells)
    engine = M18ContextEngine()
    candidate_set = tuple(dict(item) for item in SLOTS)
    failed = set()

    def search(remaining: tuple[tuple[int, str], ...], current_history: list[str], chosen: list[dict]):
        if not remaining:
            return chosen
        key = (remaining, tuple(current_history[-CONTEXT_HISTORY_WINDOW:]))
        if key in failed:
            return None
        choices = list(remaining)
        random.Random(_stable_seed(seed, block_id, len(chosen), "branch-order")).shuffle(choices)
        for slot, condition in choices:
            window = current_history[-CONTEXT_HISTORY_WINDOW:]
            context = engine.generate(
                completed_history=window,
                task_state_id="planner-{}-{}".format(block_id, len(current_history)),
                scenario_seed=_stable_seed(seed, block_id, ordinal + len(chosen), "context"),
                generated_at_monotonic_ns=(ordinal + len(chosen)) * 2_000_000 + 1,
                frozen_at_monotonic_ns=(ordinal + len(chosen)) * 2_000_000 + 2,
                candidate_set=candidate_set,
            )
            top_slot = next((item["slotIndex"] for item in SLOTS if item["logicalBlockId"] in context.context_top), None)
            if condition == "ALIGNED" and top_slot != slot:
                continue
            if condition == "CONFLICT" and (top_slot is None or top_slot == slot):
                continue
            next_remaining = tuple(item for item in remaining if item != (slot, condition))
            target = SLOTS[slot]
            row = {
                "trialId": "m19-p2-{}-{:02d}".format(block_id.lower(), len(chosen) + 1),
                "blockId": block_id,
                "blockIndex": block_index,
                "ordinal": ordinal + len(chosen),
                "group": "NATURAL_GAZE",
                "slotIndex": slot,
                "targetId": target["targetId"],
                "targetLabel": target["label"],
                "frequencyHz": target["frequencyHz"],
                "contextCondition": condition,
                "split": None,
            }
            result = search(next_remaining, current_history + [target["logicalBlockId"]], chosen + [row])
            if result is not None:
                return result
        failed.add(key)
        return None

    result = search(tuple(cells), list(history), [])
    if result is None:
        raise ResearchProtocolError("no causal balanced Context schedule found for {}".format(block_id))
    return result, [SLOTS[row["slotIndex"]]["logicalBlockId"] for row in result]


def build_plan(seed: int = 190019) -> dict:
    rng = random.Random(int(seed))
    history: list[str] = []
    rows: list[dict] = []
    ordinal = 0
    natural_index = 0
    block_seed_offsets = {}
    for block in PROTOCOL["blockLayout"]:
        block_id = block["blockId"]
        if block["group"] == "FOCUSED":
            slots = [0, 0, 0, 1, 1, 1, 2, 2, 2]
            random.Random(_stable_seed(seed, block_id, "focused-order")).shuffle(slots)
            block_rows = []
            for slot in slots:
                ordinal += 1
                target = SLOTS[slot]
                block_rows.append({
                    "trialId": "m19-p2-{}-{:02d}".format(block_id.lower(), len(block_rows) + 1),
                    "blockId": block_id,
                    "blockIndex": 0 if block_id == "FOCUSED_01" else 1,
                    "ordinal": ordinal,
                    "group": "FOCUSED",
                    "slotIndex": slot,
                    "targetId": target["targetId"],
                    "targetLabel": target["label"],
                    "frequencyHz": target["frequencyHz"],
                    "contextCondition": "FOCUSED_NO_CONTEXT",
                    "split": "FOCUSED_REFERENCE",
                })
                history.append(target["logicalBlockId"])
            rows.extend(block_rows)
        else:
            natural_index += 1
            block_rows, block_history = _build_natural_block(block_id, natural_index - 1, history, ordinal + 1, seed)
            for row in block_rows:
                ordinal += 1
                row["ordinal"] = ordinal
            history.extend(block_history)
            rows.extend(block_rows)
            block_seed_offsets[block_id] = natural_index - 1

    # Split each Natural frequency-by-condition cell across four DEV and four
    # LOCKED_TEST blocks. Each block consequently contains four or five DEV
    # rows and represents both splits, avoiding a chronological-half split.
    split_flip = _stable_seed(seed, "natural-split-pattern") % 2
    cell_parity = {
        (slot, condition): (slot * len(CONDITIONS) + CONDITIONS.index(condition) + split_flip) % 2
        for slot in range(3) for condition in CONDITIONS
    }
    natural_rows = [item for item in rows if item["group"] == "NATURAL_GAZE"]
    for row in natural_rows:
        slot = row["slotIndex"]
        condition_index = CONDITIONS.index(row["contextCondition"])
        cell_index = slot * 3 + condition_index
        parity = cell_parity[(slot, row["contextCondition"])]
        row["split"] = "DEV" if (row["blockIndex"] + parity) % 2 == 0 else "LOCKED_TEST"

    plan_body = {
        "recordType": "m19_phase2_only_precommitted_plan",
        "sessionKind": "FORMAL_PHASE2",
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "seed": int(seed),
        "protocol": deepcopy(PROTOCOL),
        "protocolSha256": _sha256_bytes(_canonical_json(PROTOCOL).encode("utf-8")),
        "createdUtc": _utc_now(),
        "formalTrialCount": len(rows),
        "blockSchedule": [dict(item) for item in PROTOCOL["blockLayout"]],
        "rows": rows,
        "contextPolicy": {
            "historyWindow": CONTEXT_HISTORY_WINDOW,
            "currentTargetPassedToGenerator": False,
            "manifestTargetIsQuotaReservationUntilRuntimeContextFreeze": True,
        },
        "splitAssignment": {
            "method": PROTOCOL["naturalBalance"]["splitMethod"],
            "byBlock": {
                block_id: {
                    "DEV": sum(1 for row in natural_rows if row["blockId"] == block_id and row["split"] == "DEV"),
                    "LOCKED_TEST": sum(1 for row in natural_rows if row["blockId"] == block_id and row["split"] == "LOCKED_TEST"),
                }
                for block_id in [item["blockId"] for item in PROTOCOL["blockLayout"] if item["group"] == "NATURAL_GAZE"]
            },
        },
        "seedDerivation": {
            "method": "sha256(seed|component...) first four bytes big endian",
            "splitCellParity": {"{}:{}".format(slot, condition): parity for (slot, condition), parity in cell_parity.items()},
        },
    }
    plan_body["scheduleFingerprint"] = _sha256_bytes(_canonical_json(plan_body).encode("utf-8"))
    validate_plan(plan_body)
    return plan_body


def validate_plan(plan: dict) -> dict:
    if not isinstance(plan, dict) or plan.get("protocolVersion") != PROTOCOL_VERSION:
        raise ResearchProtocolError("malformed or incompatible M19 Research plan")
    if plan.get("protocolSha256") != _sha256_bytes(_canonical_json(plan.get("protocol")).encode("utf-8")):
        raise ResearchProtocolError("Research plan protocol hash mismatch")
    rows = plan.get("rows")
    if not isinstance(rows, list) or len(rows) != 90:
        raise ResearchProtocolError("formal plan must contain exactly 90 rows")
    ids = [item.get("trialId") for item in rows]
    if len(set(ids)) != 90 or any(not isinstance(value, str) or not value for value in ids):
        raise ResearchProtocolError("formal plan trial IDs must be unique and non-empty")
    groups = Counter(item.get("group") for item in rows)
    if groups != Counter({"FOCUSED": 18, "NATURAL_GAZE": 72}):
        raise ResearchProtocolError("formal plan group counts do not reconcile")
    blocks = Counter(item.get("blockId") for item in rows)
    expected_blocks = {item["blockId"]: 9 for item in PROTOCOL["blockLayout"]}
    if dict(blocks) != expected_blocks:
        raise ResearchProtocolError("each of the ten protocol blocks must contain exactly nine trials")
    for row in rows:
        slot = row.get("slotIndex")
        if isinstance(slot, bool) or slot not in (0, 1, 2):
            raise ResearchProtocolError("plan contains an invalid research slot")
        target = SLOTS[slot]
        if row.get("targetId") != target["targetId"] or row.get("frequencyHz") != target["frequencyHz"]:
            raise ResearchProtocolError("target/slot/frequency relation is invalid")
        if row["group"] == "NATURAL_GAZE" and row.get("contextCondition") not in CONDITIONS:
            raise ResearchProtocolError("Natural row has an invalid Context condition")
        if row["group"] == "NATURAL_GAZE" and row.get("split") not in ("DEV", "LOCKED_TEST"):
            raise ResearchProtocolError("Natural row has no precommitted split")
    natural = [item for item in rows if item["group"] == "NATURAL_GAZE"]
    if Counter(item["slotIndex"] for item in natural) != Counter({0: 24, 1: 24, 2: 24}):
        raise ResearchProtocolError("Natural frequency balance failed")
    if Counter(item["contextCondition"] for item in natural) != Counter({key: 24 for key in CONDITIONS}):
        raise ResearchProtocolError("Natural Context balance failed")
    cells = Counter((item["slotIndex"], item["contextCondition"]) for item in natural)
    if any(cells[(slot, condition)] != 8 for slot in range(3) for condition in CONDITIONS):
        raise ResearchProtocolError("Natural frequency-by-Context balance failed")
    split_counts = Counter(item["split"] for item in natural)
    if split_counts != Counter({"DEV": 36, "LOCKED_TEST": 36}):
        raise ResearchProtocolError("Natural DEV/LOCKED_TEST split is not 36/36")
    by_cell_split = Counter((item["slotIndex"], item["contextCondition"], item["split"]) for item in natural)
    if any(by_cell_split[(slot, condition, split)] != 4 for slot in range(3) for condition in CONDITIONS for split in ("DEV", "LOCKED_TEST")):
        raise ResearchProtocolError("Natural split is not balanced within every frequency-by-Context cell")
    for block in [item["blockId"] for item in PROTOCOL["blockLayout"] if item["group"] == "NATURAL_GAZE"]:
        block_rows = [item for item in natural if item["blockId"] == block]
        block_cells = Counter((item["slotIndex"], item["contextCondition"]) for item in block_rows)
        if len(block_rows) != 9 or any(block_cells[(slot, condition)] != 1 for slot in range(3) for condition in CONDITIONS):
            raise ResearchProtocolError("Natural block {} does not contain each balanced cell once".format(block))
        block_split = Counter(item["split"] for item in block_rows)
        if block_split["DEV"] not in (4, 5) or block_split["LOCKED_TEST"] not in (4, 5):
            raise ResearchProtocolError("Natural DEV/LOCKED_TEST must both be represented in each block")
    history = []
    for row in rows:
        if row["group"] == "NATURAL_GAZE":
            snapshot = _generate_context(history, seed=int(plan["seed"]), ordinal=int(row["ordinal"]), condition=row["contextCondition"])
            top_slot = _context_top_slot(snapshot)
            if row["contextCondition"] == "ALIGNED" and top_slot != row["slotIndex"]:
                raise ResearchProtocolError("causal Context replay invalidated an ALIGNED row")
            if row["contextCondition"] == "CONFLICT" and (top_slot is None or top_slot == row["slotIndex"]):
                raise ResearchProtocolError("causal Context replay invalidated a CONFLICT row")
        history.append(next(item["logicalBlockId"] for item in SLOTS if item["slotIndex"] == row["slotIndex"]))
    return {"status": "PASS", "formalTrials": len(rows), "focused": groups["FOCUSED"], "natural": groups["NATURAL_GAZE"], "split": dict(split_counts), "scheduleFingerprint": plan.get("scheduleFingerprint")}


def save_or_load_plan(root: Path, seed: int, resume: bool = False) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "plan.json"
    if path.exists():
        if not resume:
            raise FileExistsError("Research plan already exists; refusing to regenerate after session start")
        plan = json.loads(path.read_text(encoding="utf-8"))
        validate_plan(plan)
        if int(plan.get("seed")) != int(seed):
            raise ResearchProtocolError("resume seed differs from the persisted plan")
        expected_fingerprint = plan.get("scheduleFingerprint")
        check = dict(plan)
        check.pop("scheduleFingerprint", None)
        actual_fingerprint = _sha256_bytes(_canonical_json(check).encode("utf-8"))
        if actual_fingerprint != expected_fingerprint:
            raise ResearchProtocolError("persisted plan fingerprint mismatch; refusing resume")
        return plan
    plan = build_plan(seed)
    _write_new_json(path, plan)
    return plan


def build_smoke_plan(seed: int = 190019) -> dict:
    """Create a distinct, non-formal six-trial target/capture smoke plan."""
    slots = [0, 1, 2, 0, 1, 2]
    random.Random(_stable_seed(seed, "m19-smoke-order")).shuffle(slots)
    rows = []
    for ordinal, slot in enumerate(slots, 1):
        target = SLOTS[slot]
        rows.append({
            "trialId": "m19-smoke-qc-{:02d}".format(ordinal),
            "blockId": "SMOKE_QC_01",
            "blockIndex": 0,
            "ordinal": ordinal,
            "group": "SMOKE_QC",
            "formal": False,
            "countsTowardPlan": True,
            "slotIndex": slot,
            "targetId": target["targetId"],
            "targetLabel": target["label"],
            "frequencyHz": target["frequencyHz"],
            "contextCondition": "SMOKE_QC_NO_CONTEXT",
            "split": "NON_FORMAL_QC",
        })
    body = {
        "recordType": "m19_research_smoke_qc_plan",
        "sessionKind": "SMOKE_QC",
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "seed": int(seed),
        "protocolSha256": _sha256_bytes(_canonical_json(PROTOCOL).encode("utf-8")),
        "createdUtc": _utc_now(),
        "formalTrialCount": 0,
        "smokeQcTrialCount": len(rows),
        "rows": rows,
        "rawFirst": True,
        "notPartOfFormalPlan": True,
    }
    body["scheduleFingerprint"] = _sha256_bytes(_canonical_json(body).encode("utf-8"))
    validate_smoke_plan(body)
    return body


def validate_smoke_plan(plan: dict) -> dict:
    if not isinstance(plan, dict) or plan.get("sessionKind") != "SMOKE_QC" or plan.get("protocolVersion") != PROTOCOL_VERSION:
        raise ResearchProtocolError("malformed or incompatible M19 Smoke/QC plan")
    if plan.get("protocolSha256") != _sha256_bytes(_canonical_json(PROTOCOL).encode("utf-8")):
        raise ResearchProtocolError("Smoke/QC plan protocol hash mismatch")
    rows = plan.get("rows")
    if not isinstance(rows, list) or len(rows) > 6 or len(rows) != int(plan.get("smokeQcTrialCount", -1)):
        raise ResearchProtocolError("Smoke/QC may contain at most six separately classified trials")
    if plan.get("formalTrialCount") != 0 or plan.get("notPartOfFormalPlan") is not True:
        raise ResearchProtocolError("Smoke/QC rows must remain outside formal N")
    ids = [row.get("trialId") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or len(set(ids)) != len(rows):
        raise ResearchProtocolError("Smoke/QC trial IDs must be unique")
    slot_counts = Counter()
    for ordinal, row in enumerate(rows, 1):
        slot = row.get("slotIndex")
        if isinstance(slot, bool) or slot not in (0, 1, 2):
            raise ResearchProtocolError("Smoke/QC plan has an invalid slot")
        target = SLOTS[slot]
        if (
            row.get("ordinal") != ordinal
            or row.get("group") != "SMOKE_QC"
            or row.get("formal") is not False
            or row.get("countsTowardPlan") is not True
            or row.get("blockId") != "SMOKE_QC_01"
            or row.get("targetId") != target["targetId"]
            or row.get("targetLabel") != target["label"]
            or row.get("frequencyHz") != target["frequencyHz"]
            or row.get("contextCondition") != "SMOKE_QC_NO_CONTEXT"
            or row.get("split") != "NON_FORMAL_QC"
        ):
            raise ResearchProtocolError("Smoke/QC row violates the non-formal target/capture contract")
        slot_counts[slot] += 1
    if rows and any(slot_counts[slot] > 2 for slot in range(3)):
        raise ResearchProtocolError("Smoke/QC plan may use each target no more than twice")
    check = dict(plan)
    fingerprint = check.pop("scheduleFingerprint", None)
    if fingerprint != _sha256_bytes(_canonical_json(check).encode("utf-8")):
        raise ResearchProtocolError("Smoke/QC plan fingerprint mismatch")
    return {"status": "PASS", "smokeQcTrials": len(rows), "formalTrials": 0, "scheduleFingerprint": fingerprint}


def save_or_load_smoke_plan(root: Path, seed: int, resume: bool = False) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "plan.json"
    if path.exists():
        if not resume:
            raise FileExistsError("Smoke/QC plan already exists; refusing to regenerate after session start")
        plan = json.loads(path.read_text(encoding="utf-8"))
        validate_smoke_plan(plan)
        if int(plan.get("seed")) != int(seed):
            raise ResearchProtocolError("Smoke/QC resume seed differs from the persisted plan")
        return plan
    plan = build_smoke_plan(seed)
    _write_new_json(path, plan)
    return plan


class ResearchTrialLifecycle:
    """Single formal attempt guard shared by the service and software tests."""
    def __init__(self):
        self.state = "IDLE"
        self.trial_id = None
        self.attempt_id = None

    def offer(self, trial_id: str, attempt_id: str) -> None:
        if self.state not in ("IDLE", "COMPLETE", "ABORTED", "REJECTED"):
            raise StateTransitionError("cannot offer while a Research attempt is pending")
        if not trial_id or not attempt_id:
            raise StateTransitionError("Research offer requires trial and attempt identity")
        self.trial_id = trial_id
        self.attempt_id = attempt_id
        self.state = "WAITING_TRIGGER"

    def trigger(self, trial_id: str) -> None:
        if self.state != "WAITING_TRIGGER" or trial_id != self.trial_id:
            raise StateTransitionError("duplicate, stale, or concurrent Research Trigger")
        self.state = "CAPTURING"

    def cancel_before_trigger(self) -> None:
        if self.state != "WAITING_TRIGGER":
            raise StateTransitionError("only a pre-trigger Research attempt can be cancelled")
        self.state = "ABORTED"

    def complete(self) -> None:
        if self.state != "CAPTURING":
            raise StateTransitionError("Research capture can complete only after Trigger")
        self.state = "COMPLETE"

    def reject_capture(self) -> None:
        if self.state != "CAPTURING":
            raise StateTransitionError("only a triggered Research capture can be rejected")
        self.state = "REJECTED"


def _continuous_resume_offsets(metadata_path: Path) -> tuple[int, int]:
    """Return the next global packet/sample identity from M18 nested metadata."""
    metadata_path = Path(metadata_path)
    if not metadata_path.is_file() or not metadata_path.stat().st_size:
        return 0, 0
    last = None
    with metadata_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                last = json.loads(line)
    if last is None:
        return 0, 0
    continuity = last.get("continuity", {})
    packet = last.get("packet", {})
    sequence = continuity.get("packet_sequence", continuity.get("packetSequence"))
    first_sample = continuity.get("cumulative_first_sample_index", continuity.get("cumulativeFirstSampleIndex"))
    sample_count = packet.get("sample_count", packet.get("sampleCount", packet.get("sampleCountPerChannel")))
    if sequence is None or first_sample is None or sample_count is None:
        raise ResearchProtocolError("cannot safely resume continuous raw packet/sample identity")
    sequence, first_sample, sample_count = int(sequence), int(first_sample), int(sample_count)
    if sequence < 0 or first_sample < 0 or sample_count <= 0:
        raise ResearchProtocolError("continuous raw packet/sample identity is invalid")
    return sequence + 1, first_sample + sample_count


class FakeContinuousSource:
    """Fast deterministic packet producer with a persistent virtual stream."""
    def __init__(self, recorder: M18SessionRecorder, clock: SyntheticClock, fault: str | None = None):
        self.recorder = recorder
        self.clock = clock
        self.source = SyntheticContinuousEegSource(recorder, clock, time_scale=1.0, packet_samples=PACKET_SAMPLES)
        metadata_path = recorder.root / "blocks" / "RESEARCH_STREAM" / "eeg" / "packet-metadata.jsonl"
        self.source.packet_sequence, self.source.sample_index = _continuous_resume_offsets(metadata_path)
        self.started = True
        self.closed = False
        self.fault = fault
        self.channel_admission = {
            "verdict": "READY_SYNTHETIC",
            "selectedChannels": list(range(CHANNEL_COUNT)),
            "channelCount": CHANNEL_COUNT,
            "qualityAssessment": "software_fake_source_only_not_human_eeg_quality",
        }

    def wait_before_trigger(self, seconds: float) -> None:
        if self.closed:
            raise ResearchProtocolError("continuous fake source is closed")
        if seconds > 0:
            self.source.emit_segment(seconds, None)

    def capture(self, event: dict, slot: int) -> dict:
        if self.fault == "continuity_failure":
            self.fault = None
            raise ResearchProtocolError("injected packet continuity failure")
        if self.fault == "recorder_failure":
            self.fault = None
            raise OSError("injected append-only raw recorder failure")
        anchor = self.source.capture_sample_anchor(event)
        start = int(anchor["sampleAnchorSampleIndex"])
        self.source.emit_segment(CAPTURE_SECONDS, slot)
        stop = int(self.source.sample_index)
        if stop - start != CAPTURE_SAMPLES:
            raise ResearchProtocolError("fake capture did not produce the frozen sample count")
        return {
            **anchor,
            "startSampleIndexInclusive": start,
            "endSampleIndexExclusive": stop,
            "sampleCount": stop - start,
            "nominalSamplingRateHz": NOMINAL_SAMPLING_RATE_HZ,
            "packetSequenceEndExclusive": self.source.packet_sequence,
            "continuityStatus": "continuous",
            "channelAdmission": self.channel_admission,
            "hardwareTimingVerified": False,
            "physicalOpticalTimingVerified": False,
        }

    def close(self) -> None:
        self.closed = True

    def tick(self, seconds: float = 0.02) -> None:
        if not self.closed:
            self.source.emit_segment(seconds, None)


class M19LiveResearchContinuousSource:
    """M18/M13.7 raw-first continuous ND8 stream with M19 trial anchors."""
    def __init__(self, session: M19ResearchSession, com_port: str):
        from integration.m18_unified_acquisition import M18LiveContinuousEegSource
        from integration.m19_eeg_backends import LIVE_CHANNEL_ADMISSION_SAMPLES

        self.session = session
        self.recorder = session.stream_recorder
        self._admission_sample_count = int(LIVE_CHANNEL_ADMISSION_SAMPLES)
        packet_offset, sample_offset = self._resume_offsets()
        self.source = M18LiveContinuousEegSource(
            self.recorder,
            com_port,
            packet_sequence_offset=packet_offset,
            sample_index_offset=sample_offset,
        )
        self.pipeline = self.source.pipeline
        self.channel_admission = None
        self.started = False
        self.closed = False

    def _resume_offsets(self) -> tuple[int, int]:
        metadata_path = self.recorder.root / "blocks" / "RESEARCH_STREAM" / "eeg" / "packet-metadata.jsonl"
        return _continuous_resume_offsets(metadata_path)

    def open(self) -> dict:
        if self.started:
            return dict(self.channel_admission)
        self.source.open()
        self.started = True
        self.pipeline.wait_for_stop_sample(self._admission_sample_count, 75.0)
        start = int(self.pipeline.buffer.start_sample or 0)
        values = self.pipeline.buffer.window(start, start + self._admission_sample_count)
        from eeg.decoder.formal_online import channel_admission

        self.channel_admission = channel_admission(
            values,
            self.pipeline.continuity_statuses,
            self.recorder.manifest.get("softwareCommit", "unavailable"),
            _utc_now(),
        )
        self.channel_admission["baselineSamplesPerChannel"] = self._admission_sample_count
        self.channel_admission["nominalSamplingRateHz"] = NOMINAL_SAMPLING_RATE_HZ
        self.channel_admission["recordedChannelCount"] = CHANNEL_COUNT
        if self.channel_admission.get("verdict") != "READY":
            self.recorder._touch_manifest(channelAdmission=self.channel_admission, selectedChannels=[])
            raise ResearchProtocolError("ND8 channel quality admission failed; no formal trial started")
        self.pipeline.selected_channels = tuple(self.channel_admission["selectedChannels"])
        self.recorder._touch_manifest(
            channelAdmission=self.channel_admission,
            selectedChannels=list(self.pipeline.selected_channels),
        )
        return dict(self.channel_admission)

    def wait_before_trigger(self, seconds: float) -> None:
        # The ND8 callback thread continues uninterrupted while the participant waits.
        del seconds

    def capture(self, event: dict, slot: int) -> dict:
        if not self.started or self.closed:
            raise ResearchProtocolError("live continuous stream is not running")
        del slot  # Acquisition does not inspect or decode the target frequency.
        status_start = len(self.pipeline.continuity_statuses)
        # Trial zero is logged with time.monotonic_ns(), while ND8 receive
        # timestamps use perf_counter_ns(). Take the segment boundary from the
        # same clock as packet receive timestamps instead of comparing the
        # event-log clock with the packet clock.
        self.pipeline.mark_next_segment(
            stimulus_onset_monotonic_ns=time.perf_counter_ns(),
            stimulus_onset_event_sequence=int(event["sequence"]),
        )
        self.pipeline.wait_for_next_segment_anchor(12.0)
        anchor = dict(self.pipeline.next_segment_anchor_metadata)
        if anchor.get("sampleAnchorSampleIndex") is None:
            raise ResearchProtocolError("live continuous stream did not provide a sample anchor")
        anchor["anchorBasis"] = "first_packet_received_at_or_after_m19_trigger_arm"
        start = int(anchor["sampleAnchorSampleIndex"])
        stop = start + CAPTURE_SAMPLES
        self.pipeline.wait_for_stop_sample(stop, CAPTURE_SECONDS + 12.0)
        captured = self.pipeline.buffer.window(start, stop)
        if int(captured.shape[-1]) != CAPTURE_SAMPLES:
            raise ResearchProtocolError("live raw sample window is incomplete")
        statuses = list(self.pipeline.continuity_statuses[status_start:])
        bad_statuses = [status for status in statuses if status not in ("initial", "continuous")]
        if bad_statuses:
            raise ResearchProtocolError("live capture continuity failure: " + ",".join(sorted(set(bad_statuses))))
        last_packet = self.pipeline.last_packet
        if last_packet is None:
            raise ResearchProtocolError("live capture has no recorded ND8 packet metadata")
        end_sequence = int(last_packet[0].packet_sequence) + 1
        return {
            **anchor,
            "startSampleIndexInclusive": start,
            "endSampleIndexExclusive": stop,
            "sampleCount": CAPTURE_SAMPLES,
            "nominalSamplingRateHz": NOMINAL_SAMPLING_RATE_HZ,
            "packetSequenceEndExclusive": end_sequence,
            "continuityStatus": "continuous",
            "continuityStatusesObserved": statuses,
            "channelAdmission": self.channel_admission,
            "selectedChannels": list(self.pipeline.selected_channels),
            "hardwareTimingVerified": False,
            "physicalOpticalTimingVerified": False,
        }

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.started:
            self.source.close()


class M19ResearchAcquisitionService:
    """One persistent Quest Research session using a precommitted plan."""
    def __init__(self, session: M19ResearchSession, source, transport, cue_backend):
        self.session = session
        self.source = source
        self.transport = transport
        self.lifecycle = ResearchTrialLifecycle()
        self.current_row = None
        self.current_offer = None
        self.current_snapshot = None
        self.current_relation = None
        self.current_attempt_id = None
        self.paused_at_block = None
        self.resume_block_on_ready = False
        self.block_pause_requested = False
        self.block_pause = None
        self.waiting_for_ready = True
        self.completed_session = False
        self.capture_failure_paused = False
        if session.metadata.get("pauseReason") == "BLOCK_COMPLETE":
            last_block = session.metadata.get("lastCompletedBlockId")
            if last_block:
                self.paused_at_block = str(last_block)
                self.resume_block_on_ready = True
        self.cue = AudioCueSystem(
            cue_backend,
            self._cue_event,
            monotonic_ns=time.monotonic_ns,
            utc_now=_utc_now,
            sleep=time.sleep,
            required_silence_seconds=1.0,
            timing_configuration={
                "targetFrequenciesHzBySlot": {0: 660.0, 1: 880.0, 2: 1320.0},
                "targetBeepSeconds": 0.22,
                "interTargetBeepSilenceSeconds": 0.45,
                "targetGroupSeparationSeconds": 0.70,
            },
            fail_on_backend_error=True,
        )

    def _cue_event(self, event_type, trialId=None, sessionId=None, **values):
        self.session.event_log.append(event_type, sessionId=self.session.session_id, trialId=trialId, **values)

    def _send(self, message_type: str, **values):
        payload = {"protocolVersion": 1, "messageType": message_type, **values}
        self.transport.send_research_message(payload)
        self.session.event_log.append("QUEST_MESSAGE_SENT", sessionId=self.session.session_id, messageType=message_type, message=payload)

    def handle_event(self, event: dict) -> None:
        if not isinstance(event, dict):
            self.session.event_log.append("MALFORMED_QUEST_MESSAGE_REJECTED", reason="not_object", failClosed=True)
            return
        event_type = event.get("messageType")
        if event_type == "m19_research_ready":
            if self.completed_session or self.capture_failure_paused:
                self.session.event_log.append("STALE_RESEARCH_READY_REJECTED", sessionId=self.session.session_id, failClosed=True)
                return
            if self.paused_at_block is not None:
                if not self.resume_block_on_ready:
                    self.session.event_log.append("STALE_RESEARCH_READY_REJECTED", sessionId=self.session.session_id, failClosed=True)
                    return
                self.session.event_log.append("RESEARCH_BLOCK_RESUMED", sessionId=self.session.session_id, blockId=self.paused_at_block, resumeMode="session_restart")
                self.paused_at_block = None
                self.resume_block_on_ready = False
                self.session.update_metadata(status="RECORDING", resumeCursor=self.session.next_index, pauseReason=None, resumedUtc=_utc_now())
            if self.current_offer is not None:
                self.session.event_log.append("OVERLAPPING_RESEARCH_READY_REJECTED", sessionId=self.session.session_id, trialId=self.current_row["trialId"], failClosed=True)
                return
            self.waiting_for_ready = False
            self._prepare_and_offer()
            return
        if event_type == "m19_research_offer_ack":
            if not self._matches_current(event):
                self.session.event_log.append("STALE_RESEARCH_OFFER_ACK_REJECTED", sessionId=self.session.session_id, trialId=event.get("trialId"), failClosed=True)
                return
            self.session.event_log.append("RESEARCH_OFFER_ACKNOWLEDGED", sessionId=self.session.session_id, trialId=self.current_row["trialId"], attemptId=self.current_attempt_id)
            return
        if event_type == "m19_research_cancel":
            if not self._matches_current(event) or self.lifecycle.state != "WAITING_TRIGGER":
                self.session.event_log.append("STALE_OR_POSTTRIGGER_CANCEL_REJECTED", sessionId=self.session.session_id, trialId=event.get("trialId"), attemptId=event.get("attemptId"), failClosed=True)
                return
            self.lifecycle.cancel_before_trigger()
            self.session.event_log.append("PRETRIGGER_CANCELLED", sessionId=self.session.session_id, trialId=self.current_row["trialId"], attemptId=self.current_attempt_id, reason=event.get("reason", "participant_cancel"), formal=False, countsTowardPlan=False)
            self.session.record_attempt(self.current_attempt_id, {
                "recordType": "m19_research_attempt",
                "sessionId": self.session.session_id,
                "trialId": self.current_row["trialId"],
                "attemptId": self.current_attempt_id,
                "status": "CANCELLED_BEFORE_TRIGGER",
                "formal": False,
                "contextSnapshot": self.current_snapshot,
                "rawRetained": True,
                "countsTowardPlan": False,
            })
            self.current_offer = None
            self.current_row = None
            self.current_snapshot = None
            self.current_attempt_id = None
            self.waiting_for_ready = True
            return
        if event_type == "m19_research_resume":
            if self.completed_session or self.paused_at_block is None or event.get("sessionId") != self.session.session_id:
                self.session.event_log.append("STALE_RESEARCH_RESUME_REJECTED", sessionId=event.get("sessionId"), blockId=event.get("blockId"), failClosed=True)
                return
            if event.get("blockId") != self.paused_at_block:
                self.session.event_log.append("RESEARCH_RESUME_BLOCK_MISMATCH_REJECTED", sessionId=self.session.session_id, expectedBlockId=self.paused_at_block, receivedBlockId=event.get("blockId"), failClosed=True)
                return
            self.session.event_log.append("RESEARCH_BLOCK_RESUMED", sessionId=self.session.session_id, blockId=self.paused_at_block)
            self.paused_at_block = None
            self.resume_block_on_ready = False
            self.session.update_metadata(status="RECORDING", resumeCursor=self.session.next_index, pauseReason=None, resumedUtc=_utc_now())
            self.waiting_for_ready = False
            self._prepare_and_offer()
            return
        if event_type == "m19_research_trigger":
            self._handle_trigger(event)
            return
        self.session.event_log.append("UNSUPPORTED_RESEARCH_MESSAGE_REJECTED", sessionId=event.get("sessionId"), messageType=event_type, failClosed=True)

    def _matches_current(self, event: dict) -> bool:
        return (
            self.current_offer is not None
            and event.get("sessionId") == self.session.session_id
            and event.get("trialId") == self.current_offer["trialId"]
            and event.get("attemptId") == self.current_offer["attemptId"]
        )

    def _prepare_and_offer(self) -> None:
        if self.current_offer is not None or self.completed_session or self.paused_at_block is not None:
            raise StateTransitionError("cannot prepare another Research offer while one is pending or paused")
        if self.session.next_index >= len(self.session.plan["rows"]):
            self._finish_session()
            return
        row = self.session.plan["rows"][self.session.next_index]
        self.session.attempt_counter += 1
        attempt_id = "{}-attempt-{:04d}".format(row["trialId"], self.session.attempt_counter)
        snapshot, relation = _prepare_trial(
            self.session,
            row,
            attempt_id=attempt_id,
            generated_at_monotonic_ns=time.monotonic_ns(),
        )
        self.current_row = row
        self.current_snapshot = snapshot
        self.current_relation = relation
        self.current_attempt_id = attempt_id
        self.current_offer = {
            "sessionId": self.session.session_id,
            "trialId": row["trialId"],
            "attemptId": attempt_id,
            "blockId": row["blockId"],
            "ordinal": row["ordinal"],
            "slotIndex": row["slotIndex"],
            "frequencyHz": row["frequencyHz"],
            "targetLabel": row["targetLabel"],
            "cueBeepCount": row["slotIndex"] + 1,
        }
        self.lifecycle = ResearchTrialLifecycle()
        self.lifecycle.offer(row["trialId"], attempt_id)
        if snapshot is not None:
            self.session.record_context(snapshot, row)
            self.session.event_log.append("CONTEXT_FROZEN", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], contextSnapshotId=snapshot["contextSnapshotId"], contextSnapshotSha256=snapshot["snapshotCanonicalSha256"], condition=row["contextCondition"], completedHistoryWindow=snapshot["causalHistoryWindow"], targetVisibleToGenerator=False, futureEegVisibleToGenerator=False, futureOutcomeVisibleToGenerator=False)
        self.session.event_log.append("TARGET_ASSIGNED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], slotIndex=row["slotIndex"], targetId=row["targetId"], targetLabel=row["targetLabel"], frequencyHz=row["frequencyHz"], contextCondition=row["contextCondition"], observedContextRelation=relation, conditionHiddenFromParticipant=True)
        self.session.event_log.append("RESEARCH_ATTEMPT_STARTED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], ordinal=row["ordinal"], formal=False)
        self.cue.target_beep_frequency_hz = RESEARCH_TARGET_CUE_TONES_HZ[row["slotIndex"]]
        self.cue.target_cue(row["trialId"], self.session.session_id, row["slotIndex"], row["frequencyHz"])
        self.session.event_log.append("RESEARCH_OFFER_READY", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, targetLabel=row["targetLabel"], cueBeepCount=row["slotIndex"] + 1, contextConditionSentToQuest=False)
        self._send("m19_research_offer", **self.current_offer)

    def _handle_trigger(self, event: dict) -> None:
        if not self._matches_current(event) or self.lifecycle.state != "WAITING_TRIGGER":
            self.session.event_log.append("DUPLICATE_OR_STALE_TRIGGER_REJECTED", sessionId=event.get("sessionId"), trialId=event.get("trialId"), attemptId=event.get("attemptId"), failClosed=True)
            return
        try:
            self.lifecycle.trigger(self.current_row["trialId"])
        except StateTransitionError as error:
            self.session.event_log.append("RESEARCH_TRIGGER_REJECTED", sessionId=self.session.session_id, trialId=self.current_row["trialId"], attemptId=self.current_attempt_id, reason=str(error), failClosed=True)
            return
        trigger = self.session.event_log.append(
            "TRIGGER_DWELL_COMPLETED",
            sessionId=self.session.session_id,
            trialId=self.current_row["trialId"],
            attemptId=self.current_attempt_id,
            dwellSeconds=1.5,
            questEventUtc=event.get("eventUtc"),
            questSoftwareFrame=event.get("softwareFrame"),
            pcReceiveMonotonicNs=time.monotonic_ns(),
            softwareTrigger=True,
            physicalOpticalOnset=False,
        )
        trial_zero = self.session.event_log.append(
            "TRIAL_ZERO",
            sessionId=self.session.session_id,
            trialId=self.current_row["trialId"],
            attemptId=self.current_attempt_id,
            triggerEventSequence=trigger["sequence"],
            triggerEventMonotonicNs=trigger["monotonicNs"],
            trialZeroBasis="PC receipt of Quest 1.5-second Trigger dwell completion; not optical onset",
            physicalOpticalOnsetVerified=False,
        )
        try:
            capture = self.source.capture(trial_zero, self.current_row["slotIndex"])
            if capture.get("sampleCount") != CAPTURE_SAMPLES or capture.get("endSampleIndexExclusive", 0) - capture.get("startSampleIndexInclusive", 0) != CAPTURE_SAMPLES:
                raise ResearchProtocolError("capture does not equal the fixed 4-second sample range")
        except (OSError, ResearchProtocolError, RuntimeError) as error:
            self.lifecycle.reject_capture()
            self.session.event_log.append("TRIAL_CAPTURE_REJECTED", sessionId=self.session.session_id, trialId=self.current_row["trialId"], attemptId=self.current_attempt_id, failureType=type(error).__name__, failureReason=str(error), formal=False, rawPreserved=True)
            self.session.record_attempt(self.current_attempt_id, {
                "recordType": "m19_research_attempt",
                "sessionId": self.session.session_id,
                "trialId": self.current_row["trialId"],
                "attemptId": self.current_attempt_id,
                "status": "REJECTED_CAPTURE",
                "formal": False,
                "failureReason": str(error),
                "contextSnapshot": self.current_snapshot,
                "rawReferences": ["continuous_stream/blocks/RESEARCH_STREAM/eeg/raw-eeg.jsonl"],
                "rawRetained": True,
                "countsTowardPlan": False,
            })
            offer = dict(self.current_offer)
            self.current_offer = None
            self.current_row = None
            self.current_attempt_id = None
            self.current_snapshot = None
            self._send("m19_research_trial_complete", **{**offer, "status": "rejected_capture"})
            if self.session.session_kind == "SMOKE_QC":
                self.capture_failure_paused = True
                self.waiting_for_ready = False
                self.session.update_metadata(
                    status="PAUSED_RESUMABLE",
                    resumeCursor=self.session.next_index,
                    pauseUtc=_utc_now(),
                    pauseReason="TRIAL_CAPTURE_REJECTED",
                    lastFailureType=type(error).__name__,
                    lastFailureReason=str(error),
                )
            else:
                self.waiting_for_ready = True
            return

        row = self.current_row
        attempt_id = self.current_attempt_id
        raw_refs = [
            "continuous_stream/blocks/RESEARCH_STREAM/eeg/raw-eeg.jsonl",
            "continuous_stream/blocks/RESEARCH_STREAM/eeg/packet-metadata.jsonl",
        ]
        attempt_record = {
            "recordType": "m19_research_trial_capture",
            "schemaVersion": SCHEMA_VERSION,
            "sessionId": self.session.session_id,
            "trialId": row["trialId"],
            "attemptId": attempt_id,
            "blockId": row["blockId"],
            "ordinal": row["ordinal"],
            "group": row["group"],
            "split": row["split"],
            "formal": bool(row.get("formal", True)),
            "countsTowardPlan": bool(row.get("countsTowardPlan", True)),
            "trialZero": {"eventSequence": trial_zero["sequence"], "monotonicNs": trial_zero["monotonicNs"], "basis": trial_zero["trialZeroBasis"]},
            "target": {"slotIndex": row["slotIndex"], "targetId": row["targetId"], "label": row["targetLabel"], "frequencyHz": row["frequencyHz"]},
            "contextCondition": row["contextCondition"],
            "observedContextRelation": self.current_relation,
            "contextSnapshot": self.current_snapshot,
            "sampleRange": capture,
            "rawReferences": raw_refs,
            "channelAdmission": capture.get("channelAdmission"),
            "analysisStatus": "NOT_RUN_ACQUISITION_ONLY",
        }
        attempt_path = self.session.record_attempt(attempt_id, attempt_record)
        is_formal = bool(row.get("formal", True))
        counts_toward_plan = bool(row.get("countsTowardPlan", True))
        self.session.event_log.append("TRIAL_CAPTURE_COMPLETE", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], formal=is_formal, countsTowardPlan=counts_toward_plan, sampleCount=capture["sampleCount"], startSampleIndex=capture["startSampleIndexInclusive"], endSampleIndexExclusive=capture["endSampleIndexExclusive"], rawReferences=raw_refs, attemptRecord=attempt_path.relative_to(self.session.root).as_posix())
        self.session.event_log.append("EPISODE_COMPLETED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], group=row["group"], formal=is_formal, countsTowardPlan=counts_toward_plan, split=row["split"], contextCondition=row["contextCondition"], slotIndex=row["slotIndex"], targetId=row["targetId"], frequencyHz=row["frequencyHz"], rawPreserved=True)
        self.session.completed_ids.add(row["trialId"])
        self.lifecycle.complete()
        self.session.completed_history.append(next(item["logicalBlockId"] for item in SLOTS if item["slotIndex"] == row["slotIndex"]))
        self.session.next_index += 1
        completed_block_id = row["blockId"]
        next_offer = dict(self.current_offer)
        self.current_offer = None
        self.current_row = None
        self.current_attempt_id = None
        self.current_snapshot = None
        self.current_relation = None
        if self.session.next_index == self.session.total_planned_trials:
            self._finish_session()
            return
        if (
            bool(row.get("formal", True))
            and self.session.session_kind == "FORMAL_PHASE2"
            and row["ordinal"] % 9 == 0
        ):
            next_row = self.session.plan["rows"][self.session.next_index]
            rest_seconds = 180 if completed_block_id == "NATURAL_04" else 60 if completed_block_id in ("NATURAL_02", "NATURAL_06") else 0
            completed_label = ("F" if completed_block_id.startswith("FOCUSED_") else "N") + str(int(completed_block_id.rsplit("_", 1)[1]))
            next_block_id = next_row["blockId"]
            next_label = ("F" if next_block_id.startswith("FOCUSED_") else "N") + str(int(next_block_id.rsplit("_", 1)[1]))
            self.paused_at_block = completed_block_id
            self.block_pause_requested = True
            self.block_pause = {"blockComplete": completed_label, "nextBlock": next_label}
            self.session.event_log.append("RESEARCH_BLOCK_PAUSED", sessionId=self.session.session_id, blockId=completed_block_id, nextBlockId=next_block_id, completedFormalTrials=self.session.next_index, recommendedRestSeconds=rest_seconds, automaticSessionExit=True)
            self.session.update_metadata(status="PAUSED_RESUMABLE", resumeCursor=self.session.next_index, pauseUtc=_utc_now(), pauseReason="BLOCK_COMPLETE", lastCompletedBlockId=completed_block_id, nextBlockId=next_block_id)
            pause_status = "long_break" if rest_seconds >= 180 else "short_break" if rest_seconds else "block_complete"
            self._send("m19_research_block_pause", sessionId=self.session.session_id, blockId=completed_block_id, nextBlockId=next_block_id, status=pause_status, restRecommendedSeconds=rest_seconds)
            print("BLOCK_COMPLETE={}".format(completed_label), flush=True)
            print("NEXT_BLOCK={}".format(next_label), flush=True)
            return
        self._send("m19_research_trial_complete", **{**next_offer, "status": "complete"})
        self.waiting_for_ready = True

    def _finish_session(self) -> None:
        if self.completed_session:
            return
        if self.session.next_index != self.session.total_planned_trials:
            raise ResearchProtocolError("cannot finalize before all planned Research/QC trials complete")
        stream_manifest = self.session.stream_recorder.finalize()
        raw_file = self.session.stream_root / "blocks" / "RESEARCH_STREAM" / "eeg" / "raw-eeg.jsonl"
        packet_file = self.session.stream_root / "blocks" / "RESEARCH_STREAM" / "eeg" / "packet-metadata.jsonl"
        live_nd8 = self.session.metadata.get("sourceType") == "live_nd8"
        self.session.update_metadata(
            status="FINALIZED",
            completedFormalTrials=sum(1 for row in self.session.plan["rows"][:self.session.next_index] if bool(row.get("formal", True))),
            completedQcTrials=sum(1 for row in self.session.plan["rows"][:self.session.next_index] if not bool(row.get("formal", True))),
            finalizedUtc=_utc_now(),
            streamRecorderManifestStatus=stream_manifest.get("status"),
            rawFileSha256=_sha256_file(raw_file),
            packetMetadataSha256=_sha256_file(packet_file),
            hardwareBoundary={"questOperated": live_nd8, "nd8Operated": live_nd8, "physicalTimingVerified": False, "humanEegCollected": live_nd8},
        )
        self.completed_session = True
        self._send("m19_research_session_complete", sessionId=self.session.session_id, status="complete", formalTrialCount=self.session.metadata.get("completedFormalTrials", 0), smokeQcTrialCount=self.session.metadata.get("completedQcTrials", 0), sessionKind=self.session.session_kind)

    def run_forever(self) -> None:
        self.transport.accept_research_peer()
        self.session.event_log.append("RESEARCH_PC_LISTENER_CONNECTED", sessionId=self.session.session_id, peer=self.transport.evidence.get("peer"))
        try:
            while not self.completed_session:
                events = self.transport.poll_controller_events(0.05)
                for event in events:
                    self.handle_event(event)
                    if self.capture_failure_paused or self.block_pause_requested:
                        break
                if self.capture_failure_paused or self.block_pause_requested:
                    break
                if callable(getattr(self.source, "tick", None)) and self.current_offer is not None:
                    self.source.tick(0.02)
        except KeyboardInterrupt:
            self.session.event_log.append("RESEARCH_OPERATOR_PAUSE", sessionId=self.session.session_id, nextFormalOrdinal=self.session.next_index + 1, reason="keyboard_interrupt")
            self.session.update_metadata(status="PAUSED_RESUMABLE", resumeCursor=self.session.next_index, pauseUtc=_utc_now())
        finally:
            self.source.close()


class M19ResearchSession:
    def __init__(self, root: Path, plan: dict, session_id: str, resume: bool = False, source_type: str = "synthetic"):
        self.root = Path(root)
        self.plan = plan
        self.session_id = str(session_id)
        self.session_kind = str(plan.get("sessionKind", "FORMAL_PHASE2"))
        if self.session_kind == "SMOKE_QC":
            validate_smoke_plan(plan)
        else:
            validate_plan(plan)
        self.total_planned_trials = len(plan["rows"])
        if source_type not in ("synthetic", "live_nd8"):
            raise ValueError("source_type must be synthetic or live_nd8")
        self.root.mkdir(parents=True, exist_ok=True)
        self.plan_path = self.root / "plan.json"
        if not self.plan_path.exists():
            _write_new_json(self.plan_path, plan)
        else:
            persisted = json.loads(self.plan_path.read_text(encoding="utf-8"))
            if persisted.get("sessionKind") == "SMOKE_QC":
                validate_smoke_plan(persisted)
            else:
                validate_plan(persisted)
            if persisted.get("scheduleFingerprint") != plan.get("scheduleFingerprint"):
                raise ResearchProtocolError("session root plan differs from requested immutable plan")
        self.event_log = AppendOnlyJsonl(self.root / "events.jsonl")
        self.context_path = self.root / "context-snapshots.jsonl"
        self.context_path.touch(exist_ok=True)
        self.session_path = self.root / "session.json"
        self.stream_root = self.root / "continuous_stream"
        if self.session_path.exists():
            if not resume:
                raise FileExistsError("Research session exists; explicit --resume is required")
            self.metadata = json.loads(self.session_path.read_text(encoding="utf-8"))
            if self.metadata.get("sessionId") != self.session_id:
                raise ResearchProtocolError("resume session ID mismatch")
            if self.metadata.get("planFingerprint") != self.plan.get("scheduleFingerprint"):
                raise ResearchProtocolError("resume plan fingerprint mismatch")
            if self.metadata.get("sessionKind", "FORMAL_PHASE2") != self.session_kind:
                raise ResearchProtocolError("resume session kind mismatch")
            if self.metadata.get("sourceType") != source_type:
                raise ResearchProtocolError("resume source type mismatch")
            if self.metadata.get("status") == "FINALIZED":
                raise ResearchProtocolError("finalized Research session is immutable")
        else:
            if any(item.name not in ("plan.json", "events.jsonl", "context-snapshots.jsonl") for item in self.root.iterdir()):
                raise FileExistsError("non-empty Research session root has no session manifest")
            self.metadata = {
                "recordType": "m19_research_session_manifest",
                "schemaVersion": SCHEMA_VERSION,
                "protocolVersion": PROTOCOL_VERSION,
                "sessionKind": self.session_kind,
                "formalTrialCount": 90 if self.session_kind == "FORMAL_PHASE2" else 0,
                "plannedQcTrialCount": self.total_planned_trials if self.session_kind == "SMOKE_QC" else 0,
                "sessionId": self.session_id,
                "participantId": "P001",
                "planFingerprint": self.plan["scheduleFingerprint"],
                "protocolSha256": self.plan["protocolSha256"],
                "status": "RECORDING",
                "sourceType": source_type,
                "samplingRateHz": NOMINAL_SAMPLING_RATE_HZ,
                "channelCount": CHANNEL_COUNT,
                "rawFirst": True,
                "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "physicalTimingVerified": False},
                "files": {"events": "events.jsonl", "plan": "plan.json", "contexts": "context-snapshots.jsonl", "continuousRaw": "continuous_stream/blocks/RESEARCH_STREAM/eeg/raw-eeg.jsonl"},
            }
            _atomic_metadata(self.session_path, self.metadata)
        self.stream_recorder = M18SessionRecorder(
            self.stream_root,
            self.session_id + "-continuous-stream",
            {
                "recordType": "m19_research_continuous_stream_protocol",
                "protocolVersion": PROTOCOL_VERSION,
                "productionBoundary": deepcopy(PROTOCOL["productionBoundary"]),
                "timing": deepcopy(PROTOCOL["timing"]),
            },
            source_type="synthetic" if self.metadata.get("sourceType") == "synthetic" else "live_nd8",
            sampling_rate_hz=NOMINAL_SAMPLING_RATE_HZ,
            channel_count=CHANNEL_COUNT,
            resume=resume,
        )
        self.stream_recorder.set_active_block("RESEARCH_STREAM")
        self.completed_ids = {
            item.get("trialId") for item in self._events()
            if item.get("eventType") == "EPISODE_COMPLETED" and (item.get("countsTowardPlan") is True or item.get("formal") is True)
        }
        ordered_ids = [row["trialId"] for row in self.plan["rows"]]
        prefix = []
        for identity in ordered_ids:
            if identity in self.completed_ids:
                prefix.append(identity)
            else:
                break
        if set(prefix) != self.completed_ids:
            raise ResearchProtocolError("resume ledger is not a completed prefix; refusing to skip or duplicate trials")
        self.next_index = len(prefix)
        self.attempt_counter = sum(1 for item in self._events() if item.get("eventType") == "RESEARCH_ATTEMPT_STARTED")
        self.completed_history = [
            next(item["logicalBlockId"] for item in SLOTS if item["slotIndex"] == row["slotIndex"])
            for row in self.plan["rows"][:self.next_index]
        ]

    def _events(self) -> list[dict]:
        if not (self.root / "events.jsonl").exists():
            return []
        values = []
        with (self.root / "events.jsonl").open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    values.append(json.loads(line))
        return values

    def update_metadata(self, **values) -> None:
        self.metadata.update(values)
        _atomic_metadata(self.session_path, self.metadata)

    def record_context(self, snapshot: dict, row: dict) -> None:
        record = {
            **snapshot,
            "sessionId": self.session_id,
            "blockId": row["blockId"],
            "trialId": row["trialId"],
            "visibility": "research_provenance",
            "runtimeConditionBlind": True,
        }
        with self.context_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def record_attempt(self, attempt_id: str, attempt: dict) -> Path:
        path = self.root / "attempts" / (attempt_id + ".json")
        _write_new_json(path, attempt)
        return path


def _prepare_trial(
    session: M19ResearchSession,
    row: dict,
    *,
    attempt_id: str | None = None,
    generated_at_monotonic_ns: int | None = None,
) -> tuple[dict | None, str]:
    condition = row["contextCondition"]
    if condition in ("FOCUSED_NO_CONTEXT", "SMOKE_QC_NO_CONTEXT"):
        return None, "NOT_APPLICABLE_" + condition.removesuffix("_NO_CONTEXT")
    snapshot = _generate_context(
        session.completed_history,
        seed=session.plan["seed"],
        ordinal=row["ordinal"],
        condition=condition,
        attempt_id=attempt_id,
        generated_at_monotonic_ns=generated_at_monotonic_ns,
    )
    top_slot = _context_top_slot(snapshot)
    if condition == "ALIGNED" and top_slot != row["slotIndex"]:
        raise ResearchProtocolError("invalid target/context relation: ALIGNED target is not the frozen unique top")
    if condition == "CONFLICT" and (top_slot is None or top_slot == row["slotIndex"]):
        raise ResearchProtocolError("invalid target/context relation: CONFLICT target equals the frozen unique top")
    relation = "NEUTRAL" if condition == "NEUTRAL" else ("ALIGNED" if top_slot == row["slotIndex"] else "CONFLICT")
    return snapshot, relation


def run_software_rehearsal(
    output_dir: Path,
    *,
    trial_count: int = 90,
    seed: int = 190019,
    session_id: str | None = None,
    resume: bool = False,
    fault: str | None = None,
    wait_seconds: float = 0.35,
) -> dict:
    if trial_count not in (1, 9, 90):
        raise ValueError("rehearsal size must be 1 trial, 1 block (9), or the full 90 trials")
    output_dir = Path(output_dir)
    plan_exists = (output_dir / "plan.json").is_file()
    session_exists = (output_dir / "session.json").is_file()
    if session_exists and not resume:
        raise FileExistsError("Research session exists; pass --resume to continue its persisted manifest")
    plan = save_or_load_plan(output_dir, seed=seed, resume=plan_exists)
    plan_status = validate_plan(plan)
    if session_id is None and resume and (output_dir / "session.json").is_file():
        session_id = json.loads((output_dir / "session.json").read_text(encoding="utf-8")).get("sessionId")
    session_id = session_id or "m19-p2-software-" + uuid.uuid4().hex[:12]
    session = M19ResearchSession(output_dir, plan, session_id, resume=resume)
    session.update_metadata(sourceType="synthetic", activeMode="RESEARCH_ACQUISITION", continuousStreamStarted=True)
    clock = SyntheticClock()
    source = FakeContinuousSource(session.stream_recorder, clock, fault=fault)
    cue_backend = NullLoggingAudioBackend(clock.monotonic_ns)
    cue = AudioCueSystem(
        cue_backend,
        lambda event_type, trialId=None, sessionId=None, **values: session.event_log.append(
            event_type, sessionId=session_id, trialId=trialId, **values
        ),
        monotonic_ns=clock.monotonic_ns,
        utc_now=clock.utc_now,
        sleep=clock.sleep,
        required_silence_seconds=1.0,
        timing_configuration={"targetBeepSeconds": 0.15, "interTargetBeepSilenceSeconds": 0.28, "targetGroupSeparationSeconds": 0.70},
    )
    lifecycle = ResearchTrialLifecycle()
    attempted = 0
    failures = []
    if session.next_index > trial_count:
        source.close()
        raise ResearchProtocolError("resume cursor is beyond requested rehearsal size")
    stop_at = trial_count
    while session.next_index < stop_at:
        row = plan["rows"][session.next_index]
        session.attempt_counter += 1
        attempted += 1
        attempt_id = "{}-attempt-{:04d}".format(row["trialId"], session.attempt_counter)
        snapshot, relation = _prepare_trial(session, row, attempt_id=attempt_id)
        if snapshot is not None:
            session.record_context(snapshot, row)
            session.event_log.append("CONTEXT_FROZEN", sessionId=session_id, trialId=row["trialId"], blockId=row["blockId"], contextSnapshotId=snapshot["contextSnapshotId"], contextSnapshotSha256=snapshot["snapshotCanonicalSha256"], condition=row["contextCondition"], completedHistoryWindow=snapshot["causalHistoryWindow"], targetVisibleToGenerator=False, futureEegVisibleToGenerator=False, futureOutcomeVisibleToGenerator=False)
        session.event_log.append("TARGET_ASSIGNED", sessionId=session_id, trialId=row["trialId"], blockId=row["blockId"], slotIndex=row["slotIndex"], targetId=row["targetId"], targetLabel=row["targetLabel"], frequencyHz=row["frequencyHz"], contextCondition=row["contextCondition"], observedContextRelation=relation, conditionHiddenFromParticipant=True)
        session.event_log.append("RESEARCH_ATTEMPT_STARTED", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], ordinal=row["ordinal"], formal=False)
        lifecycle.offer(row["trialId"], attempt_id)
        cue.target_cue(row["trialId"], session_id, row["slotIndex"], row["frequencyHz"])
        session.event_log.append("RESEARCH_OFFER_READY", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, targetLabel=row["targetLabel"], cueBeepCount=row["slotIndex"] + 1, contextConditionSentToQuest=False)
        source.wait_before_trigger(wait_seconds + ((row["ordinal"] % 5) * 0.1))
        if fault == "pretrigger_cancel" and attempted == 1:
            fault = None
            lifecycle.cancel_before_trigger()
            session.event_log.append("PRETRIGGER_CANCELLED", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, formal=False, countsTowardPlan=False)
            session.record_attempt(attempt_id, {"attemptId": attempt_id, "trialId": row["trialId"], "status": "CANCELLED_BEFORE_TRIGGER", "formal": False, "rawRetained": True})
            if trial_count == 1:
                source.close()
                return {"status": "PASS", "faultInjected": fault, "formalCompleted": 0, "attempts": 1, "expectedPretriggerCancellation": True}
            # Keep the same manifest row pending; a new attempt identity will be assigned.
            lifecycle = ResearchTrialLifecycle()
            continue
        trigger_event = session.event_log.append("TRIGGER_DWELL_COMPLETED", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, triggerUtc=_utc_now(), dwellSeconds=1.5, softwareTrigger=True, physicalOpticalOnset=False)
        lifecycle.trigger(row["trialId"])
        trial_zero = session.event_log.append("TRIAL_ZERO", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, triggerEventSequence=trigger_event["sequence"], triggerEventMonotonicNs=trigger_event["monotonicNs"], trialZeroBasis="Quest Trigger dwell completion mapped to software event; not optical onset", physicalOpticalOnsetVerified=False)
        if fault == "duplicate_trigger" and attempted == 1:
            try:
                lifecycle.trigger(row["trialId"])
            except StateTransitionError:
                session.event_log.append("DUPLICATE_TRIGGER_REJECTED", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, failClosed=True)
            else:
                raise ResearchProtocolError("duplicate Trigger fault was not rejected")
        if fault == "trigger_while_pending" and attempted == 1:
            try:
                lifecycle.offer("unexpected-second-trial", "unexpected-attempt")
            except StateTransitionError:
                session.event_log.append("TRIGGER_WHILE_PENDING_REJECTED", sessionId=session_id, trialId=row["trialId"], failClosed=True)
            else:
                raise ResearchProtocolError("overlapping trial offer was not rejected")
        try:
            capture = source.capture(trial_zero, row["slotIndex"])
        except (OSError, ResearchProtocolError) as error:
            lifecycle.reject_capture()
            reason = "continuity_failure" if "continuity" in str(error).lower() else "recorder_failure"
            failures.append({"trialId": row["trialId"], "attemptId": attempt_id, "reason": reason})
            session.event_log.append("TRIAL_CAPTURE_REJECTED", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, failureType=type(error).__name__, failureReason=str(error), formal=False, rawPreserved=True)
            session.record_attempt(attempt_id, {"attemptId": attempt_id, "trialId": row["trialId"], "status": "REJECTED", "formal": False, "failureReason": str(error), "contextSnapshot": snapshot, "rawRetained": True})
            if fault in ("continuity_failure", "recorder_failure"):
                source.close()
                return {"status": "PASS", "faultInjected": fault, "formalCompleted": session.next_index, "rejectedAttempts": failures, "failedCaptureCountsTowardFormal": False, "planRegenerated": False}
            raise
        if capture["sampleCount"] != CAPTURE_SAMPLES or capture["endSampleIndexExclusive"] - capture["startSampleIndexInclusive"] != CAPTURE_SAMPLES:
            lifecycle.reject_capture()
            raise ResearchProtocolError("captured sample range does not equal fixed 4-second protocol")
        lifecycle.complete()
        raw_reference = "continuous_stream/blocks/RESEARCH_STREAM/eeg/raw-eeg.jsonl"
        attempt_record = {
            "recordType": "m19_research_trial_capture",
            "schemaVersion": SCHEMA_VERSION,
            "sessionId": session_id,
            "trialId": row["trialId"],
            "attemptId": attempt_id,
            "blockId": row["blockId"],
            "ordinal": row["ordinal"],
            "group": row["group"],
            "split": row["split"],
            "formal": True,
            "trialZero": {"eventSequence": trial_zero["sequence"], "monotonicNs": trial_zero["monotonicNs"], "basis": trial_zero["trialZeroBasis"]},
            "target": {"slotIndex": row["slotIndex"], "targetId": row["targetId"], "label": row["targetLabel"], "frequencyHz": row["frequencyHz"]},
            "contextCondition": row["contextCondition"],
            "observedContextRelation": relation,
            "contextSnapshot": snapshot,
            "sampleRange": capture,
            "rawReferences": [raw_reference, "continuous_stream/blocks/RESEARCH_STREAM/eeg/packet-metadata.jsonl"],
            "channelAdmission": source.channel_admission,
            "analysisStatus": "NOT_RUN_ACQUISITION_ONLY",
        }
        attempt_path = session.record_attempt(attempt_id, attempt_record)
        session.event_log.append("TRIAL_CAPTURE_COMPLETE", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], formal=True, sampleCount=capture["sampleCount"], rawReference=raw_reference, attemptRecord=attempt_path.relative_to(output_dir).as_posix())
        session.event_log.append("EPISODE_COMPLETED", sessionId=session_id, trialId=row["trialId"], attemptId=attempt_id, blockId=row["blockId"], group=row["group"], formal=True, split=row["split"], contextCondition=row["contextCondition"], slotIndex=row["slotIndex"], targetId=row["targetId"], frequencyHz=row["frequencyHz"], rawPreserved=True)
        session.completed_ids.add(row["trialId"])
        session.next_index += 1
        session.completed_history.append(next(item["logicalBlockId"] for item in SLOTS if item["slotIndex"] == row["slotIndex"]))
        if fault == "stale_trigger" and attempted == 1:
            try:
                lifecycle.trigger("old-trial-id")
            except StateTransitionError:
                session.event_log.append("STALE_TRIGGER_REJECTED", sessionId=session_id, trialId="old-trial-id", failClosed=True)
            else:
                raise ResearchProtocolError("stale Trigger fault was not rejected")

    source.close()
    if trial_count == 90 and session.next_index == 90:
        stream_manifest = session.stream_recorder.finalize()
        raw_file = session.stream_root / "blocks" / "RESEARCH_STREAM" / "eeg" / "raw-eeg.jsonl"
        packet_file = session.stream_root / "blocks" / "RESEARCH_STREAM" / "eeg" / "packet-metadata.jsonl"
        session.update_metadata(status="FINALIZED", completedFormalTrials=90, finalizedUtc=_utc_now(), continuousStreamStarted=True, rawFileSha256=_sha256_file(raw_file), packetMetadataSha256=_sha256_file(packet_file), streamRecorderManifestStatus=stream_manifest.get("status"), hardwareBoundary={"questOperated": False, "nd8Operated": False, "physicalTimingVerified": False})
        summary = build_rehearsal_summary(output_dir, plan, session)
        _write_new_json(output_dir / "dry_run_summary.json", summary)
        return summary
    source.close()
    return {
        "status": "PASS" if not failures else "FAIL",
        "rehearsalSize": trial_count,
        "formalCompletedThisRun": min(trial_count, session.next_index),
        "sessionId": session_id,
        "planFingerprint": plan["scheduleFingerprint"],
        "resumeCursor": session.next_index,
        "failures": failures,
        "rawStreamPath": str(session.stream_root / "blocks" / "RESEARCH_STREAM" / "eeg" / "raw-eeg.jsonl"),
    }


def build_rehearsal_summary(output_dir: Path, plan: dict, session: M19ResearchSession) -> dict:
    events = session._events()
    completed = [item for item in events if item.get("eventType") == "EPISODE_COMPLETED" and item.get("formal") is True]
    by_group = Counter(item.get("group") for item in completed)
    natural = [item for item in completed if item.get("group") == "NATURAL_GAZE"]
    block_counts = Counter(item.get("blockId") for item in completed)
    natural_cells = Counter((item.get("slotIndex"), item.get("contextCondition")) for item in natural)
    split_counts = Counter(item.get("split") for item in natural)
    raw_path = session.stream_root / "blocks" / "RESEARCH_STREAM" / "eeg" / "raw-eeg.jsonl"
    packet_path = session.stream_root / "blocks" / "RESEARCH_STREAM" / "eeg" / "packet-metadata.jsonl"
    checks = {
        "formal90": len(completed) == 90,
        "focused18": by_group["FOCUSED"] == 18,
        "natural72": by_group["NATURAL_GAZE"] == 72,
        "eightNaturalBlocksOfNine": all(block_counts["NATURAL_{:02d}".format(index)] == 9 for index in range(1, 9)),
        "naturalCellCountsEight": all(natural_cells[(slot, condition)] == 8 for slot in range(3) for condition in CONDITIONS),
        "devLockedSplit36Each": split_counts == Counter({"DEV": 36, "LOCKED_TEST": 36}),
        "uniqueCompletedIds": len({item.get("trialId") for item in completed}) == 90,
        "allAttemptArtifactsExist": all((Path(output_dir) / "attempts" / (item.get("attemptId") + ".json")).is_file() for item in completed),
        "rawStreamExists": raw_path.is_file() and raw_path.stat().st_size > 0,
        "packetMetadataExists": packet_path.is_file() and packet_path.stat().st_size > 0,
        "noHardwareOperation": not session.metadata.get("hardwareBoundary", {}).get("questOperated") and not session.metadata.get("hardwareBoundary", {}).get("nd8Operated"),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "recordType": "m19_research_90_trial_accelerated_dry_run_summary",
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "sessionId": session.session_id,
        "planFingerprint": plan["scheduleFingerprint"],
        "formalTrialsCompleted": len(completed),
        "focusedCompleted": by_group["FOCUSED"],
        "naturalCompleted": by_group["NATURAL_GAZE"],
        "naturalBlockCounts": {"NATURAL_{:02d}".format(index): block_counts["NATURAL_{:02d}".format(index)] for index in range(1, 9)},
        "naturalFrequencyCounts": {str(slot): sum(1 for item in natural if item.get("slotIndex") == slot) for slot in range(3)},
        "naturalConditionCounts": {condition: sum(1 for item in natural if item.get("contextCondition") == condition) for condition in CONDITIONS},
        "naturalFrequencyConditionCells": {"{}:{}".format(slot, condition): natural_cells[(slot, condition)] for slot in range(3) for condition in CONDITIONS},
        "naturalSplitCounts": dict(split_counts),
        "uniqueTrialIds": len({item.get("trialId") for item in completed}),
        "uniqueAttemptIds": len({item.get("attemptId") for item in completed}),
        "rawStream": {"path": "continuous_stream/blocks/RESEARCH_STREAM/eeg/raw-eeg.jsonl", "sha256": _sha256_file(raw_path), "bytes": raw_path.stat().st_size, "packetMetadataPath": "continuous_stream/blocks/RESEARCH_STREAM/eeg/packet-metadata.jsonl", "packetMetadataSha256": _sha256_file(packet_path)},
        "checks": checks,
        "analysisPerformed": False,
        "decoderChanged": False,
        "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "physicalTimingVerified": False, "humanEegCollected": False},
    }


def software_preflight(output_root: Path, *, source: str, confirm_live_human: bool = False, com_port: str | None = None, quest_host: str = "0.0.0.0", quest_port: int = 11001, cue_backend: str = "pc-winsound") -> dict:
    output_root = Path(output_root)
    import_ready = False
    cue_ready = False
    plan_ready = False
    try:
        from eeg.decoder.formal_online import channel_admission as _channel_admission
        from integration.m13_7_golden_session import AudioCueSystem as _AudioCueSystem
        from integration.m18_unified_acquisition import M18LiveContinuousEegSource as _M18LiveSource
        from integration.m8_selection_orchestration import QuestSelectionTcpServer as _QuestTcpServer
        import_ready = callable(_channel_admission) and _AudioCueSystem is not None and _M18LiveSource is not None and _QuestTcpServer is not None
    except Exception:
        import_ready = False
    try:
        if cue_backend == "pc-winsound":
            PcToneAudioBackend()
        elif cue_backend == "null":
            NullLoggingAudioBackend()
        else:
            raise ValueError("unsupported cue backend")
        cue_ready = True
    except Exception:
        cue_ready = False
    try:
        validate_plan(build_plan(190019))
        plan_ready = True
    except Exception:
        plan_ready = False
    live_backend_runtime_ready = source != "live-nd8"
    if source == "live-nd8":
        try:
            from integration.m8_live_nd8 import validate_vendor_cpython39_runtime

            validate_vendor_cpython39_runtime()
            live_backend_runtime_ready = True
        except Exception:
            # Import and interpreter validation only; this never enumerates or
            # opens a COM device. Live acquisition stays separately gated.
            live_backend_runtime_ready = False
    parent = output_root.parent
    checks = {
        "protocolValid": PROTOCOL_VERSION == PROTOCOL["protocolVersion"] and len(SLOTS) == 3,
        "slotMappingValid": [item["frequencyHz"] for item in SLOTS] == [7.2, 9.0, 12.0],
        "researchOutputSeparate": "m18" not in str(output_root).lower() and "m13_10" not in str(output_root).lower(),
        "outputRootUnique": not output_root.exists(),
        "outputRootParentExists": parent.exists() and parent.is_dir(),
        "outputRootParentWritable": parent.exists() and os.access(parent, os.W_OK),
        "sourceRecognized": source in ("synthetic", "live-nd8"),
        "liveConfirmationPresent": source != "live-nd8" or bool(confirm_live_human),
        "liveComConfigured": source != "live-nd8" or bool(com_port and str(com_port).strip()),
        "listenerHostValid": isinstance(quest_host, str) and bool(quest_host.strip()),
        "listenerPortValid": 1 <= int(quest_port) <= 65535,
        "pythonRuntimeCompatible": (
            platform.python_implementation() == "CPython" and sys.version_info >= (3, 9)
        ),
        "liveNd8RuntimeReady": live_backend_runtime_ready,
        "requiredImportsAvailable": import_ready,
        "cueBackendAvailable": cue_ready,
        "phase2PlanAndContextValid": plan_ready,
        "captureTimingValid": CAPTURE_SECONDS == 4.0 and CAPTURE_SAMPLES == 4000 and NOMINAL_SAMPLING_RATE_HZ == 1000.0,
        "fakeContinuousSourceCompatible": callable(getattr(SyntheticContinuousEegSource, "emit_segment", None)) and callable(getattr(M18SessionRecorder, "record_packet", None)),
        "noActiveTrialState": ResearchTrialLifecycle().state == "IDLE",
        "acquisitionOnlyDecoderBoundary": PROTOCOL["productionBoundary"]["decoderChanged"] is False,
        "noHardwareOpened": True,
    }
    status = "PASS" if all(checks.values()) else "FAIL_CLOSED"
    return {
        "status": status,
        "mode": "RESEARCH_ACQUISITION",
        "source": source,
        "outputRoot": str(output_root),
        "questListener": {"host": quest_host, "port": int(quest_port)},
        "liveConfirmationProvided": bool(confirm_live_human),
        "cueBackend": cue_backend,
        "pythonVersion": ".".join(str(value) for value in sys.version_info[:3]),
        "liveHardwareOpened": False,
        "COMOpened": False,
        "questBuilt": False,
        "physicalRobotOperated": False,
        "checks": checks,
    }


def software_resume_preflight(
    session_root: Path,
    *,
    session_id: str,
    seed: int,
    source: str,
    session_kind: str = "FORMAL_PHASE2",
    confirm_live_human: bool = False,
    com_port: str | None = None,
    quest_host: str = "0.0.0.0",
    quest_port: int = 11001,
    cue_backend: str = "pc-winsound",
) -> dict:
    """Read-only validation of an exact persisted Research session before resume."""
    session_root = Path(session_root)
    report = software_preflight(
        session_root,
        source=source,
        confirm_live_human=confirm_live_human,
        com_port=com_port,
        quest_host=quest_host,
        quest_port=quest_port,
        cue_backend=cue_backend,
    )
    checks = dict(report["checks"])
    checks["outputRootUnique"] = False  # Replaced by exact-session validation below.
    checks["resumeManifestValid"] = False
    checks["resumeSessionIdMatches"] = False
    checks["resumeSourceMatches"] = False
    checks["resumeSeedMatches"] = False
    checks["resumeNotFinalized"] = False
    try:
        manifest = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
        plan = json.loads((session_root / "plan.json").read_text(encoding="utf-8"))
        if session_kind == "SMOKE_QC":
            validate_smoke_plan(plan)
        else:
            validate_plan(plan)
        checks["resumeManifestValid"] = manifest.get("planFingerprint") == plan.get("scheduleFingerprint")
        checks["resumeManifestValid"] = checks["resumeManifestValid"] and manifest.get("sessionKind", "FORMAL_PHASE2") == session_kind and plan.get("sessionKind", "FORMAL_PHASE2") == session_kind
        checks["resumeSessionIdMatches"] = manifest.get("sessionId") == str(session_id)
        expected_source = "live_nd8" if source == "live-nd8" else "synthetic"
        checks["resumeSourceMatches"] = manifest.get("sourceType") == expected_source
        checks["resumeSeedMatches"] = int(plan.get("seed")) == int(seed)
        checks["resumeNotFinalized"] = manifest.get("status") != "FINALIZED"
        checks["outputRootUnique"] = checks["resumeManifestValid"] and checks["resumeSessionIdMatches"] and checks["resumeSourceMatches"] and checks["resumeSeedMatches"] and checks["resumeNotFinalized"]
    except (OSError, ValueError, TypeError, KeyError, ResearchProtocolError, json.JSONDecodeError):
        pass
    report["checks"] = checks
    report["resume"] = True
    report["sessionId"] = str(session_id)
    report["status"] = "PASS" if all(checks.values()) else "FAIL_CLOSED"
    return report


def serve_research_session(
    *,
    session_root: Path,
    session_id: str,
    seed: int,
    source_name: str,
    com_port: str | None,
    confirm_live_human: bool,
    quest_host: str,
    quest_port: int,
    resume: bool,
    cue_backend_name: str,
    session_kind: str = "FORMAL_PHASE2",
) -> dict:
    """Run the persistent Quest Research service after a side-effect-free preflight."""
    source_type = "live_nd8" if source_name == "live-nd8" else "synthetic"
    preflight = (
        software_resume_preflight(
            session_root,
            session_id=session_id,
            seed=seed,
            source=source_name,
            session_kind=session_kind,
            confirm_live_human=confirm_live_human,
            com_port=com_port,
            quest_host=quest_host,
            quest_port=quest_port,
            cue_backend=cue_backend_name,
        )
        if resume
        else software_preflight(
            session_root,
            source=source_name,
            confirm_live_human=confirm_live_human,
            com_port=com_port,
            quest_host=quest_host,
            quest_port=quest_port,
            cue_backend=cue_backend_name,
        )
    )
    preflight["sessionId"] = str(session_id)
    preflight["resume"] = bool(resume)
    if not str(session_id).strip():
        preflight["checks"]["sessionIdProvided"] = False
        preflight["status"] = "FAIL_CLOSED"
    else:
        preflight["checks"]["sessionIdProvided"] = True
    if cue_backend_name not in ("pc-winsound", "null"):
        preflight["checks"]["cueBackendRecognized"] = False
        preflight["status"] = "FAIL_CLOSED"
    else:
        preflight["checks"]["cueBackendRecognized"] = True
    preflight["checks"]["sessionKindRecognized"] = session_kind in ("FORMAL_PHASE2", "SMOKE_QC")
    if not preflight["checks"]["sessionKindRecognized"]:
        preflight["status"] = "FAIL_CLOSED"
    if preflight["status"] != "PASS":
        return {"status": "FAIL_CLOSED", "preflight": preflight, "liveHardwareOpened": False, "COMOpened": False}

    # Validate/save the frozen schedule only after the caller passes every
    # gate; resume always reads the exact persisted plan and seed.
    if session_kind == "SMOKE_QC":
        plan = save_or_load_smoke_plan(session_root, seed=seed, resume=resume)
        validate_smoke_plan(plan)
    else:
        plan = save_or_load_plan(session_root, seed=seed, resume=resume)
        validate_plan(plan)
    transport = None
    source = None
    session = None
    try:
        from integration.m8_selection_orchestration import QuestSelectionTcpServer

        transport = QuestSelectionTcpServer(host=quest_host, port=quest_port).start()
        session = M19ResearchSession(session_root, plan, session_id, resume=resume, source_type=source_type)
        active_mode = "RESEARCH_ACQUISITION" if session_kind == "FORMAL_PHASE2" else "SMOKE_QC"
        session.update_metadata(activeMode=active_mode, launchPreflight=preflight, listenerHost=quest_host, listenerPort=transport.port)
        if source_name == "live-nd8":
            if not confirm_live_human:
                raise ResearchProtocolError("live ND8 confirmation gate was not satisfied")
            source = M19LiveResearchContinuousSource(session, str(com_port))
            source.open()
            session.update_metadata(continuousStreamStarted=True, channelAdmission=source.channel_admission)
        else:
            clock = SyntheticClock()
            source = FakeContinuousSource(session.stream_recorder, clock)
            session.update_metadata(continuousStreamStarted=True, channelAdmission=source.channel_admission)
        backend = PcToneAudioBackend() if cue_backend_name == "pc-winsound" else NullLoggingAudioBackend(time.monotonic_ns)
        service = M19ResearchAcquisitionService(session, source, transport, backend)
        service.run_forever()
        return {
            "status": "PASS" if service.completed_session else "PAUSED_RESUMABLE",
            "sessionId": session_id,
            "planFingerprint": plan["scheduleFingerprint"],
            "resumeCursor": session.next_index,
            "completedPlanTrials": session.next_index,
            "formalCompleted": sum(1 for item in session._events() if item.get("eventType") == "EPISODE_COMPLETED" and item.get("formal") is True),
            "smokeQcCompleted": sum(1 for item in session._events() if item.get("eventType") == "EPISODE_COMPLETED" and item.get("group") == "SMOKE_QC"),
            "sessionKind": session.session_kind,
            "sessionRoot": str(Path(session_root).resolve()),
            "liveHardwareOpened": source_name == "live-nd8" and bool(getattr(source, "started", False)),
            "COMOpened": source_name == "live-nd8" and bool(getattr(source, "started", False)),
            "questPeerAccepted": bool(transport.evidence.get("connectionAccepted")),
        }
    except KeyboardInterrupt:
        if session is not None:
            session.event_log.append("RESEARCH_OPERATOR_PAUSE", sessionId=session.session_id, nextFormalOrdinal=session.next_index + 1, reason="keyboard_interrupt")
            session.update_metadata(status="PAUSED_RESUMABLE", resumeCursor=session.next_index, pauseUtc=_utc_now())
        return {"status": "PAUSED_RESUMABLE", "sessionId": session_id, "resumeCursor": 0 if session is None else session.next_index}
    except Exception as error:
        if session is not None:
            session.event_log.append("RESEARCH_SERVICE_FAILURE", sessionId=session.session_id, failureType=type(error).__name__, failureReason=str(error), rawPreserved=True, resumeCursor=session.next_index)
            if session.metadata.get("status") != "FINALIZED":
                session.update_metadata(status="FAULTED_RESUMABLE", resumeCursor=session.next_index, lastFailureType=type(error).__name__, lastFailureReason=str(error))
        raise
    finally:
        if source is not None:
            source.close()
        if transport is not None:
            transport.close()


def run_fault_injection_matrix(output_root: Path, seed: int = 190019) -> dict:
    """Run bounded persistent fault/recovery cases without opening hardware."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    results = {}

    for fault, expected_event in (
        ("duplicate_trigger", "DUPLICATE_TRIGGER_REJECTED"),
        ("trigger_while_pending", "TRIGGER_WHILE_PENDING_REJECTED"),
        ("stale_trigger", "STALE_TRIGGER_REJECTED"),
    ):
        case_root = output_root / fault
        report = run_software_rehearsal(case_root, trial_count=1, seed=seed, fault=fault)
        events = [json.loads(line) for line in (case_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        results[fault] = {
            "status": "PASS" if report["status"] == "PASS" and any(item.get("eventType") == expected_event for item in events) else "FAIL",
            "report": report,
            "expectedRejectionEventObserved": any(item.get("eventType") == expected_event for item in events),
        }

    cancel_root = output_root / "pretrigger_cancel_retry"
    cancel_report = run_software_rehearsal(cancel_root, trial_count=9, seed=seed, fault="pretrigger_cancel")
    cancel_events = [json.loads(line) for line in (cancel_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    cancel_completed = [item for item in cancel_events if item.get("eventType") == "EPISODE_COMPLETED" and item.get("formal") is True]
    results["pretrigger_cancel_retry"] = {
        "status": "PASS" if cancel_report["status"] == "PASS" and len(cancel_completed) == 9 and any(item.get("eventType") == "PRETRIGGER_CANCELLED" for item in cancel_events) else "FAIL",
        "report": cancel_report,
        "formalCompleted": len(cancel_completed),
        "cancelObserved": any(item.get("eventType") == "PRETRIGGER_CANCELLED" for item in cancel_events),
    }

    for fault in ("continuity_failure", "recorder_failure"):
        case_root = output_root / (fault + "_resume")
        rejected = run_software_rehearsal(case_root, trial_count=1, seed=seed, fault=fault)
        recovered = run_software_rehearsal(case_root, trial_count=1, seed=seed, resume=True)
        events = [json.loads(line) for line in (case_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        completed = [item for item in events if item.get("eventType") == "EPISODE_COMPLETED" and item.get("formal") is True]
        rejected_count = sum(item.get("eventType") == "TRIAL_CAPTURE_REJECTED" for item in events)
        metadata_path = case_root / "continuous_stream" / "blocks" / "RESEARCH_STREAM" / "eeg" / "packet-metadata.jsonl"
        packet_rows = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        packet_sequences = [int(item["packet"]["packet_sequence"]) for item in packet_rows]
        sample_starts = [int(item["continuity"]["cumulative_first_sample_index"]) for item in packet_rows]
        packet_contiguous = len(packet_sequences) == len(set(packet_sequences)) and all(right == left + 1 for left, right in zip(packet_sequences, packet_sequences[1:]))
        samples_contiguous = all(sample_starts[index + 1] == sample_starts[index] + int(packet_rows[index]["packet"]["sample_count"]) for index in range(len(packet_rows) - 1))
        results[fault + "_resume"] = {
            "status": "PASS" if rejected.get("status") == "PASS" and recovered.get("status") == "PASS" and rejected_count == 1 and len(completed) == 1 and packet_contiguous and samples_contiguous else "FAIL",
            "firstAttempt": rejected,
            "resumeAttempt": recovered,
            "rejectedCaptureCount": rejected_count,
            "formalCompleted": len(completed),
            "globalPacketIdentityContinuous": packet_contiguous,
            "globalSampleIdentityContinuous": samples_contiguous,
        }

    guard_root = output_root / "guard_tests"
    plan_root = guard_root / "immutable_plan"
    plan = save_or_load_plan(plan_root, seed=seed)
    session = M19ResearchSession(plan_root, plan, "fault-matrix-immutable", source_type="synthetic")
    try:
        try:
            save_or_load_plan(plan_root, seed=seed + 1, resume=False)
            regeneration_rejected = False
        except FileExistsError:
            regeneration_rejected = True
        try:
            save_or_load_plan(plan_root, seed=seed + 1, resume=True)
            seed_mismatch_rejected = False
        except ResearchProtocolError:
            seed_mismatch_rejected = True
    finally:
        session.stream_recorder.finalize()
    results["plan_regeneration_after_session_start"] = {
        "status": "PASS" if regeneration_rejected and seed_mismatch_rejected else "FAIL",
        "regenerationRejected": regeneration_rejected,
        "resumeSeedMismatchRejected": seed_mismatch_rejected,
    }

    malformed = deepcopy(plan)
    malformed["rows"].pop()
    try:
        validate_plan(malformed)
        malformed_rejected = False
    except ResearchProtocolError:
        malformed_rejected = True
    invalid_relation = deepcopy(plan)
    aligned = next(row for row in invalid_relation["rows"] if row.get("group") == "NATURAL_GAZE" and row.get("contextCondition") == "ALIGNED")
    replacement_slot = (aligned["slotIndex"] + 1) % len(SLOTS)
    aligned["slotIndex"] = replacement_slot
    aligned["targetId"] = SLOTS[replacement_slot]["targetId"]
    aligned["targetLabel"] = SLOTS[replacement_slot]["label"]
    aligned["frequencyHz"] = SLOTS[replacement_slot]["frequencyHz"]
    try:
        validate_plan(invalid_relation)
        invalid_relation_rejected = False
    except ResearchProtocolError:
        invalid_relation_rejected = True
    missing_confirmation = software_preflight(output_root / "must-not-be-created", source="live-nd8", com_port="COM11")
    collision = software_preflight(plan_root, source="synthetic")
    results["malformed_plan"] = {"status": "PASS" if malformed_rejected else "FAIL", "rejected": malformed_rejected}
    results["invalid_target_context_relation"] = {"status": "PASS" if invalid_relation_rejected else "FAIL", "rejected": invalid_relation_rejected}
    results["missing_live_confirmation"] = {"status": "PASS" if missing_confirmation["status"] == "FAIL_CLOSED" and not missing_confirmation["COMOpened"] else "FAIL", "preflight": missing_confirmation}
    results["output_root_collision"] = {"status": "PASS" if collision["status"] == "FAIL_CLOSED" and not collision["checks"]["outputRootUnique"] else "FAIL", "preflight": collision}

    summary = {
        "status": "PASS" if all(item.get("status") == "PASS" for item in results.values()) else "FAIL",
        "recordType": "m19_research_fault_injection_matrix",
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "seed": int(seed),
        "hardwareOpened": False,
        "formalTrialDataOverwritten": False,
        "caseCount": len(results),
        "cases": results,
    }
    _write_new_json(output_root / "fault_matrix_summary.json", summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output-root", required=True, type=Path)
    preflight.add_argument("--source", choices=("synthetic", "live-nd8"), default="synthetic")
    preflight.add_argument("--com", dest="com_port", default=None)
    preflight.add_argument("--confirm-live-human", action="store_true")
    preflight.add_argument("--host", default="0.0.0.0")
    preflight.add_argument("--port", type=int, default=11001)
    preflight.add_argument("--cue-backend", choices=("pc-winsound", "null"), default="pc-winsound")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--output-dir", required=True, type=Path)
    plan_parser.add_argument("--seed", type=int, default=190019)
    rehearse = sub.add_parser("rehearse")
    rehearse.add_argument("--output-dir", required=True, type=Path)
    rehearse.add_argument("--size", choices=("trial", "block", "full-90"), default="full-90")
    rehearse.add_argument("--seed", type=int, default=190019)
    rehearse.add_argument("--resume", action="store_true")
    rehearse.add_argument("--session-id", default=None)
    rehearse.add_argument("--fault", choices=("duplicate_trigger", "trigger_while_pending", "pretrigger_cancel", "continuity_failure", "recorder_failure", "stale_trigger"), default=None)
    serve = sub.add_parser("serve", help="start the explicit operator Research Acquisition session")
    serve.add_argument("--session-root", required=True, type=Path)
    serve.add_argument("--session-id", required=True)
    serve.add_argument("--seed", type=int, required=True)
    serve.add_argument("--source", choices=("live-nd8", "synthetic"), default="live-nd8")
    serve.add_argument("--com", dest="com_port", default=None)
    serve.add_argument("--confirm-live-human", action="store_true")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=11001)
    serve.add_argument("--resume", action="store_true")
    serve.add_argument("--cue-backend", choices=("pc-winsound", "null"), default="pc-winsound")
    serve.add_argument("--mode", choices=("research", "smoke-qc"), default="research")
    faults = sub.add_parser("faults", help="run the bounded software-only M19 fault/recovery matrix")
    faults.add_argument("--output-dir", required=True, type=Path)
    faults.add_argument("--seed", type=int, default=190019)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        report = software_preflight(args.output_root, source=args.source, confirm_live_human=args.confirm_live_human, com_port=args.com_port, quest_host=args.host, quest_port=args.port, cue_backend=args.cue_backend)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 2
    if args.command == "plan":
        result = save_or_load_plan(args.output_dir, seed=args.seed, resume=False)
        print(json.dumps({"status": "PASS", "formalTrials": 90, "scheduleFingerprint": result["scheduleFingerprint"]}, sort_keys=True))
        return 0
    if args.command == "serve":
        report = serve_research_session(
            session_root=args.session_root,
            session_id=args.session_id,
            seed=args.seed,
            source_name=args.source,
            com_port=args.com_port,
            confirm_live_human=args.confirm_live_human,
            quest_host=args.host,
            quest_port=args.port,
            resume=args.resume,
            cue_backend_name=args.cue_backend,
            session_kind="SMOKE_QC" if args.mode == "smoke-qc" else "FORMAL_PHASE2",
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report.get("status") in ("PASS", "PAUSED_RESUMABLE") else 2
    if args.command == "faults":
        report = run_fault_injection_matrix(args.output_dir, seed=args.seed)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    trial_count = {"trial": 1, "block": 9, "full-90": 90}[args.size]
    report = run_software_rehearsal(args.output_dir, trial_count=trial_count, seed=args.seed, session_id=args.session_id, resume=args.resume, fault=args.fault)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
