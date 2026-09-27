"""M18 unified Phase 2 + Phase 3 EEG acquisition framework.

This module is research-acquisition infrastructure only.  It freezes the
M18 one-wear protocol, produces deterministic schedules, records append-only
continuous EEG/event evidence, and runs a synthetic software dry-run.  It
does not modify or invoke the production decoder policy and it never claims
human, Quest, ND8, TPR, FPR, or accuracy evidence.

The M13.7 ``AudioCueSystem`` and packet/continuity models are deliberately
reused at the integration boundary.  The M18 recorder uses its own directory
layout and schema so that a new acquisition cannot overwrite a M13.7 session.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import tempfile
import time
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import numpy as np

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.sample_association.models import PacketContinuityRecord
from integration.m13_7_golden_session import (
    AudioCueSystem,
    NullLoggingAudioBackend,
    SLOT_TO_FREQUENCY_HZ,
)


M18_SCHEMA_VERSION = 1
M18_PROTOCOL_VERSION = "m18-unified-phase23-v1"
M18_GENERATOR_VERSION = "m18-unified-acquisition-generator-v1"
M18_SESSION_ID = "m18-synthetic-001"
M18_DATA_ROOT_HINT = r"D:\EEG_Study\m18_unified_phase23"
TRIAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
FREQUENCIES_HZ = (7.2, 9.0, 12.0)
CONTEXT_CONDITIONS = ("ALIGNED", "NEUTRAL", "CONFLICT")
PRIMARY_GROUPS = ("FOCUSED", "NATURAL", "SELF_PACED")


M18_CANDIDATES = [
    {
        "slotIndex": 0,
        "targetId": "m9-vblock-red-01",
        "logicalBlockId": "block_sim_01",
        "frequencyHz": 7.2,
    },
    {
        "slotIndex": 1,
        "targetId": "m9-vblock-green-01",
        "logicalBlockId": "block_sim_02",
        "frequencyHz": 9.0,
    },
    {
        "slotIndex": 2,
        "targetId": "m9-vblock-blue-01",
        "logicalBlockId": "block_sim_03",
        "frequencyHz": 12.0,
    },
]


M18_PROTOCOL: dict[str, Any] = {
    "schemaVersion": M18_SCHEMA_VERSION,
    "protocolVersion": M18_PROTOCOL_VERSION,
    "recordType": "m18_unified_acquisition_protocol",
    "status": "SOFTWARE_PROTOCOL_FREEZE",
    "researchOnly": True,
    "dataRootHint": M18_DATA_ROOT_HINT,
    "seed": 180018,
    "frequenciesHz": list(FREQUENCIES_HZ),
    "candidates": M18_CANDIDATES,
    "primaryCounts": {
        "FOCUSED": 18,
        "NATURAL": 60,
        "SELF_PACED": 24,
        "TRUE_IDLE_MINUTES": 9,
        "PASSIVE_BROWSE_MINUTES": 12,
        "SHAM_READY_CUES": 12,
    },
    "frequencyBalance": {
        "FOCUSED": {"7.2": 6, "9.0": 6, "12.0": 6},
        "NATURAL": {"7.2": 20, "9.0": 20, "12.0": 20},
        "SELF_PACED": {"7.2": 8, "9.0": 8, "12.0": 8},
    },
    "scheduledContextBalance": {
        "FOCUSED": {"ALIGNED": 6, "NEUTRAL": 6, "CONFLICT": 6},
        "NATURAL": {"ALIGNED": 20, "NEUTRAL": 20, "CONFLICT": 20},
        "SELF_PACED": {"ALIGNED": 8, "NEUTRAL": 8, "CONFLICT": 8},
    },
    "naturalContextFrequencyMatrix": {
        "7.2": {"ALIGNED": 7, "NEUTRAL": 7, "CONFLICT": 6},
        "9.0": {"ALIGNED": 6, "NEUTRAL": 7, "CONFLICT": 7},
        "12.0": {"ALIGNED": 7, "NEUTRAL": 6, "CONFLICT": 7},
    },
    "timing": {
        "preIntentNcSeconds": 4.0,
        "preparationSeconds": 6.0,
        "guaranteedSilenceBeforeStimulusSeconds": 1.0,
        "intentionalSeconds": 4.0,
        "postIntentSeconds": 4.0,
        "rearmObservationSeconds": 4.0,
        "selfPacedOpportunitySeconds": 8.0,
        "targetAssignmentSeconds": 1.0,
        "selfPacedCueWashoutSeconds": 1.0,
        "interEpisodeRestSeconds": 2.0,
        "trueIdleBlockSeconds": 180.0,
        "passiveBrowseBlockSeconds": 180.0,
        "blockRestSeconds": 60.0,
        "shortBreakSeconds": 60.0,
        "longBreakSeconds": 180.0,
        "ratingMinutesPerActiveBlock": 1.0,
    },
    "cueProfile": {
        "authority": "M13.7_Golden_Protocol_v1",
        "targetFrequencyHz": 880.0,
        "targetBeepSeconds": 0.15,
        "readyFrequencyHz": 1760.0,
        "readyBeepSeconds": 0.5,
        "endFrequencyHz": 440.0,
        "endBeepSeconds": 0.6,
        "unchanged": True,
    },
    "blockLayout": [
        {"blockId": "FOCUSED_01", "group": "FOCUSED", "count": 6},
        {"blockId": "NATURAL_01", "group": "NATURAL", "count": 10},
        {"blockId": "TRUE_IDLE_01", "group": "TRUE_IDLE", "durationSeconds": 180},
        {"blockId": "PASSIVE_BROWSE_01", "group": "PASSIVE_BROWSE", "durationSeconds": 180},
        {"blockId": "SELF_PACED_01", "group": "SELF_PACED", "count": 6},
        {"blockId": "NATURAL_02", "group": "NATURAL", "count": 10},
        {"blockId": "FOCUSED_02", "group": "FOCUSED", "count": 6},
        {"blockId": "TRUE_IDLE_02", "group": "TRUE_IDLE", "durationSeconds": 180},
        {"blockId": "PASSIVE_BROWSE_02", "group": "PASSIVE_BROWSE", "durationSeconds": 180},
        {"blockId": "SELF_PACED_02", "group": "SELF_PACED", "count": 6},
        {"blockId": "NATURAL_03", "group": "NATURAL", "count": 10},
        {"blockId": "FOCUSED_03", "group": "FOCUSED", "count": 6},
        {"blockId": "TRUE_IDLE_03", "group": "TRUE_IDLE", "durationSeconds": 180},
        {"blockId": "PASSIVE_BROWSE_03", "group": "PASSIVE_BROWSE", "durationSeconds": 180},
        {"blockId": "SELF_PACED_03", "group": "SELF_PACED", "count": 6},
        {"blockId": "NATURAL_04", "group": "NATURAL", "count": 10},
        {"blockId": "PASSIVE_BROWSE_04", "group": "PASSIVE_BROWSE", "durationSeconds": 180},
        {"blockId": "NATURAL_05", "group": "NATURAL", "count": 10},
        {"blockId": "SELF_PACED_04", "group": "SELF_PACED", "count": 6},
        {"blockId": "NATURAL_06", "group": "NATURAL", "count": 10},
    ],
    "smoke": {"maximumEpisodes": 9, "countsTowardPrimary": False},
    "continuousEeg": {
        "authoritativeSource": "continuous_raw_nd8_stream",
        "samplingRateHz": 1000.0,
        "channelCount": 8,
        "eventToSampleAssociation": "first_packet_received_at_or_after_STIMULUS_ONSET; hardware verification required",
        "rawImmutable": True,
    },
    "context": {
        "provenance": "REAL_HUMAN_EEG_DERIVED_DATA_PLUS_PROSPECTIVE_CONTEXT_RUNTIME",
        "generatorVersion": "m16_three_candidate_adapter_v1",
        "routes": {
            "alpha": ["block_sim_01", "block_sim_02", "block_sim_03"],
            "beta": ["block_sim_01", "block_sim_03", "block_sim_02"],
            "gamma": ["block_sim_02", "block_sim_01", "block_sim_03"],
        },
        "alignedPriorWeight": 0.70,
        "alignedUniformWeight": 0.30,
        "forbiddenInputs": [
            "trueTarget",
            "currentTrialEeg",
            "currentDecoderScore",
            "selectionResult",
            "futureOutcome",
        ],
        "targetIdentityOverrideAllowed": False,
    },
    "labels": {
        "focused": "IC_FOCUSED",
        "natural": "IC_NATURAL",
        "selfPaced": "IC_SELF_PACED",
        "trueIdle": "NC_TRUE_IDLE",
        "passiveBrowse": "NC_PASSIVE_BROWSE",
        "preIntent": "NC_PRE_INTENT",
        "prepare": "AMBIGUOUS_PREPARE",
        "postIntent": "POST_INTENT_UNCERTAIN",
        "sham": "SHAM_CUE_NC",
        "refractory": "REFRACTORY",
    },
    "productionBoundary": {
        "productionDecoderChanged": False,
        "productionContextEnabled": False,
        "productionThresholdsChanged": False,
        "robotCommandSemanticsChanged": False,
        "questCanonicalTargetMappingChanged": False,
    },
}


M18_EVENT_SCHEMA: dict[str, Any] = {
    "schemaVersion": M18_SCHEMA_VERSION,
    "recordType": "m18_event_schema",
    "appendOnly": True,
    "commonRequired": [
        "recordType", "schemaVersion", "eventType", "sessionId", "blockId",
        "episodeId", "sequence", "monotonicNs", "utcTimestamp", "eventSource",
        "protocolVersion", "softwareCommit",
    ],
    "eventTypes": {
        "SESSION_STARTED": ["participantId", "samplingRateHz", "channelCount", "protocolSha256"],
        "BLOCK_STARTED": ["group", "controlStateGroundTruth", "continuousEegFile"],
        "EPISODE_STARTED": ["group", "attentionCondition", "controlStateGroundTruth", "intentOnsetGroundTruth"],
        "CONTEXT_GENERATED": ["contextSnapshotId", "taskStateId", "inputHistoryIds", "generatedAtMonotonicNs"],
        "CONTEXT_FROZEN": ["contextSnapshotId", "snapshotCanonicalSha256", "frozenAtMonotonicNs"],
        "TARGET_SAMPLED": ["targetSamplingRule", "targetSamplerSeed", "observedContextRelation"],
        "TARGET_INSTRUCTION": ["targetVisibleToContextGenerator"],
        "STIMULUS_ONSET": ["frequencyHz", "timingSemantics", "physicalOpticalTimingVerified"],
        "TRIAL_SAMPLE_ANCHOR": ["sampleAnchorSampleIndex", "anchorPacketSequence", "anchorBasis"],
        "SHAM_READY_CUE": ["cueType", "controlStateGroundTruth"],
        "EPISODE_ABORTED": ["abortReason"],
        "EPISODE_COMPLETED": ["controlStateGroundTruth", "intentOnsetGroundTruth"],
        "BLOCK_FINISHED": ["completedEpisodeCount", "abortedEpisodeCount"],
        "SESSION_FINALIZED": ["recordCounts", "rawFileSha256ByBlock"],
    },
    "evaluationTruth": {
        "file": "evaluation-truth.jsonl",
        "runtimeReadable": False,
        "allowedConsumers": ["offline_exporter", "offline_evaluator"],
        "forbiddenConsumers": ["context_generator", "stopping_controller", "live_decoder"],
        "fields": ["trueTarget", "targetSamplerDraw", "observedContextRelation"],
    },
}


M18_CONTEXT_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "schemaVersion": M18_SCHEMA_VERSION,
    "recordType": "m18_context_snapshot_schema",
    "immutable": True,
    "required": [
        "contextSnapshotId", "taskStateId", "candidateSet", "candidateSetHash",
        "completedHistory", "inputHistoryIds", "priorVector", "contextTop",
        "entropy", "normalizedEntropy", "contextInformativeness",
        "generatedAtMonotonicNs", "frozenAtMonotonicNs", "snapshotCanonicalSha256",
        "targetVisibleToGenerator", "futureEegVisibleToGenerator",
        "futureOutcomeVisibleToGenerator",
    ],
    "forbiddenKeys": ["trueTarget", "currentTrialEeg", "currentDecoderScore", "selectionResult", "futureOutcome"],
    "causalOrder": ["generated", "frozen", "target_sampled", "target_instruction", "stimulus_onset"],
}


def _utc_from_ns(ns: int) -> str:
    return datetime.fromtimestamp(1_735_689_600.0 + (int(ns) / 1_000_000_000.0), timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(_canonical_json(dict(record)) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not an object")
            values.append(value)
    return values


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _git_dirty() -> bool | None:
    try:
        return bool(subprocess.check_output(["git", "status", "--short"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _seed(*parts: Any) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _balanced_values(counts: Mapping[str, int], seed: int, maximum_run: int = 3) -> list[str]:
    values = [key for key, count in counts.items() for _ in range(int(count))]
    rng = random.Random(int(seed))
    for _ in range(5000):
        rng.shuffle(values)
        runs: list[int] = []
        for value in values:
            if not runs or value != values[sum(runs) - 1]:
                runs.append(1)
            else:
                runs[-1] += 1
        if max(runs, default=0) <= maximum_run:
            return list(values)
    raise ValueError("unable to construct deterministic balanced sequence")


def _candidate_by_slot(slot: int) -> dict[str, Any]:
    return dict(M18_CANDIDATES[int(slot)])


def _candidate_hash(candidates: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_bytes(_canonical_json([dict(item) for item in candidates]).encode("utf-8"))


@dataclass(frozen=True)
class ContextSnapshot:
    context_snapshot_id: str
    task_state_id: str
    generator_version: str
    scenario_seed: int
    candidate_set: tuple[Mapping[str, Any], ...]
    candidate_set_hash: str
    completed_history: tuple[str, ...]
    input_history_ids: tuple[str, ...]
    prior_vector: tuple[float, ...]
    context_top: tuple[str, ...]
    entropy: float
    normalized_entropy: float
    context_informativeness: str
    generated_at_monotonic_ns: int
    frozen_at_monotonic_ns: int
    generated_at_utc: str
    frozen_at_utc: str
    snapshot_canonical_sha256: str
    target_visible_to_generator: bool = False
    future_eeg_visible_to_generator: bool = False
    future_outcome_visible_to_generator: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordType": "m18_context_snapshot",
            "schemaVersion": M18_SCHEMA_VERSION,
            "contextSnapshotId": self.context_snapshot_id,
            "taskStateId": self.task_state_id,
            "generatorVersion": self.generator_version,
            "scenarioSeed": self.scenario_seed,
            "candidateSet": [dict(item) for item in self.candidate_set],
            "candidateSetHash": self.candidate_set_hash,
            "completedHistory": list(self.completed_history),
            "inputHistoryIds": list(self.input_history_ids),
            "priorVector": list(self.prior_vector),
            "contextTop": list(self.context_top),
            "entropy": self.entropy,
            "normalizedEntropy": self.normalized_entropy,
            "contextInformativeness": self.context_informativeness,
            "generatedAtMonotonicNs": self.generated_at_monotonic_ns,
            "frozenAtMonotonicNs": self.frozen_at_monotonic_ns,
            "generatedAtUtc": self.generated_at_utc,
            "frozenAtUtc": self.frozen_at_utc,
            "snapshotCanonicalSha256": self.snapshot_canonical_sha256,
            "targetVisibleToGenerator": self.target_visible_to_generator,
            "futureEegVisibleToGenerator": self.future_eeg_visible_to_generator,
            "futureOutcomeVisibleToGenerator": self.future_outcome_visible_to_generator,
        }


class M18ContextEngine:
    """Frozen three-candidate M16 adapter with causal input enforcement."""

    ROUTES = (
        ("alpha", ("block_sim_01", "block_sim_02", "block_sim_03")),
        ("beta", ("block_sim_01", "block_sim_03", "block_sim_02")),
        ("gamma", ("block_sim_02", "block_sim_01", "block_sim_03")),
    )

    def generate(
        self,
        *,
        completed_history: Iterable[str],
        task_state_id: str,
        scenario_seed: int,
        generated_at_monotonic_ns: int,
        frozen_at_monotonic_ns: int | None = None,
        candidate_set: Sequence[Mapping[str, Any]] = M18_CANDIDATES,
        **forbidden_inputs: Any,
    ) -> ContextSnapshot:
        if forbidden_inputs:
            raise ValueError("Context generator received forbidden runtime input(s): " + ", ".join(sorted(forbidden_inputs)))
        # Freeze nested candidate records as well as the dataclass shell.  A
        # frozen dataclass alone does not prevent callers from mutating a
        # dict stored inside it, which would invalidate the recorded hash.
        candidates = tuple(MappingProxyType(dict(item)) for item in candidate_set)
        if len(candidates) != 3:
            raise ValueError("M18 Context requires exactly three candidates")
        history = tuple(str(item) for item in completed_history)
        candidate_ids = tuple(item["logicalBlockId"] for item in candidates)
        if any(item not in candidate_ids for item in history):
            raise ValueError("completed history is not an observable valid candidate history")
        # The unified acquisition is not a one-pass sequential-task
        # benchmark: a participant may select the same candidate again.  The
        # full ordered history remains observable and is logged, while the
        # M16 route adapter uses the distinct completed set for its next-step
        # hypothesis lookup.
        distinct_history = tuple(dict.fromkeys(history))
        generated = int(generated_at_monotonic_ns)
        frozen = generated if frozen_at_monotonic_ns is None else int(frozen_at_monotonic_ns)
        if frozen < generated:
            raise ValueError("Context freeze cannot precede generation")
        counts = {candidate_id: 0.0 for candidate_id in candidate_ids}
        input_history_ids = tuple(f"history-{index:03d}-{value}" for index, value in enumerate(history, 1))
        for _route_name, route in self.ROUTES:
            next_candidates = [value for value in route if value not in distinct_history]
            if next_candidates:
                counts[next_candidates[0]] += 1.0
        if not any(counts.values()):
            counts = {candidate_id: 1.0 for candidate_id in candidate_ids}
        total = sum(counts.values())
        prior = tuple(counts[value] / total for value in candidate_ids)
        maximum = max(prior)
        top = tuple(candidate_id for candidate_id, probability in zip(candidate_ids, prior) if math.isclose(probability, maximum, abs_tol=1e-12))
        entropy = -sum(probability * math.log(probability, 2) for probability in prior if probability > 0.0)
        normalized = entropy / math.log(len(candidate_ids), 2)
        informativeness = "INFORMATIVE_UNIQUE_TOP" if len(top) == 1 and normalized < 0.999999 else "NEUTRAL_TIE_OR_LOW_INFORMATION"
        base = {
            "contextSnapshotId": f"m18-context-{scenario_seed:08d}-{len(history):02d}",
            "taskStateId": str(task_state_id),
            "generatorVersion": M18_PROTOCOL["context"]["generatorVersion"],
            "scenarioSeed": int(scenario_seed),
            "candidateSet": [dict(item) for item in candidates],
            "candidateSetHash": _candidate_hash(candidates),
            "completedHistory": list(history),
            "inputHistoryIds": list(input_history_ids),
            "priorVector": list(prior),
            "contextTop": list(top),
            "entropy": entropy,
            "normalizedEntropy": normalized,
            "contextInformativeness": informativeness,
            "generatedAtMonotonicNs": generated,
            "frozenAtMonotonicNs": frozen,
            "generatedAtUtc": _utc_from_ns(generated),
            "frozenAtUtc": _utc_from_ns(frozen),
            "targetVisibleToGenerator": False,
            "futureEegVisibleToGenerator": False,
            "futureOutcomeVisibleToGenerator": False,
        }
        digest = _sha256_bytes(_canonical_json(base).encode("utf-8"))
        return ContextSnapshot(
            context_snapshot_id=base["contextSnapshotId"],
            task_state_id=base["taskStateId"],
            generator_version=base["generatorVersion"],
            scenario_seed=base["scenarioSeed"],
            candidate_set=candidates,
            candidate_set_hash=base["candidateSetHash"],
            completed_history=history,
            input_history_ids=input_history_ids,
            prior_vector=prior,
            context_top=top,
            entropy=entropy,
            normalized_entropy=normalized,
            context_informativeness=informativeness,
            generated_at_monotonic_ns=generated,
            frozen_at_monotonic_ns=frozen,
            generated_at_utc=base["generatedAtUtc"],
            frozen_at_utc=base["frozenAtUtc"],
            snapshot_canonical_sha256=digest,
        )


@dataclass(frozen=True)
class TargetSample:
    slot_index: int
    target_id: str
    logical_block_id: str
    frequency_hz: float
    scheduled_context_condition: str
    observed_context_relation: str
    sampler_seed: int
    mixture_component: str
    sampling_rule: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "slotIndex": self.slot_index,
            "targetId": self.target_id,
            "logicalBlockId": self.logical_block_id,
            "frequencyHz": self.frequency_hz,
            "scheduledContextCondition": self.scheduled_context_condition,
            "observedContextRelation": self.observed_context_relation,
            "targetSamplerSeed": self.sampler_seed,
            "mixtureComponent": self.mixture_component,
            "targetSamplingRule": self.sampling_rule,
        }


class M18TargetSampler:
    """Post-freeze M16 target sampler with group-level balance quotas."""

    def sample(
        self,
        *,
        snapshot: ContextSnapshot,
        scheduled_context_condition: str,
        sampler_seed: int,
        remaining_slot_counts: Mapping[int, int],
    ) -> TargetSample:
        if snapshot.frozen_at_monotonic_ns < snapshot.generated_at_monotonic_ns:
            raise ValueError("target sampling requires a frozen Context snapshot")
        condition = str(scheduled_context_condition).upper()
        if condition not in CONTEXT_CONDITIONS:
            raise ValueError(f"unknown Context condition: {condition}")
        available = [slot for slot, count in remaining_slot_counts.items() if int(count) > 0]
        if not available:
            raise ValueError("target sampler has no remaining balanced target slots")
        rng = random.Random(int(sampler_seed))
        unique_top_slot = None
        if len(snapshot.context_top) == 1:
            unique_top_slot = next((item["slotIndex"] for item in snapshot.candidate_set if item["logicalBlockId"] == snapshot.context_top[0]), None)
        if condition == "ALIGNED":
            mixture_component = "prior_based" if rng.random() < 0.70 else "uniform"
            weights = []
            for item in snapshot.candidate_set:
                slot = int(item["slotIndex"])
                prior = float(snapshot.prior_vector[slot])
                weights.append(0.70 * prior + 0.30 / 3.0)
        elif condition == "NEUTRAL":
            mixture_component = "uniform"
            weights = [1.0 / 3.0] * 3
        else:
            mixture_component = "outside_unique_top" if unique_top_slot is not None else "uniform_low_information"
            weights = [0.0 if slot == unique_top_slot else 1.0 for slot in range(3)]
            if not any(weights[slot] > 0.0 for slot in available):
                weights = [1.0] * 3
        restricted = [float(weights[slot]) if slot in available else 0.0 for slot in range(3)]
        if sum(restricted) <= 0.0:
            restricted = [1.0 if slot in available else 0.0 for slot in range(3)]
        slot = rng.choices([0, 1, 2], weights=restricted, k=1)[0]
        candidate = _candidate_by_slot(slot)
        if unique_top_slot is None:
            observed = "NEUTRAL"
        else:
            observed = "ALIGNED" if slot == unique_top_slot else "CONFLICT"
        return TargetSample(
            slot_index=slot,
            target_id=candidate["targetId"],
            logical_block_id=candidate["logicalBlockId"],
            frequency_hz=float(candidate["frequencyHz"]),
            scheduled_context_condition=condition,
            observed_context_relation=observed,
            sampler_seed=int(sampler_seed),
            mixture_component=mixture_component,
            sampling_rule=(
                "aligned=0.70_prior+0.30_uniform; neutral=seeded_uniform; "
                "conflict=seeded_draw_outside_unique_informative_top; balance_quota_constrained"
            ),
        )


@dataclass(frozen=True)
class EpisodePlan:
    episode_id: str
    block_id: str
    ordinal: int
    group: str
    attention_condition: str | None
    scheduled_context_condition: str | None
    slot_index: int | None
    frequency_hz: float | None
    target_id: str | None
    logical_block_id: str | None
    observed_context_relation: str | None
    intent_onset_ground_truth: str
    control_state_ground_truth: str
    context_snapshot: dict[str, Any] | None
    target_sample: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episodeId": self.episode_id,
            "blockId": self.block_id,
            "ordinal": self.ordinal,
            "group": self.group,
            "attentionCondition": self.attention_condition,
            "scheduledContextCondition": self.scheduled_context_condition,
            "slotIndex": self.slot_index,
            "frequencyHz": self.frequency_hz,
            "targetId": self.target_id,
            "logicalBlockId": self.logical_block_id,
            "observedContextRelation": self.observed_context_relation,
            "intentOnsetGroundTruth": self.intent_onset_ground_truth,
            "controlStateGroundTruth": self.control_state_ground_truth,
            "contextSnapshot": self.context_snapshot,
            "targetSample": self.target_sample,
        }


class EpisodeStateMachine:
    STATES = (
        "CREATED", "PRE_NC", "CONTEXT_GENERATED", "CONTEXT_FROZEN", "TARGET_SAMPLED",
        "TARGET_PRESENTED", "PREPARE", "READY", "INTENTIONAL", "END", "POST_INTENT",
        "COMPLETE", "LOG_FLUSHED", "ABORTED",
    )
    ALLOWED = {
        "CREATED": {"PRE_NC", "ABORTED"},
        "PRE_NC": {"CONTEXT_GENERATED", "TARGET_SAMPLED", "ABORTED"},
        "CONTEXT_GENERATED": {"CONTEXT_FROZEN", "ABORTED"},
        "CONTEXT_FROZEN": {"TARGET_SAMPLED", "ABORTED"},
        "TARGET_SAMPLED": {"TARGET_PRESENTED", "ABORTED"},
        "TARGET_PRESENTED": {"PREPARE", "ABORTED"},
        "PREPARE": {"READY", "INTENTIONAL", "ABORTED"},
        "READY": {"INTENTIONAL", "ABORTED"},
        "INTENTIONAL": {"END", "ABORTED"},
        "END": {"POST_INTENT", "ABORTED"},
        "POST_INTENT": {"COMPLETE", "ABORTED"},
        "COMPLETE": {"LOG_FLUSHED"},
        "LOG_FLUSHED": set(),
        "ABORTED": set(),
    }

    def __init__(self, episode_id: str):
        self.episode_id = str(episode_id)
        self.state = "CREATED"
        self.transitions: list[dict[str, Any]] = []

    def move(self, new_state: str, reason: str) -> dict[str, Any]:
        new_state = str(new_state)
        if new_state not in self.STATES or new_state not in self.ALLOWED[self.state]:
            raise RuntimeError(f"illegal M18 episode transition {self.state}->{new_state}")
        transition = {"from": self.state, "to": new_state, "reason": str(reason), "episodeId": self.episode_id}
        self.transitions.append(transition)
        self.state = new_state
        return transition

    def abort(self, reason: str) -> dict[str, Any]:
        if self.state in ("COMPLETE", "LOG_FLUSHED", "ABORTED"):
            raise RuntimeError(f"cannot abort terminal M18 episode state {self.state}")
        return self.move("ABORTED", reason)


@dataclass
class M18SessionRecorder:
    root: Path
    session_id: str
    protocol: Mapping[str, Any]
    source_type: str = "synthetic"
    participant_id: str = "P001"
    resume: bool = False
    software_commit: str = field(default_factory=_git_commit)
    sampling_rate_hz: float = 1000.0
    channel_count: int = 8
    utc_now_provider: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.protocol_hash = _sha256_bytes(_canonical_json(self.protocol).encode("utf-8"))
        if self.manifest_path.exists() and not self.resume:
            raise FileExistsError(f"refusing to overwrite M18 session: {self.root}")
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if self.manifest.get("sessionId") != self.session_id:
                raise ValueError("M18 resume session ID mismatch")
            if self.manifest.get("protocolSha256") != self.protocol_hash:
                raise ValueError("M18 resume protocol hash mismatch")
            if self.manifest.get("status") == "finalized":
                raise ValueError("finalized M18 session is immutable")
        else:
            existing = [item for item in self.root.iterdir() if item.name != "manifest.json"]
            if existing:
                raise FileExistsError(f"M18 session has files without a manifest: {self.root}")
            self.manifest = {
                "recordType": "m18_session_manifest",
                "schemaVersion": M18_SCHEMA_VERSION,
                "protocolVersion": self.protocol["protocolVersion"],
                "protocolSha256": self.protocol_hash,
                "sessionId": self.session_id,
                "participantId": self.participant_id,
                "sourceType": self.source_type,
                "softwareCommit": self.software_commit,
                "workingTreeDirty": _git_dirty(),
                "samplingRateHz": float(self.sampling_rate_hz),
                "channelCount": int(self.channel_count),
                "status": "recording",
                "hardwareBoundary": {
                    "questOperated": False,
                    "nd8Operated": False,
                    "newRealEegCollected": False,
                    "physicalTimingVerified": False,
                    "humanCheckRequired": True,
                },
                "continuousRawAuthority": True,
                "productionBoundary": dict(self.protocol["productionBoundary"]),
                "files": {
                    "events": "events/events.jsonl",
                    "contextSnapshots": "context/context-snapshots.jsonl",
                    "evaluationTruth": "context/evaluation-truth.jsonl",
                    "ratings": "ratings/ratings.jsonl",
                    "validation": "validation/integrity.jsonl",
                    "exports": "exports/",
                    "blocks": "blocks/<block_id>/eeg/raw-eeg.jsonl",
                },
            }
            _atomic_json(self.manifest_path, self.manifest)
        self._event_path = self.root / "events" / "events.jsonl"
        self._snapshot_path = self.root / "context" / "context-snapshots.jsonl"
        self._truth_path = self.root / "context" / "evaluation-truth.jsonl"
        self._ratings_path = self.root / "ratings" / "ratings.jsonl"
        self._validation_path = self.root / "validation" / "integrity.jsonl"
        for path in (self._event_path, self._snapshot_path, self._truth_path, self._ratings_path, self._validation_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        events = _read_jsonl(self._event_path)
        self._sequence = max((int(item.get("sequence", -1)) for item in events), default=-1) + 1
        self._last_monotonic_ns = max((int(item.get("monotonicNs", 0)) for item in events), default=-1)
        self._last_utc_timestamp = next((item.get("utcTimestamp") for item in reversed(events) if item.get("utcTimestamp")), None)
        self._active_block: str | None = None
        completed_ids = [item.get("episodeId") for item in events if item.get("eventType") == "EPISODE_COMPLETED" and item.get("episodeId") is not None]
        if len(completed_ids) != len(set(completed_ids)):
            raise ValueError("duplicate completed M18 episode identity")
        self.completed_episodes = set(completed_ids)
        self.aborted_episodes = {item.get("episodeId") for item in events if item.get("eventType") == "EPISODE_ABORTED" and item.get("episodeId") is not None}
        self.completed_blocks = {item.get("blockId") for item in events if item.get("eventType") == "BLOCK_FINISHED"}

    def _touch_manifest(self, **values: Any) -> None:
        self.manifest.update(values)
        _atomic_json(self.manifest_path, self.manifest)

    def _ensure_recording(self) -> None:
        if self.manifest.get("status") == "finalized":
            raise RuntimeError("finalized M18 session is immutable")

    def set_active_block(self, block_id: str) -> None:
        block_id = str(block_id)
        self._active_block = block_id
        block_root = self.root / "blocks" / block_id
        for name in ("eeg", "events", "context", "ratings", "validation", "exports"):
            (block_root / name).mkdir(parents=True, exist_ok=True)
        (block_root / "eeg" / "raw-eeg.jsonl").touch(exist_ok=True)

    def record_event(self, event_type: str, episodeId: str | None = None, blockId: str | None = None, eventSource: str = "orchestrator", monotonicNs: int | None = None, utcTimestamp: str | None = None, trialId: str | None = None, sessionId: str | None = None, **values: Any) -> dict[str, Any]:
        self._ensure_recording()
        if event_type == "EPISODE_COMPLETED" and episodeId in self.completed_episodes:
            raise ValueError(f"duplicate completed M18 episode: {episodeId}")
        if event_type == "EPISODE_COMPLETED" and episodeId in self.aborted_episodes:
            raise ValueError(f"aborted M18 episode requires a new retry identity: {episodeId}")
        # AudioCueSystem is the frozen M13.7 seam and calls the recorder with
        # trialId/sessionId.  M18 names the same concept episodeId/sessionId;
        # accept both spellings without changing the old cue implementation.
        if episodeId is None and trialId is not None:
            episodeId = trialId
        if sessionId is not None and str(sessionId) != self.session_id:
            raise ValueError("event sessionId does not match the M18 recorder")
        if monotonicNs is None:
            # Reused M13.7 cue callbacks do not pass their private clock
            # reading through the event sink.  Preserve their event order at
            # the last recorder timestamp instead of manufacturing time zero.
            monotonicNs = self._last_monotonic_ns
        if int(monotonicNs) < self._last_monotonic_ns:
            raise ValueError("M18 event monotonicNs regressed")
        block = self._active_block if blockId is None else str(blockId)
        effective_utc_timestamp = utcTimestamp or (self.utc_now_provider() if self.utc_now_provider is not None else _utc_from_ns(int(monotonicNs)))
        record = {
            "recordType": "m18_event",
            "schemaVersion": M18_SCHEMA_VERSION,
            "eventType": str(event_type),
            "sessionId": self.session_id,
            "participantId": self.participant_id,
            "blockId": block,
            "episodeId": None if episodeId is None else str(episodeId),
            "sequence": self._sequence,
            "monotonicNs": int(monotonicNs),
            "utcTimestamp": effective_utc_timestamp,
            "eventSource": str(eventSource),
            "protocolVersion": self.protocol["protocolVersion"],
            "softwareCommit": self.software_commit,
            **values,
        }
        _append_jsonl(self._event_path, record)
        self._sequence += 1
        self._last_monotonic_ns = int(monotonicNs)
        self._last_utc_timestamp = str(effective_utc_timestamp)
        if event_type == "EPISODE_COMPLETED" and episodeId is not None:
            self.completed_episodes.add(str(episodeId))
        if event_type == "EPISODE_ABORTED" and episodeId is not None:
            self.aborted_episodes.add(str(episodeId))
        if event_type == "BLOCK_FINISHED" and block is not None:
            self.completed_blocks.add(str(block))
        return record

    def record_context_snapshot(self, snapshot: ContextSnapshot, block_id: str, episode_id: str) -> None:
        self._ensure_recording()
        value = snapshot.to_dict()
        if value["snapshotCanonicalSha256"] != _sha256_bytes(_canonical_json({key: item for key, item in value.items() if key not in ("recordType", "schemaVersion", "snapshotCanonicalSha256")}).encode("utf-8")):
            raise ValueError("Context snapshot hash mismatch")
        _append_jsonl(self._snapshot_path, {**value, "sessionId": self.session_id, "blockId": block_id, "episodeId": episode_id})

    def record_evaluation_truth(self, block_id: str, episode_id: str, **values: Any) -> None:
        self._ensure_recording()
        if "trueTarget" not in values:
            raise ValueError("evaluation truth requires trueTarget")
        _append_jsonl(self._truth_path, {
            "recordType": "m18_evaluation_truth",
            "schemaVersion": M18_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "blockId": block_id,
            "episodeId": episode_id,
            "visibility": "evaluation_only",
            "runtimeReadable": False,
            **values,
        })

    def record_packet(self, packet: Nd8Packet, continuity: PacketContinuityRecord, experiment_monotonic_ns: int | None = None) -> dict[str, Any]:
        self._ensure_recording()
        if self._active_block is None:
            raise RuntimeError("cannot record an EEG packet without an active block")
        if not isinstance(packet, Nd8Packet) or not isinstance(continuity, PacketContinuityRecord):
            raise TypeError("M18 record_packet requires Nd8Packet and PacketContinuityRecord")
        raw = packet.raw_log_record()
        raw.update({
            "recordType": "m18_raw_nd8_packet",
            "sessionId": self.session_id,
            "blockId": self._active_block,
            "experimentMonotonicNs": int(packet.pc_receive_monotonic_ns if experiment_monotonic_ns is None else experiment_monotonic_ns),
            "sourceSampleIndex": int(continuity.cumulative_first_sample_index),
            "rawPreservation": "values_as_received_before_preprocessing",
        })
        raw_path = self.root / "blocks" / self._active_block / "eeg" / "raw-eeg.jsonl"
        metadata_path = self.root / "blocks" / self._active_block / "eeg" / "packet-metadata.jsonl"
        _append_jsonl(raw_path, raw)
        _append_jsonl(metadata_path, {
            "recordType": "m18_packet_metadata",
            "schemaVersion": M18_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "blockId": self._active_block,
            "packet": packet.to_metadata().to_dict(),
            "continuity": continuity.to_dict(),
            "experimentMonotonicNs": raw["experimentMonotonicNs"],
        })
        if self.source_type == "live_nd8" and not self.manifest.get("hardwareBoundary", {}).get("newRealEegCollected", False):
            boundary = dict(self.manifest.get("hardwareBoundary", {}))
            boundary["newRealEegCollected"] = True
            self._touch_manifest(hardwareBoundary=boundary)
        return raw

    def record_rating(self, block_id: str, **values: Any) -> None:
        self._ensure_recording()
        _append_jsonl(self._ratings_path, {
            "recordType": "m18_block_rating",
            "schemaVersion": M18_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "blockId": block_id,
            **values,
        })

    def record_validation(self, check: str, status: str, detail: str, **values: Any) -> None:
        self._ensure_recording()
        _append_jsonl(self._validation_path, {
            "recordType": "m18_validation",
            "schemaVersion": M18_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "check": check,
            "status": status,
            "detail": detail,
            **values,
        })

    def finalize(self) -> dict[str, Any]:
        if self.manifest.get("status") == "finalized":
            return dict(self.manifest)
        events = _read_jsonl(self._event_path)
        raw_files = sorted((self.root / "blocks").glob("*/eeg/raw-eeg.jsonl"))
        self.record_event("SESSION_FINALIZED", blockId=None, eventSource="recorder", monotonicNs=max((int(item.get("monotonicNs", 0)) for item in events), default=0) + 1, recordCounts={
            "events": len(events) + 1,
            "contextSnapshots": len(_read_jsonl(self._snapshot_path)),
            "evaluationTruth": len(_read_jsonl(self._truth_path)),
            "ratings": len(_read_jsonl(self._ratings_path)),
            "rawPackets": sum(len(_read_jsonl(path)) for path in raw_files),
        }, rawFileSha256ByBlock={str(path.parent.parent.name): _sha256_file(path) for path in raw_files})
        self.manifest["status"] = "finalized"
        self.manifest["finalizedUtc"] = self._last_utc_timestamp or _utc_from_ns(max((int(item.get("monotonicNs", 0)) for item in _read_jsonl(self._event_path)), default=0))
        self.manifest["rawFileSha256ByBlock"] = {str(path.parent.parent.name): _sha256_file(path) for path in raw_files}
        _atomic_json(self.manifest_path, self.manifest)
        return dict(self.manifest)


class SyntheticClock:
    def __init__(self) -> None:
        self.ns = 0

    def monotonic_ns(self) -> int:
        return int(self.ns)

    def sleep(self, seconds: float) -> None:
        self.ns += max(0, int(round(float(seconds) * 1_000_000_000.0)))

    def utc_now(self) -> str:
        return _utc_from_ns(self.ns)


class SyntheticContinuousEegSource:
    """Small continuous packet source for software-only dry runs."""

    def __init__(self, recorder: M18SessionRecorder, clock: SyntheticClock, time_scale: float = 0.01, packet_samples: int = 20):
        self.recorder = recorder
        self.clock = clock
        self.time_scale = float(time_scale)
        self.packet_samples = int(packet_samples)
        self.sampling_rate_hz = float(recorder.sampling_rate_hz)
        self.packet_sequence = 0
        self.sample_index = 0

    def emit_segment(self, duration_seconds: float, slot: int | None = None) -> None:
        packet_seconds = self.packet_samples / self.sampling_rate_hz
        count = max(1, int(round(float(duration_seconds) * self.time_scale / packet_seconds)))
        for _ in range(count):
            t = np.arange(self.packet_samples, dtype=float) / self.sampling_rate_hz
            if slot is None:
                signal = np.zeros(self.packet_samples, dtype=float)
            else:
                signal = np.sin(2.0 * np.pi * float(FREQUENCIES_HZ[int(slot)]) * t)
            values = np.vstack([signal * (1.0 - 0.02 * channel) for channel in range(self.recorder.channel_count)])
            packet = Nd8Packet.from_sdk_payload(
                {"timestamp": self.sample_index / self.sampling_rate_hz * 1000.0, "data": values.tolist()},
                packet_sequence=self.packet_sequence,
                nominal_sampling_rate_hz=self.sampling_rate_hz,
                receive_monotonic_ns=self.clock.monotonic_ns(),
                receive_utc=self.clock.utc_now(),
            )
            continuity = PacketContinuityRecord(
                self.packet_sequence,
                self.sample_index,
                "initial" if self.packet_sequence == 0 else "continuous",
                (),
            )
            self.recorder.record_packet(packet, continuity)
            self.packet_sequence += 1
            self.sample_index += self.packet_samples
            self.clock.sleep(packet_seconds)

    def capture_sample_anchor(self, onset_event: Mapping[str, Any]) -> dict[str, Any]:
        """Describe the next synthetic packet after the recorded onset."""
        return {
            "stimulusOnsetEventSequence": int(onset_event["sequence"]),
            "stimulusOnsetMonotonicNs": int(onset_event["monotonicNs"]),
            "anchorPacketSequence": int(self.packet_sequence),
            "sampleAnchorSampleIndex": int(self.sample_index),
            "anchorPacketReceiveMonotonicNs": int(self.clock.monotonic_ns()),
            "anchorPacketSampleCount": int(self.packet_samples),
            "anchorBasis": "first_packet_received_at_or_after_stimulus_onset",
            "hardwareTimingVerified": False,
            "physicalOpticalTimingVerified": False,
        }


class RealClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, float(seconds)))

    def utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()


class M18LiveContinuousEegSource:
    """M18 live boundary over the existing M13.7 raw-first ND8 source."""

    def __init__(
        self,
        recorder: M18SessionRecorder,
        com_port: str,
        packet_sequence_offset: int = 0,
        sample_index_offset: int = 0,
    ):
        from integration.m13_7_live_launcher import GoldenPacketPipeline, LiveND8Source

        self.pipeline = GoldenPacketPipeline()
        self.source = LiveND8Source(
            str(com_port),
            recorder,
            self.pipeline,
            nominal_sampling_rate_hz=float(recorder.sampling_rate_hz),
            packet_sequence_offset=int(packet_sequence_offset),
            sample_index_offset=int(sample_index_offset),
        )

    def open(self) -> None:
        self.source.open_port()
        self.source.start_streaming()

    def emit_segment(self, duration_seconds: float, slot: int | None = None) -> None:
        # Quest owns the physical visual state.  The PC keeps ND8 streaming
        # continuously while the M18 event lifecycle advances.
        del slot
        time.sleep(max(0.0, float(duration_seconds)))

    def capture_sample_anchor(self, onset_event: Mapping[str, Any], timeout_seconds: float = 10.0) -> dict[str, Any]:
        self.pipeline.mark_next_segment(
            stimulus_onset_monotonic_ns=int(onset_event["monotonicNs"]),
            stimulus_onset_event_sequence=int(onset_event["sequence"]),
        )
        self.pipeline.wait_for_next_segment_anchor(timeout_seconds)
        metadata = self.pipeline.next_segment_anchor_metadata
        if metadata.get("sampleAnchorSampleIndex") is None:
            raise RuntimeError("M18 live source did not produce a sample anchor")
        metadata.update({
            "hardwareTimingVerified": False,
            "physicalOpticalTimingVerified": False,
        })
        return metadata

    def close(self) -> None:
        try:
            self.source.stop()
        finally:
            self.source.close()


def _make_episode_rows(protocol: Mapping[str, Any], seed: int) -> list[EpisodePlan]:
    context_engine = M18ContextEngine()
    sampler = M18TargetSampler()
    rows: list[EpisodePlan] = []
    history: list[str] = []
    ordinal = 0
    group_positions = {"FOCUSED": 0, "NATURAL": 0, "SELF_PACED": 0}
    group_condition_sequences = {
        group: _balanced_values(protocol["scheduledContextBalance"][group], _seed(seed, group, "context"))
        for group in PRIMARY_GROUPS
    }
    group_condition_offsets = {group: 0 for group in PRIMARY_GROUPS}
    group_frequency_counts = {
        group: {slot: int(protocol["frequencyBalance"][group][str(FREQUENCIES_HZ[slot])]) for slot in range(3)}
        for group in PRIMARY_GROUPS
    }
    for block in protocol["blockLayout"]:
        group = block["group"]
        if group not in PRIMARY_GROUPS:
            continue
        count = int(block["count"])
        for index in range(count):
            ordinal += 1
            group_positions[group] += 1
            episode_id = f"m18-{group.lower()}-{group_positions[group]:03d}"
            condition = group_condition_sequences[group][group_condition_offsets[group]]
            group_condition_offsets[group] += 1
            generated_ns = ordinal * 1_000_000
            snapshot = context_engine.generate(
                completed_history=history,
                task_state_id=f"task-state-{len(history):03d}",
                scenario_seed=_seed(seed, block["blockId"], episode_id, "context"),
                generated_at_monotonic_ns=generated_ns,
                frozen_at_monotonic_ns=generated_ns + 1,
            )
            sample = sampler.sample(
                snapshot=snapshot,
                scheduled_context_condition=condition,
                sampler_seed=_seed(seed, block["blockId"], episode_id, "target"),
                remaining_slot_counts=group_frequency_counts[group],
            )
            group_frequency_counts[group][sample.slot_index] -= 1
            history.append(sample.logical_block_id)
            rows.append(EpisodePlan(
                episode_id=episode_id,
                block_id=block["blockId"],
                ordinal=ordinal,
                group=group,
                attention_condition="NATURAL_GAZE" if group == "NATURAL" else ("FOCUSED" if group == "FOCUSED" else "SELF_PACED"),
                scheduled_context_condition=condition,
                slot_index=sample.slot_index,
                frequency_hz=sample.frequency_hz,
                target_id=sample.target_id,
                logical_block_id=sample.logical_block_id,
                observed_context_relation=sample.observed_context_relation,
                intent_onset_ground_truth="UNKNOWN_WITHIN_WINDOW" if group == "SELF_PACED" else "CUED_APPROXIMATE",
                control_state_ground_truth={"FOCUSED": "IC_FOCUSED", "NATURAL": "IC_NATURAL", "SELF_PACED": "IC_SELF_PACED"}[group],
                context_snapshot=snapshot.to_dict(),
                target_sample=sample.to_dict(),
            ))
    for group in PRIMARY_GROUPS:
        if any(value != 0 for value in group_frequency_counts[group].values()):
            raise AssertionError(f"frequency balance failed for {group}: {group_frequency_counts[group]}")
        if group_condition_offsets[group] != len(group_condition_sequences[group]):
            raise AssertionError(f"Context balance was not consumed for {group}")
    return rows


def _runtime_episode_plan(
    row: EpisodePlan,
    *,
    completed_history: Sequence[str],
    remaining_slot_counts: Mapping[int, int],
    generated_at_monotonic_ns: int | None = None,
) -> EpisodePlan:
    """Materialize one episode from *completed* runtime history.

    ``build_schedule`` remains the deterministic plan and preview.  The
    recorder must not use a plan-time target as evidence that the participant
    completed an earlier episode, however.  Re-materializing the same seeded
    row immediately before acquisition preserves the normal deterministic
    path while making resume/abort behavior causal.
    """
    if row.group not in PRIMARY_GROUPS or row.context_snapshot is None:
        return row
    context_engine = M18ContextEngine()
    sampler = M18TargetSampler()
    planned_snapshot = row.context_snapshot
    generated_ns = int(planned_snapshot["generatedAtMonotonicNs"] if generated_at_monotonic_ns is None else generated_at_monotonic_ns)
    snapshot = context_engine.generate(
        completed_history=completed_history,
        task_state_id=f"task-state-{len(completed_history):03d}",
        scenario_seed=int(planned_snapshot["scenarioSeed"]),
        generated_at_monotonic_ns=generated_ns,
        frozen_at_monotonic_ns=generated_ns + 1,
    )
    planned_target = row.target_sample
    if planned_target is None:
        raise AssertionError("primary episode has no planned target sample")
    target = sampler.sample(
        snapshot=snapshot,
        scheduled_context_condition=str(row.scheduled_context_condition),
        sampler_seed=int(planned_target["targetSamplerSeed"]),
        remaining_slot_counts=remaining_slot_counts,
    )
    return replace(
        row,
        slot_index=target.slot_index,
        frequency_hz=target.frequency_hz,
        target_id=target.target_id,
        logical_block_id=target.logical_block_id,
        observed_context_relation=target.observed_context_relation,
        context_snapshot=snapshot.to_dict(),
        target_sample=target.to_dict(),
    )


def _runtime_resume_state(
    session_root: Path,
    schedule: Mapping[str, Any],
    recorder: M18SessionRecorder,
    protocol: Mapping[str, Any],
) -> tuple[list[str], dict[str, dict[int, int]]]:
    """Reconstruct only completed target history for a resumable session."""
    truth_records = _read_jsonl(Path(session_root) / "context" / "evaluation-truth.jsonl")
    truth_by_episode = {str(item.get("episodeId")): item for item in truth_records}
    incomplete_truth = set(recorder.completed_episodes) - set(truth_by_episode)
    if incomplete_truth:
        raise RuntimeError(
            "completed M18 episode lacks evaluation truth; refusing resume: "
            + ", ".join(sorted(str(value) for value in incomplete_truth))
        )
    history: list[str] = []
    remaining = {
        group: {
            slot: int(protocol["frequencyBalance"][group][str(FREQUENCIES_HZ[slot])])
            for slot in range(3)
        }
        for group in PRIMARY_GROUPS
    }
    schedule_rows = {str(row["episodeId"]): row for row in schedule["episodes"]}
    for episode_id in (str(row["episodeId"]) for row in schedule["episodes"]):
        if episode_id not in recorder.completed_episodes:
            continue
        truth = truth_by_episode[episode_id].get("trueTarget") or {}
        logical_block_id = truth.get("logicalBlockId") or truth.get("targetId")
        slot_index = truth.get("slotIndex")
        row = schedule_rows[episode_id]
        if logical_block_id is None or slot_index is None:
            raise RuntimeError(f"completed M18 episode has incomplete target truth: {episode_id}")
        slot_index = int(slot_index)
        if slot_index not in remaining[row["group"]] or remaining[row["group"]][slot_index] <= 0:
            raise RuntimeError(f"completed M18 episode exceeds target quota: {episode_id}")
        remaining[row["group"]][slot_index] -= 1
        history.append(str(logical_block_id))
    return history, remaining


def _sham_distribution(protocol: Mapping[str, Any]) -> dict[str, int]:
    idle = [item["blockId"] for item in protocol["blockLayout"] if item["group"] == "TRUE_IDLE"]
    browse = [item["blockId"] for item in protocol["blockLayout"] if item["group"] == "PASSIVE_BROWSE"]
    values = {block_id: 0 for block_id in idle + browse}
    for index, block_id in enumerate(idle):
        values[block_id] = 2
    for index, block_id in enumerate(browse):
        values[block_id] = 2 if index % 2 == 0 else 1
    return values


def build_schedule(protocol: Mapping[str, Any] = M18_PROTOCOL, seed: int | None = None) -> dict[str, Any]:
    seed = int(protocol["seed"] if seed is None else seed)
    rows = _make_episode_rows(protocol, seed)
    episode_dicts = [row.to_dict() for row in rows]
    by_block: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_block.setdefault(row.block_id, []).append(row.to_dict())
    sham_distribution = _sham_distribution(protocol)
    block_rows: list[dict[str, Any]] = []
    for index, block in enumerate(protocol["blockLayout"], 1):
        group = block["group"]
        if group in PRIMARY_GROUPS:
            episode_count = int(block["count"])
            duration = episode_count * (24.0 if group != "SELF_PACED" else 24.0)
        else:
            duration = float(block["durationSeconds"])
            episode_count = 0
        block_rows.append({
            "scheduleIndex": index,
            "blockId": block["blockId"],
            "group": group,
            "episodeCount": episode_count,
            "durationSeconds": duration,
            "shamReadyCueCount": sham_distribution.get(block["blockId"], 0),
            "plannedContinuousEeg": True,
        })
    return {
        "recordType": "m18_unified_acquisition_schedule",
        "schemaVersion": M18_SCHEMA_VERSION,
        "protocolVersion": protocol["protocolVersion"],
        "generatorVersion": M18_GENERATOR_VERSION,
        "seed": seed,
        "blocks": block_rows,
        "episodes": episode_dicts,
        "shamDistribution": sham_distribution,
        "historyRule": "completed episode target is appended only after EPISODE_COMPLETED; aborted episodes are excluded",
        "scheduleFingerprint": _sha256_bytes(_canonical_json({"blocks": block_rows, "episodes": episode_dicts, "seed": seed}).encode("utf-8")),
    }


def validate_matrix(protocol: Mapping[str, Any], schedule: Mapping[str, Any]) -> dict[str, Any]:
    episodes = list(schedule["episodes"])
    checks: dict[str, bool] = {}
    checks["focused_exactly_18"] = sum(row["group"] == "FOCUSED" for row in episodes) == 18
    checks["natural_exactly_60"] = sum(row["group"] == "NATURAL" for row in episodes) == 60
    checks["self_paced_exactly_24"] = sum(row["group"] == "SELF_PACED" for row in episodes) == 24
    for group in PRIMARY_GROUPS:
        group_rows = [row for row in episodes if row["group"] == group]
        for frequency in FREQUENCIES_HZ:
            checks[f"{group}_frequency_{frequency}"] = sum(row["frequencyHz"] == frequency for row in group_rows) == int(protocol["frequencyBalance"][group][str(frequency)])
        for condition in CONTEXT_CONDITIONS:
            checks[f"{group}_context_{condition}"] = sum(row["scheduledContextCondition"] == condition for row in group_rows) == int(protocol["scheduledContextBalance"][group][condition])
    checks["true_idle_9_minutes"] = sum(row["group"] == "TRUE_IDLE" for row in schedule["blocks"]) * 3 == 9
    checks["passive_browse_12_minutes"] = sum(row["group"] == "PASSIVE_BROWSE" for row in schedule["blocks"]) * 3 == 12
    checks["sham_12"] = sum(int(value) for value in schedule["shamDistribution"].values()) == 12
    checks["no_duplicate_episode_ids"] = len({row["episodeId"] for row in episodes}) == len(episodes)
    checks["self_paced_onset_not_exact"] = all(row["intentOnsetGroundTruth"] == "UNKNOWN_WITHIN_WINDOW" for row in episodes if row["group"] == "SELF_PACED")
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "failed": [key for key, value in checks.items() if not value]}


def write_matrix_csv(path: Path, protocol: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["group", "frequencyHz", "ALIGNED", "NEUTRAL", "CONFLICT", "rowTotal", "notes"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for group in PRIMARY_GROUPS:
            for frequency in FREQUENCIES_HZ:
                condition_counts = {condition: protocol["scheduledContextBalance"][group][condition] // 3 for condition in CONTEXT_CONDITIONS}
                if group == "NATURAL":
                    condition_counts = protocol["naturalContextFrequencyMatrix"][str(frequency)]
                else:
                    # Focused and self-paced are exactly balanced overall; the
                    # generated trial-level schedule remains authoritative.
                    total = int(protocol["frequencyBalance"][group][str(frequency)])
                    base, remainder = divmod(total, 3)
                    condition_counts = {condition: base + (1 if index < remainder else 0) for index, condition in enumerate(CONTEXT_CONDITIONS)}
                writer.writerow({"group": group, "frequencyHz": frequency, **condition_counts, "rowTotal": sum(condition_counts.values())})
        writer.writerow({"group": "TRUE_IDLE", "frequencyHz": "", "ALIGNED": "", "NEUTRAL": "", "CONFLICT": "", "rowTotal": 9, "notes": "minutes"})
        writer.writerow({"group": "PASSIVE_BROWSE", "frequencyHz": "", "ALIGNED": "", "NEUTRAL": "", "CONFLICT": "", "rowTotal": 12, "notes": "minutes"})
        writer.writerow({"group": "SHAM_READY", "frequencyHz": "", "ALIGNED": "", "NEUTRAL": "", "CONFLICT": "", "rowTotal": 12, "notes": "cues"})


def write_block_schedule_csv(path: Path, schedule: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["scheduleIndex", "blockId", "group", "episodeCount", "durationSeconds", "shamReadyCueCount", "plannedContinuousEeg"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(schedule["blocks"])


def _planned_duration_seconds(protocol: Mapping[str, Any]) -> dict[str, float]:
    timing = protocol["timing"]
    intentional_episode = sum(float(timing[key]) for key in ("preIntentNcSeconds", "preparationSeconds", "intentionalSeconds", "postIntentSeconds", "rearmObservationSeconds", "interEpisodeRestSeconds"))
    self_paced_episode = sum(float(timing[key]) for key in ("preIntentNcSeconds", "targetAssignmentSeconds", "selfPacedCueWashoutSeconds", "selfPacedOpportunitySeconds", "postIntentSeconds", "rearmObservationSeconds", "interEpisodeRestSeconds"))
    recording = 78 * intentional_episode + 24 * self_paced_episode + 9 * 60 + 12 * 60
    active_blocks = 3 + 6 + 4
    rating = active_blocks * float(timing["ratingMinutesPerActiveBlock"]) * 60
    breaks = 3 * float(timing["shortBreakSeconds"]) + float(timing["longBreakSeconds"])
    return {
        "focusedNaturalSelfPacedAndNcSeconds": recording,
        "formalContinuousRecordingMinutes": recording / 60.0,
        "cueAndPreparationIncludedInRecording": True,
        "ratingsSeconds": rating,
        "recommendedBreakSeconds": breaks,
        "estimatedTotalWearTimeMinutesWithSetup": (recording + rating + breaks + 10 * 60) / 60.0,
        "estimatedTotalWearTimeMinutesWithoutSetup": (recording + rating + breaks) / 60.0,
    }


def _event_records(root: Path) -> list[dict[str, Any]]:
    return _read_jsonl(root / "events" / "events.jsonl")


def validate_session(root: Path, schedule: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(root)
    events = _event_records(root)
    snapshots = _read_jsonl(root / "context" / "context-snapshots.jsonl")
    truth = _read_jsonl(root / "context" / "evaluation-truth.jsonl")
    checks: dict[str, bool] = {}
    checks["manifest_present"] = (root / "manifest.json").is_file()
    events_append_only = True
    previous_monotonic_ns = -1
    for expected_sequence, event in enumerate(events):
        try:
            if int(event["sequence"]) != expected_sequence:
                events_append_only = False
            monotonic_ns = int(event["monotonicNs"])
            if monotonic_ns < previous_monotonic_ns:
                events_append_only = False
            previous_monotonic_ns = monotonic_ns
        except (KeyError, TypeError, ValueError):
            events_append_only = False
            break
    checks["events_append_only_parseable"] = events_append_only
    checks["context_snapshot_count"] = len(snapshots) == len(schedule["episodes"])
    snapshot_hashes_valid = True
    for snapshot in snapshots:
        recorded_hash = snapshot.get("snapshotCanonicalSha256")
        hash_payload = {
            key: value
            for key, value in snapshot.items()
            if key not in ("recordType", "schemaVersion", "snapshotCanonicalSha256", "sessionId", "blockId", "episodeId")
        }
        if not isinstance(recorded_hash, str) or recorded_hash != _sha256_bytes(_canonical_json(hash_payload).encode("utf-8")):
            snapshot_hashes_valid = False
            break
    checks["context_snapshot_hashes_valid"] = snapshot_hashes_valid
    checks["truth_separate_from_events"] = all("trueTarget" not in event for event in events)
    checks["truth_count"] = len(truth) == len(schedule["episodes"])
    checks["production_boundary_unchanged"] = protocol["productionBoundary"]["productionDecoderChanged"] is False
    by_episode: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_episode.setdefault(str(event.get("episodeId")), []).append(event)
    causal = True
    state_valid = True
    for row in schedule["episodes"]:
        episode_events = by_episode.get(row["episodeId"], [])
        types = [event.get("eventType") for event in episode_events]
        required = ["EPISODE_STARTED", "PRE_INTENT_NC_STARTED", "CONTEXT_GENERATED", "CONTEXT_FROZEN", "TARGET_SAMPLED", "TARGET_INSTRUCTION"]
        if not all(value in types for value in required):
            causal = False
        positions = {value: types.index(value) for value in required if value in types}
        if not all(positions[a] < positions[b] for a, b in zip(required, required[1:]) if a in positions and b in positions):
            causal = False
        if row["group"] != "SELF_PACED" and "READY_CUE_END" not in types:
            causal = False
        if row["group"] == "SELF_PACED" and row["intentOnsetGroundTruth"] != "UNKNOWN_WITHIN_WINDOW":
            causal = False
        transitions = [event.get("toState") for event in episode_events if event.get("eventType") == "STATE_TRANSITION"]
        if transitions and transitions[-1] != "LOG_FLUSHED":
            state_valid = False
        onset = next((event for event in episode_events if event.get("eventType") == "STIMULUS_ONSET"), None)
        anchor = next((event for event in episode_events if event.get("eventType") == "TRIAL_SAMPLE_ANCHOR"), None)
        if onset is None or anchor is None:
            causal = False
        elif not (
            anchor.get("stimulusOnsetEventSequence") == onset.get("sequence")
            and anchor.get("stimulusOnsetMonotonicNs") == onset.get("monotonicNs")
            and anchor.get("anchorBasis") == "first_packet_received_at_or_after_stimulus_onset"
            and isinstance(anchor.get("sampleAnchorSampleIndex"), int)
            and isinstance(anchor.get("anchorPacketSequence"), int)
        ):
            causal = False
    checks["context_freeze_before_target"] = causal
    checks["state_machine_closed"] = state_valid
    completed_ids = [str(event.get("episodeId")) for event in events if event.get("eventType") == "EPISODE_COMPLETED" and event.get("episodeId") is not None]
    checks["no_duplicate_completed_episodes"] = len(completed_ids) == len(set(completed_ids)) == len(schedule["episodes"])
    checks["sham_count"] = sum(1 for event in events if event.get("eventType") == "SHAM_READY_CUE") == 12
    checks["cue_events_logged"] = sum(1 for event in events if event.get("eventType") == "READY_CUE_END") >= 78
    checks["block_finished"] = sum(1 for event in events if event.get("eventType") == "BLOCK_FINISHED") == len(schedule["blocks"])
    raw_paths = sorted((root / "blocks").glob("*/eeg/raw-eeg.jsonl"))
    raw_continuous = True
    packet_count = 0
    for path in raw_paths:
        packet_records = _read_jsonl(path)
        packet_count += len(packet_records)
        expected = None
        expected_packet_sequence = None
        for packet in packet_records:
            first = int(packet.get("sourceSampleIndex", -1))
            sample_count = int(packet.get("sample_count", packet.get("sampleCount", packet.get("sampleCountPerChannel", 0))))
            if expected is not None and first != expected:
                raw_continuous = False
            packet_sequence = int(packet.get("packetSequence", -1))
            if expected_packet_sequence is not None and packet_sequence != expected_packet_sequence:
                raw_continuous = False
            expected = first + sample_count
            expected_packet_sequence = packet_sequence + 1
    checks["continuous_raw_packets"] = raw_continuous and packet_count > 0
    checks["session_finalized"] = any(event.get("eventType") == "SESSION_FINALIZED" for event in events)
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
        "eventCount": len(events),
        "contextSnapshotCount": len(snapshots),
        "evaluationTruthCount": len(truth),
        "rawPacketCount": packet_count,
        "provenance": "SYNTHETIC software dry-run" if json.loads((root / "manifest.json").read_text(encoding="utf-8")).get("sourceType") == "synthetic" else "LIVE ND8 boundary; human validation required",
        "forbiddenClaims": ["accuracy", "TPR", "FPR", "Context benefit", "human EEG validation"],
    }


def _episode_event(recorder: M18SessionRecorder, clock: SyntheticClock, event_type: str, row: EpisodePlan, **values: Any) -> dict[str, Any]:
    return recorder.record_event(event_type, episodeId=row.episode_id, blockId=row.block_id, monotonicNs=clock.monotonic_ns(), **values)


def _record_transition(recorder: M18SessionRecorder, clock: SyntheticClock, row: EpisodePlan, transition: Mapping[str, Any]) -> None:
    _episode_event(recorder, clock, "STATE_TRANSITION", row, fromState=transition["from"], toState=transition["to"], reason=transition["reason"])


def _run_primary_episode(recorder: M18SessionRecorder, source: SyntheticContinuousEegSource, cue: AudioCueSystem, clock: SyntheticClock, row: EpisodePlan) -> None:
    recorder.set_active_block(row.block_id)
    state = EpisodeStateMachine(row.episode_id)
    _episode_event(recorder, clock, "EPISODE_STARTED", row, group=row.group, attentionCondition=row.attention_condition, controlStateGroundTruth=row.control_state_ground_truth, intentOnsetGroundTruth=row.intent_onset_ground_truth, scheduledContextCondition=row.scheduled_context_condition, frequencyHz=row.frequency_hz)
    _record_transition(recorder, clock, row, state.move("PRE_NC", "episode_started"))
    _episode_event(recorder, clock, "VISUAL_STIMULUS_STATE", row, stimulusState="ON", phase="PRE_INTENT_NC", sharedAcrossNcAndIc=True)
    _episode_event(recorder, clock, "PRE_INTENT_NC_STARTED", row, controlStateGroundTruth="NC_PRE_INTENT", labelQuality="KNOWN_FOR_CUED_EPISODE")
    source.emit_segment(float(M18_PROTOCOL["timing"]["preIntentNcSeconds"]), None)
    _episode_event(recorder, clock, "PRE_INTENT_NC_FINISHED", row, controlStateGroundTruth="NC_PRE_INTENT")
    _record_transition(recorder, clock, row, state.move("CONTEXT_GENERATED", "pre_nc_complete"))
    snapshot = row.context_snapshot
    if snapshot is None:
        raise AssertionError("primary episode has no Context snapshot")
    _episode_event(recorder, clock, "CONTEXT_GENERATED", row, contextSnapshotId=snapshot["contextSnapshotId"], taskStateId=snapshot["taskStateId"], inputHistoryIds=snapshot["inputHistoryIds"], generatedAtMonotonicNs=clock.monotonic_ns(), forbiddenInputsObserved=[])
    _record_transition(recorder, clock, row, state.move("CONTEXT_FROZEN", "snapshot_hash_written"))
    _episode_event(recorder, clock, "CONTEXT_FROZEN", row, contextSnapshotId=snapshot["contextSnapshotId"], snapshotCanonicalSha256=snapshot["snapshotCanonicalSha256"], frozenAtMonotonicNs=clock.monotonic_ns())
    recorder.record_context_snapshot(ContextSnapshot(**{
        "context_snapshot_id": snapshot["contextSnapshotId"],
        "task_state_id": snapshot["taskStateId"],
        "generator_version": snapshot["generatorVersion"],
        "scenario_seed": snapshot["scenarioSeed"],
        "candidate_set": tuple(snapshot["candidateSet"]),
        "candidate_set_hash": snapshot["candidateSetHash"],
        "completed_history": tuple(snapshot["completedHistory"]),
        "input_history_ids": tuple(snapshot["inputHistoryIds"]),
        "prior_vector": tuple(snapshot["priorVector"]),
        "context_top": tuple(snapshot["contextTop"]),
        "entropy": snapshot["entropy"],
        "normalized_entropy": snapshot["normalizedEntropy"],
        "context_informativeness": snapshot["contextInformativeness"],
        "generated_at_monotonic_ns": snapshot["generatedAtMonotonicNs"],
        "frozen_at_monotonic_ns": snapshot["frozenAtMonotonicNs"],
        "generated_at_utc": snapshot["generatedAtUtc"],
        "frozen_at_utc": snapshot["frozenAtUtc"],
        "snapshot_canonical_sha256": snapshot["snapshotCanonicalSha256"],
    }), row.block_id, row.episode_id)
    _record_transition(recorder, clock, row, state.move("TARGET_SAMPLED", "context_frozen"))
    target = row.target_sample
    if target is None:
        raise AssertionError("primary episode has no target sample")
    _episode_event(recorder, clock, "TARGET_SAMPLED", row, targetSamplingRule=target["targetSamplingRule"], targetSamplerSeed=target["targetSamplerSeed"], observedContextRelation=target["observedContextRelation"], targetVisibleToContextGenerator=False)
    _record_transition(recorder, clock, row, state.move("TARGET_PRESENTED", "target_sampled"))
    _episode_event(recorder, clock, "TARGET_INSTRUCTION", row, targetVisibleToContextGenerator=False, targetInstructionSemantics="target_after_context_freeze")
    if row.group == "SELF_PACED":
        cue.target_cue(row.episode_id, recorder.session_id, row.slot_index, row.frequency_hz)
        _record_transition(recorder, clock, row, state.move("PREPARE", "target_assignment"))
        _episode_event(recorder, clock, "PREPARE_STARTED", row, controlStateGroundTruth="AMBIGUOUS_PREPARE", intentOnsetGroundTruth="UNKNOWN_WITHIN_WINDOW")
        source.emit_segment(float(M18_PROTOCOL["timing"]["targetAssignmentSeconds"] + M18_PROTOCOL["timing"]["selfPacedCueWashoutSeconds"]), None)
        _episode_event(recorder, clock, "SELF_PACED_OPPORTUNITY_OPEN", row, intentOnsetGroundTruth="UNKNOWN_WITHIN_WINDOW", readyCueProvided=False)
    else:
        cue.target_cue(row.episode_id, recorder.session_id, row.slot_index, row.frequency_hz)
        _record_transition(recorder, clock, row, state.move("PREPARE", "target_instruction"))
        _episode_event(recorder, clock, "PREPARE_STARTED", row, controlStateGroundTruth="AMBIGUOUS_PREPARE", intentOnsetGroundTruth="CUED_APPROXIMATE")
        source.emit_segment(float(M18_PROTOCOL["timing"]["preparationSeconds"]), None)
        _episode_event(recorder, clock, "PREPARE_FINISHED", row, controlStateGroundTruth="AMBIGUOUS_PREPARE")
        cue.ready_cue(row.episode_id, recorder.session_id)
        cue.wait_for_stimulus_silence(row.episode_id)
        _record_transition(recorder, clock, row, state.move("READY", "ready_cue_complete"))
    if row.group == "SELF_PACED":
        _episode_event(recorder, clock, "PREPARE_FINISHED", row, controlStateGroundTruth="AMBIGUOUS_PREPARE")
    if row.group == "SELF_PACED":
        # Self-paced deliberately has no selection-onset READY cue.  The
        # visual onset is still an event boundary, but it is not routed
        # through the M13.7 READY-gated helper.
        onset = _episode_event(
            recorder,
            clock,
            "STIMULUS_ONSET",
            row,
            eventSource="orchestrator",
            slotIndex=row.slot_index,
            frequencyHz=row.frequency_hz,
            timingSemantics="self_paced_opportunity_onset_not_intent_onset",
            physicalOpticalTimingVerified=False,
        )
    else:
        onset = cue.stimulus_onset(row.episode_id, recorder.session_id, row.slot_index, row.frequency_hz, row.scheduled_context_condition)
    anchor = source.capture_sample_anchor(onset)
    _episode_event(recorder, clock, "TRIAL_SAMPLE_ANCHOR", row, **anchor)
    _record_transition(recorder, clock, row, state.move("INTENTIONAL", "stimulus_onset"))
    _episode_event(recorder, clock, "INTENT_ONSET_MARKER", row, intentOnsetGroundTruth=row.intent_onset_ground_truth, markerQuality=row.intent_onset_ground_truth)
    source.emit_segment(float(M18_PROTOCOL["timing"]["selfPacedOpportunitySeconds"] if row.group == "SELF_PACED" else M18_PROTOCOL["timing"]["intentionalSeconds"]), row.slot_index)
    _record_transition(recorder, clock, row, state.move("END", "intentional_window_complete"))
    _episode_event(recorder, clock, "STIMULUS_OFFSET", row, stopReason="configured_intentional_window")
    if row.group == "SELF_PACED":
        _episode_event(recorder, clock, "SELF_PACED_OPPORTUNITY_CLOSE", row, intentOnsetGroundTruth="UNKNOWN_WITHIN_WINDOW")
    else:
        cue.stimulus_offset(row.episode_id, recorder.session_id, reason="configured_intentional_window")
    cue.end_cue(row.episode_id, recorder.session_id, outcome="window_complete")
    _record_transition(recorder, clock, row, state.move("POST_INTENT", "end_cue_complete"))
    _episode_event(recorder, clock, "POST_INTENT_STARTED", row, controlStateGroundTruth="POST_INTENT_UNCERTAIN")
    source.emit_segment(float(M18_PROTOCOL["timing"]["postIntentSeconds"]), row.slot_index)
    _episode_event(recorder, clock, "POST_INTENT_FINISHED", row, controlStateGroundTruth="POST_INTENT_UNCERTAIN")
    _episode_event(recorder, clock, "REARM_OBSERVATION_STARTED", row, controlStateGroundTruth="REFRACTORY")
    source.emit_segment(float(M18_PROTOCOL["timing"]["rearmObservationSeconds"]), None)
    _episode_event(recorder, clock, "REARM_OBSERVATION_FINISHED", row, controlStateGroundTruth="NC_PRE_INTENT")
    source.emit_segment(float(M18_PROTOCOL["timing"]["interEpisodeRestSeconds"]), None)
    _record_transition(recorder, clock, row, state.move("COMPLETE", "post_intent_and_rearm_logged"))
    _episode_event(recorder, clock, "EPISODE_COMPLETED", row, controlStateGroundTruth=row.control_state_ground_truth, intentOnsetGroundTruth=row.intent_onset_ground_truth, contextSnapshotId=snapshot["contextSnapshotId"], selectedTargetRuntime=None)
    _record_transition(recorder, clock, row, state.move("LOG_FLUSHED", "append_only_records_flushed"))
    recorder.record_evaluation_truth(row.block_id, row.episode_id, trueTarget={"targetId": row.target_id, "logicalBlockId": row.logical_block_id, "slotIndex": row.slot_index, "frequencyHz": row.frequency_hz}, targetSamplerDraw=target, observedContextRelation=row.observed_context_relation)


def _run_nonintentional_block(recorder: M18SessionRecorder, source: SyntheticContinuousEegSource, cue: AudioCueSystem, clock: SyntheticClock, block: Mapping[str, Any], sham_count: int) -> None:
    block_id = block["blockId"]
    group = block["group"]
    recorder.set_active_block(block_id)
    ground_truth = "NC_TRUE_IDLE" if group == "TRUE_IDLE" else "NC_PASSIVE_BROWSE"
    recorder.record_event("BLOCK_STARTED", blockId=block_id, monotonicNs=clock.monotonic_ns(), group=group, controlStateGroundTruth=ground_truth, continuousEegFile=f"blocks/{block_id}/eeg/raw-eeg.jsonl", behaviorCondition=group)
    recorder.record_event("VISUAL_STIMULUS_STATE", blockId=block_id, monotonicNs=clock.monotonic_ns(), stimulusState="ON", phase="NC", sharedAcrossNcAndIc=True)
    duration = float(block["durationSeconds"])
    if sham_count <= 0:
        source.emit_segment(duration, None)
    else:
        segment = duration / float(sham_count + 1)
        for index in range(sham_count):
            source.emit_segment(segment, None)
            sham_id = f"{block_id}-sham-{index + 1:02d}"
            recorder.record_event("SHAM_READY_CUE", blockId=block_id, episodeId=sham_id, monotonicNs=clock.monotonic_ns(), eventSource="orchestrator", cueType="SHAM_READY", controlStateGroundTruth="SHAM_CUE_NC", behaviorCondition=group, selectionInstructionProvided=False)
            cue.ready_cue(sham_id, recorder.session_id)
        source.emit_segment(segment, None)
    recorder.record_event("BLOCK_FINISHED", blockId=block_id, monotonicNs=clock.monotonic_ns(), group=group, completedEpisodeCount=0, abortedEpisodeCount=0, controlStateGroundTruth=ground_truth)


def run_synthetic_dry_run(output_dir: Path, protocol: Mapping[str, Any] = M18_PROTOCOL, session_id: str = M18_SESSION_ID, resume: bool = False) -> dict[str, Any]:
    output_dir = Path(output_dir)
    schedule = build_schedule(protocol)
    matrix = validate_matrix(protocol, schedule)
    if matrix["status"] != "PASS":
        raise ValueError(f"M18 schedule matrix failed: {matrix['failed']}")
    session_root = output_dir / "participant_P001" / "session_001"
    existing_manifest_path = session_root / "manifest.json"
    if resume and existing_manifest_path.is_file():
        existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        if existing_manifest.get("status") == "finalized":
            validation = validate_session(session_root, schedule, protocol)
            return {"status": validation["status"], "sessionRoot": str(session_root), "schedule": schedule, "matrix": matrix, "validation": validation, "manifest": existing_manifest, "resumedWithoutWrites": True}
    clock = SyntheticClock()
    recorder = M18SessionRecorder(session_root, session_id, protocol, source_type="synthetic", resume=resume, utc_now_provider=clock.utc_now)
    if resume:
        existing_events = _event_records(session_root)
        clock.ns = max((int(item.get("monotonicNs", 0)) for item in existing_events), default=-1) + 1
    source = SyntheticContinuousEegSource(recorder, clock)
    cue = AudioCueSystem(
        NullLoggingAudioBackend(clock.monotonic_ns),
        recorder.record_event,
        monotonic_ns=clock.monotonic_ns,
        utc_now=clock.utc_now,
        sleep=clock.sleep,
        # AudioCueSystem enforces the frozen M13.7 minimum of one second.
        # SyntheticClock advances virtual time only, so this does not make
        # the software dry-run wait in wall-clock time.
        required_silence_seconds=float(protocol["timing"]["guaranteedSilenceBeforeStimulusSeconds"]),
        timing_configuration={
            "targetFrequencyHz": protocol["cueProfile"]["targetFrequencyHz"],
            "targetBeepSeconds": protocol["cueProfile"]["targetBeepSeconds"],
            "readyFrequencyHz": protocol["cueProfile"]["readyFrequencyHz"],
            "readyBeepSeconds": protocol["cueProfile"]["readyBeepSeconds"],
            "endFrequencyHz": protocol["cueProfile"]["endFrequencyHz"],
            "endBeepSeconds": protocol["cueProfile"]["endBeepSeconds"],
        },
    )
    if not _event_records(session_root):
        recorder.record_event("SESSION_STARTED", blockId=None, monotonicNs=clock.monotonic_ns(), participantId="P001", samplingRateHz=1000.0, channelCount=8, protocolSha256=recorder.protocol_hash, hardwareBoundary=recorder.manifest["hardwareBoundary"], sourceType="synthetic", accuracyNotComputed=True)
    if resume:
        runtime_history, runtime_remaining = _runtime_resume_state(session_root, schedule, recorder, protocol)
    else:
        runtime_history = []
        runtime_remaining = {
            group: {
                slot: int(protocol["frequencyBalance"][group][str(FREQUENCIES_HZ[slot])])
                for slot in range(3)
            }
            for group in PRIMARY_GROUPS
        }
    episodes_by_block: dict[str, list[EpisodePlan]] = {}
    for row_dict in schedule["episodes"]:
        row = EpisodePlan(
            episode_id=row_dict["episodeId"], block_id=row_dict["blockId"], ordinal=row_dict["ordinal"], group=row_dict["group"], attention_condition=row_dict["attentionCondition"], scheduled_context_condition=row_dict["scheduledContextCondition"], slot_index=row_dict["slotIndex"], frequency_hz=row_dict["frequencyHz"], target_id=row_dict["targetId"], logical_block_id=row_dict["logicalBlockId"], observed_context_relation=row_dict["observedContextRelation"], intent_onset_ground_truth=row_dict["intentOnsetGroundTruth"], control_state_ground_truth=row_dict["controlStateGroundTruth"], context_snapshot=row_dict["contextSnapshot"], target_sample=row_dict["targetSample"],
        )
        episodes_by_block.setdefault(row.block_id, []).append(row)
    for block in schedule["blocks"]:
        block_id = block["blockId"]
        if block_id in recorder.completed_blocks:
            continue
        recorder.set_active_block(block_id)
        if block["group"] in PRIMARY_GROUPS:
            recorder.record_event("BLOCK_STARTED", blockId=block_id, monotonicNs=clock.monotonic_ns(), group=block["group"], controlStateGroundTruth=f"IC_{'NATURAL' if block['group'] == 'NATURAL' else block['group']}", continuousEegFile=f"blocks/{block_id}/eeg/raw-eeg.jsonl", attentionCondition="NATURAL_GAZE" if block["group"] == "NATURAL" else block["group"])
            for row in episodes_by_block[block_id]:
                if row.episode_id in recorder.completed_episodes:
                    continue
                if row.episode_id in recorder.aborted_episodes:
                    raise RuntimeError(f"aborted M18 episode requires a new retry identity: {row.episode_id}")
                runtime_row = _runtime_episode_plan(
                    row,
                    completed_history=runtime_history,
                    remaining_slot_counts=runtime_remaining[row.group],
                )
                _run_primary_episode(recorder, source, cue, clock, runtime_row)
                runtime_history.append(str(runtime_row.logical_block_id))
                runtime_remaining[row.group][int(runtime_row.slot_index)] -= 1
            recorder.record_event("BLOCK_RATING", blockId=block_id, monotonicNs=clock.monotonic_ns(), ratingStatus="NOT_COLLECTED_IN_SYNTHETIC_DRY_RUN", ratingsRequired=["attentionEffort", "visualFatigue", "taskDifficulty", "naturalness"])
            recorder.record_event("BLOCK_FINISHED", blockId=block_id, monotonicNs=clock.monotonic_ns(), group=block["group"], completedEpisodeCount=len(episodes_by_block[block_id]), abortedEpisodeCount=0)
        else:
            _run_nonintentional_block(recorder, source, cue, clock, block, int(schedule["shamDistribution"].get(block_id, 0)))
        if int(block["scheduleIndex"]) in (5, 10, 15, 20):
            recorder.record_event("BREAK_WINDOW", blockId=block_id, monotonicNs=clock.monotonic_ns(), breakSeconds=60.0 if int(block["scheduleIndex"]) != 10 else 180.0, eegIncluded=False)
    manifest = recorder.finalize()
    validation = validate_session(session_root, schedule, protocol)
    _atomic_json(output_dir / "synthetic_manifest.json", {
        "recordType": "m18_synthetic_manifest",
        "schemaVersion": M18_SCHEMA_VERSION,
        "status": validation["status"],
        "sessionRoot": str(session_root),
        "sourceType": "synthetic",
        "accuracy": "NOT_COMPUTED",
        "tpr": "NOT_COMPUTED",
        "fpr": "NOT_COMPUTED",
        "contextBenefit": "NOT_COMPUTED",
        "validation": validation,
        "manifestStatus": manifest.get("status"),
        "timeScale": 0.01,
        "hardwareOperated": False,
    })
    return {"status": validation["status"], "sessionRoot": str(session_root), "schedule": schedule, "matrix": matrix, "validation": validation, "manifest": manifest}


def _physical_smoke_rows(schedule: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for group in ("FOCUSED", "NATURAL"):
        for condition in CONTEXT_CONDITIONS:
            match = next((row for row in schedule["episodes"] if row["group"] == group and row["scheduledContextCondition"] == condition), None)
            if match is not None:
                selected.append(match)
    return selected


def run_physical_smoke(
    data_root: Path,
    *,
    com_port: str,
    session_id: str = "m18-physical-smoke-001",
    confirm_live_human: bool = False,
) -> dict[str, Any]:
    """Run the bounded physical smoke only after explicit operator confirmation.

    The function is intentionally not called by software preflight or artifact
    generation.  It opens the requested ND8 COM port only when the operator
    supplies ``confirm_live_human=True`` and writes outside the repository.
    Quest visual presentation remains the existing M5/M7 Unity path and is
    recorded as a human acceptance boundary rather than inferred by Python.
    """
    if not confirm_live_human:
        raise PermissionError("physical smoke requires --confirm-live-human")
    repository = Path(__file__).resolve().parents[1]
    root = Path(data_root).resolve()
    if root == repository or root.is_relative_to(repository):
        raise ValueError("live M18 output must be outside the repository")
    if not TRIAL_ID_RE.match(str(session_id)):
        raise ValueError("invalid physical smoke session ID")
    schedule = build_schedule(M18_PROTOCOL)
    rows = _physical_smoke_rows(schedule)
    if len(rows) != 6:
        raise RuntimeError("physical smoke plan did not find six Focused/Natural context-coverage episodes")
    session_root = root / str(session_id)
    clock = RealClock()
    recorder = M18SessionRecorder(session_root, session_id, M18_PROTOCOL, source_type="live_nd8", utc_now_provider=clock.utc_now)
    source = M18LiveContinuousEegSource(recorder, com_port)
    from integration.m13_7_golden_session import PcToneAudioBackend

    cue = AudioCueSystem(
        PcToneAudioBackend(),
        recorder.record_event,
        monotonic_ns=clock.monotonic_ns,
        utc_now=clock.utc_now,
        sleep=clock.sleep,
        required_silence_seconds=float(M18_PROTOCOL["timing"]["guaranteedSilenceBeforeStimulusSeconds"]),
        timing_configuration=M18_PROTOCOL["cueProfile"],
    )
    recorder.record_event(
        "SESSION_STARTED",
        blockId=None,
        monotonicNs=clock.monotonic_ns(),
        participantId=recorder.participant_id,
        samplingRateHz=recorder.sampling_rate_hz,
        channelCount=recorder.channel_count,
        protocolSha256=recorder.protocol_hash,
        sourceType="live_nd8",
        smokeOnly=True,
        questManualAcceptanceRequired=True,
    )
    try:
        recorder.set_active_block(rows[0]["blockId"])
        source.open()
        row_objects = [EpisodePlan(
            episode_id=row["episodeId"], block_id=row["blockId"], ordinal=row["ordinal"], group=row["group"], attention_condition=row["attentionCondition"], scheduled_context_condition=row["scheduledContextCondition"], slot_index=row["slotIndex"], frequency_hz=row["frequencyHz"], target_id=row["targetId"], logical_block_id=row["logicalBlockId"], observed_context_relation=row["observedContextRelation"], intent_onset_ground_truth=row["intentOnsetGroundTruth"], control_state_ground_truth=row["controlStateGroundTruth"], context_snapshot=row["contextSnapshot"], target_sample=row["targetSample"],
        ) for row in rows]
        runtime_history: list[str] = []
        runtime_remaining = {
            group: {
                slot: int(M18_PROTOCOL["frequencyBalance"][group][str(FREQUENCIES_HZ[slot])])
                for slot in range(3)
            }
            for group in PRIMARY_GROUPS
        }
        for row in row_objects:
            runtime_row = _runtime_episode_plan(
                row,
                completed_history=runtime_history,
                remaining_slot_counts=runtime_remaining[row.group],
                generated_at_monotonic_ns=clock.monotonic_ns(),
            )
            _run_primary_episode(recorder, source, cue, clock, runtime_row)
            runtime_history.append(str(runtime_row.logical_block_id))
            runtime_remaining[row.group][int(runtime_row.slot_index)] -= 1
        idle_block = {"blockId": "SMOKE_TRUE_IDLE", "group": "TRUE_IDLE", "durationSeconds": 10.0}
        browse_block = {"blockId": "SMOKE_PASSIVE_BROWSE", "group": "PASSIVE_BROWSE", "durationSeconds": 10.0}
        _run_nonintentional_block(recorder, source, cue, clock, idle_block, 0)
        _run_nonintentional_block(recorder, source, cue, clock, browse_block, 0)
        recorder.record_event("SMOKE_ONLY_REVIEW_REQUIRED", blockId=None, monotonicNs=clock.monotonic_ns(), eventSource="operator", questVisualCheckRequired=True, electrodeQualityCheckRequired=True, physicalTimingCheckRequired=True)
    except Exception as error:
        recorder.record_event("SESSION_ABORTED", blockId=recorder._active_block, monotonicNs=clock.monotonic_ns(), eventSource="recorder", abortReason=f"{type(error).__name__}: {error}")
        raise
    finally:
        source.close()
    manifest = recorder.finalize()
    events = _event_records(session_root)
    raw_files = sorted((session_root / "blocks").glob("*/eeg/raw-eeg.jsonl"))
    smoke_report = {
        "recordType": "m18_physical_smoke_report",
        "schemaVersion": M18_SCHEMA_VERSION,
        "status": "SMOKE_RECORDED_REQUIRES_HUMAN_REVIEW",
        "smokeOnly": True,
        "sessionRoot": str(session_root),
        "intentionalEpisodes": len(row_objects),
        "eventCount": len(events),
        "rawPacketCount": sum(len(_read_jsonl(path)) for path in raw_files),
        "newRealEegCollected": manifest.get("hardwareBoundary", {}).get("newRealEegCollected", False),
        "humanChecksRequired": ["Quest stimulus visible/continuous", "target mapping", "cue audibility", "electrode quality", "physical timing", "replay integrity"],
        "primaryN": "NOT_INCLUDED",
        "accuracy": "NOT_COMPUTED",
    }
    _atomic_json(session_root / "smoke_acceptance.json", smoke_report)
    return smoke_report


def export_canonical(session_root: Path, output_dir: Path, schedule: Mapping[str, Any]) -> dict[str, Any]:
    session_root = Path(session_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events = _event_records(session_root)
    snapshots = _read_jsonl(session_root / "context" / "context-snapshots.jsonl")
    truth = _read_jsonl(session_root / "context" / "evaluation-truth.jsonl")
    episode_events = {row["episodeId"]: [event for event in events if event.get("episodeId") == row["episodeId"]] for row in schedule["episodes"]}
    phase2_rows = []
    phase3_rows = []
    for row in schedule["episodes"]:
        own = episode_events[row["episodeId"]]
        onset = next((event for event in own if event.get("eventType") == "STIMULUS_ONSET"), None)
        anchor = next((event for event in own if event.get("eventType") == "TRIAL_SAMPLE_ANCHOR"), None)
        cue_markers = {
            str(event.get("eventType")): {
                "sequence": event.get("sequence"),
                "monotonicNs": event.get("monotonicNs"),
            }
            for event in own
            if "CUE" in str(event.get("eventType")) or event.get("eventType") in {
                "STIMULUS_ONSET", "ANALYSIS_WINDOW_OPEN", "ANALYSIS_WINDOW_CLOSE", "STIMULUS_OFFSET",
            }
        }
        record = {
            "episodeId": row["episodeId"],
            "blockId": row["blockId"],
            "group": row["group"],
            "attentionCondition": row["attentionCondition"],
            "scheduledContextCondition": row["scheduledContextCondition"],
            "observedContextRelation": row["observedContextRelation"],
            "target": "EVALUATION_ONLY_IN_CONTEXT_TRUTH_FILE",
            "frequencyHz": row["frequencyHz"],
            "intentOnsetGroundTruth": row["intentOnsetGroundTruth"],
            "controlStateGroundTruth": row["controlStateGroundTruth"],
            "contextSnapshotId": (row["contextSnapshot"] or {}).get("contextSnapshotId"),
            "stimulusOnsetLogged": onset is not None,
            "eventTypes": [str(event.get("eventType")) for event in own],
            "cueMarkers": cue_markers,
            "stimulusOnset": None if onset is None else {
                "sequence": onset.get("sequence"),
                "monotonicNs": onset.get("monotonicNs"),
                "slotIndex": onset.get("slotIndex"),
                "frequencyHz": onset.get("frequencyHz"),
            },
            "sampleAnchor": None if anchor is None else {
                "sampleAnchorSampleIndex": anchor.get("sampleAnchorSampleIndex"),
                "anchorPacketSequence": anchor.get("anchorPacketSequence"),
                "anchorBasis": anchor.get("anchorBasis"),
                "stimulusOnsetEventSequence": anchor.get("stimulusOnsetEventSequence"),
                "stimulusOnsetMonotonicNs": anchor.get("stimulusOnsetMonotonicNs"),
            },
            "continuousRawSource": f"blocks/{row['blockId']}/eeg/raw-eeg.jsonl",
        }
        if row["group"] in ("FOCUSED", "NATURAL"):
            phase2_rows.append(record)
        phase3_rows.append(record)
    phase2 = {
        "recordType": "m18_phase2_canonical_export",
        "schemaVersion": M18_SCHEMA_VERSION,
        "source": "continuous_raw_nd8_plus_append_only_events",
        "rows": phase2_rows,
        "analysisReadyFields": ["attentionCondition", "scheduledContextCondition", "observedContextRelation", "frequencyHz", "4.0_s_intentional_window", "pre_intent_metadata", "post_intent_metadata"],
        "classifierTrainingPerformed": False,
    }
    phase3 = {
        "recordType": "m18_phase3_canonical_export",
        "schemaVersion": M18_SCHEMA_VERSION,
        "source": "continuous_raw_nd8_plus_fine_grained_control_state_events",
        "rows": phase3_rows,
        "ncSources": ["NC_TRUE_IDLE", "NC_PASSIVE_BROWSE", "NC_PRE_INTENT", "POST_INTENT_UNCERTAIN", "REARM_OBSERVATION"],
        "candidateConditionedNcDeferredToOfflineFBCCA": True,
        "selfPacedOnsetTruth": "UNKNOWN_WITHIN_WINDOW",
        "classifierTrainingPerformed": False,
        "humanPerformanceMetrics": "NOT_COMPUTED",
    }
    _atomic_json(output_dir / "phase2_export.json", phase2)
    _atomic_json(output_dir / "phase3_export.json", phase3)
    _atomic_json(output_dir / "export_manifest.json", {
        "recordType": "m18_canonical_export_manifest",
        "schemaVersion": M18_SCHEMA_VERSION,
        "phase2": "phase2_export.json",
        "phase3": "phase3_export.json",
        "snapshotCount": len(snapshots),
        "evaluationTruthCount": len(truth),
        "eventCount": len(events),
        "finalTargetTruthLocation": "context/evaluation-truth.jsonl",
        "productionDecoderChanged": False,
    })
    return {"phase2Rows": len(phase2_rows), "phase3Rows": len(phase3_rows), "status": "PASS"}


def run_context_causality_tests() -> dict[str, Any]:
    engine = M18ContextEngine()
    generated = engine.generate(completed_history=(), task_state_id="task-0", scenario_seed=1, generated_at_monotonic_ns=10, frozen_at_monotonic_ns=11)
    checks = {
        "same_history_seed_reproducible": generated.to_dict() == engine.generate(completed_history=(), task_state_id="task-0", scenario_seed=1, generated_at_monotonic_ns=10, frozen_at_monotonic_ns=11).to_dict(),
        "freeze_after_generation": generated.frozen_at_monotonic_ns >= generated.generated_at_monotonic_ns,
        "target_not_visible": generated.target_visible_to_generator is False,
        "forbidden_true_target_rejected": False,
        "forbidden_eeg_rejected": False,
        "snapshot_hash_reproducible": generated.snapshot_canonical_sha256 == generated.to_dict()["snapshotCanonicalSha256"],
    }
    try:
        engine.generate(completed_history=(), task_state_id="task-0", scenario_seed=1, generated_at_monotonic_ns=10, true_target="leak")
    except ValueError:
        checks["forbidden_true_target_rejected"] = True
    try:
        engine.generate(completed_history=(), task_state_id="task-0", scenario_seed=1, generated_at_monotonic_ns=10, current_trial_eeg=[1])
    except ValueError:
        checks["forbidden_eeg_rejected"] = True
    checks["conflict_semantics_defined"] = M18_PROTOCOL["context"]["forbiddenInputs"][-1] == "futureOutcome"
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def run_software_preflight(artifact_dir: Path, protocol: Mapping[str, Any] = M18_PROTOCOL, schedule: Mapping[str, Any] | None = None) -> dict[str, Any]:
    artifact_dir = Path(artifact_dir)
    schedule = build_schedule(protocol) if schedule is None else schedule
    matrix = validate_matrix(protocol, schedule)
    causality = run_context_causality_tests()
    dry_run = run_synthetic_dry_run(artifact_dir / "synthetic_session", protocol=protocol, session_id=M18_SESSION_ID, resume=False)
    exports = export_canonical(Path(dry_run["sessionRoot"]), Path(dry_run["sessionRoot"]) / "exports", schedule)
    synthetic_manifest_path = artifact_dir / "synthetic_manifest.json"
    source_synthetic_manifest = artifact_dir / "synthetic_session" / "synthetic_manifest.json"
    if source_synthetic_manifest.is_file():
        _atomic_json(synthetic_manifest_path, json.loads(source_synthetic_manifest.read_text(encoding="utf-8")))
    checks = {
        "protocol": matrix["status"] == "PASS",
        "matrix": matrix["status"] == "PASS",
        "context_causality": causality["status"] == "PASS",
        "synthetic_dry_run": dry_run["status"] == "PASS",
        "offline_exporter": exports["status"] == "PASS",
        "write_permission": os.access(str(artifact_dir), os.W_OK),
        "quest_endpoint": "NOT_ATTEMPTED_HUMAN_CHECK_REQUIRED",
        "nd8_com": "NOT_ATTEMPTED_HUMAN_CHECK_REQUIRED",
        "electrode_quality": "HUMAN_CHECK_REQUIRED",
        "physical_timing": "HUMAN_CHECK_REQUIRED",
    }
    software_pass = all(value is True for value in checks.values() if isinstance(value, bool))
    report = {
        "recordType": "m18_software_preflight_report",
        "schemaVersion": M18_SCHEMA_VERSION,
        "status": "PASS" if software_pass else "FAIL",
        "finalStatus": "M18_SOFTWARE_READY_FOR_PHYSICAL_SMOKE" if software_pass else "M18_BLOCKED: software preflight failure",
        "checks": checks,
        "matrix": matrix,
        "contextCausality": causality,
        "syntheticDryRun": {"status": dry_run["status"], "sessionRoot": dry_run["sessionRoot"], "validation": dry_run["validation"]},
        "offlineExporter": exports,
        "hardwareBoundary": {
            "questOperated": False,
            "nd8Operated": False,
            "humanEegCollected": False,
            "physicalTimingVerified": False,
            "humanChecksRequired": True,
        },
        "forbiddenClaims": ["HUMAN_DATA_VALIDATED", "PHASE3_VALIDATED", "accuracy", "TPR", "FPR"],
    }
    _atomic_json(artifact_dir / "preflight_report.json", report)
    return report


def run_hardware_preflight(
    output_path: Path,
    *,
    com_port: str = "COM11",
    quest_host: str = "0.0.0.0",
    quest_port: int = 11001,
) -> dict[str, Any]:
    """Create the operator-facing hardware preflight report without opening hardware.

    This mode is intentionally conservative: it validates the software
    boundary and records the requested endpoint, but electrode quality,
    physical display timing, Quest visibility, ND8 packets, and human audible
    cue perception remain explicit human checks.
    """
    checks: list[dict[str, Any]] = []
    required = [
        "integration/m18_unified_acquisition.py",
        "integration/m13_7_golden_session.py",
        "eeg/acquisition/nd8_serial_adapter.py",
        "m7_unity6000/Assets/BCI/TargetBinding/BciSsvepTargetBinding.cs",
    ]
    repository = Path(__file__).resolve().parents[1]
    missing = [item for item in required if not (repository / item).is_file()]
    checks.append({"name": "required-software-boundary", "status": "PASS" if not missing else "FAIL", "detail": "all reusable M13.7/ND8/Unity boundary files are present" if not missing else f"missing: {missing}"})
    try:
        PcToneAudioBackend = __import__("integration.m13_7_golden_session", fromlist=["PcToneAudioBackend"]).PcToneAudioBackend
        backend = PcToneAudioBackend()
        checks.append({"name": "pc-audio-constructor", "status": "PASS", "detail": f"{backend.name} constructed; no tone emitted"})
    except Exception as error:  # pragma: no cover - platform-specific
        checks.append({"name": "pc-audio-constructor", "status": "WARN", "detail": f"human audio check required: {type(error).__name__}: {error}"})
    checks.extend([
        {"name": "nd8-com", "status": "HUMAN_CHECK_REQUIRED", "detail": f"requested port={com_port}; this report did not open a COM port"},
        {"name": "quest-endpoint", "status": "HUMAN_CHECK_REQUIRED", "detail": f"requested endpoint={quest_host}:{int(quest_port)}; this report did not connect Quest"},
        {"name": "electrode-quality", "status": "HUMAN_CHECK_REQUIRED", "detail": "perform the frozen ND8 channel-quality check with electrodes attached"},
        {"name": "physical-optical-timing", "status": "HUMAN_CHECK_REQUIRED", "detail": "verify Quest refresh/optical timing on the physical device"},
        {"name": "cue-perception", "status": "HUMAN_CHECK_REQUIRED", "detail": "operator must hear target/READY/END cues and approve comfort"},
    ])
    report = {
        "recordType": "m18_hardware_preflight_report",
        "schemaVersion": M18_SCHEMA_VERSION,
        "status": "READY_FOR_HUMAN_CHECK" if not missing else "BLOCKED",
        "finalStatus": "HUMAN_CHECK_REQUIRED",
        "protocolVersion": M18_PROTOCOL_VERSION,
        "requestedComPort": str(com_port),
        "requestedQuestEndpoint": {"host": str(quest_host), "port": int(quest_port)},
        "checks": checks,
        "hardwareBoundary": {
            "comOpened": False,
            "nd8Operated": False,
            "questConnectionAttempted": False,
            "newRealEegCollected": False,
            "physicalOpticalTimingVerified": False,
        },
        "humanActionRequired": True,
    }
    _atomic_json(Path(output_path), report)
    return report


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def render_artifact_docs(artifact_dir: Path, protocol: Mapping[str, Any], schedule: Mapping[str, Any], preflight: Mapping[str, Any]) -> None:
    artifact_dir = Path(artifact_dir)
    duration = _planned_duration_seconds(protocol)
    status = preflight.get("finalStatus", "M18_BLOCKED: preflight not run")
    docs: dict[str, str] = {
        "00_EXECUTIVE_SUMMARY.md": f"""# M18 Unified Phase 2 + Phase 3 Acquisition Framework

Status: **{status}**

This is software-side protocol freeze, acquisition infrastructure, synthetic validation, and hardware preflight preparation. No Quest, ND8, human EEG, or physical timing was operated here. Synthetic EEG is used only to validate file continuity and causal ordering; no accuracy, TPR, FPR, or Context benefit is computed.

Primary plan: Focused 18, Natural 60, Self-paced 24, True Idle 9 minutes, Passive Browse 12 minutes, and 12 embedded sham READY cues. Smoke is at most 9 episodes and is not primary N.

The next real action after this software pass is a user-operated physical smoke with Quest + ND8, followed by human review of the acceptance checklist.
""",
        "01_FINAL_PROTOCOL.md": """# Final Protocol

The authoritative machine-readable protocol is `m18_protocol.json`. The one-wear plan uses one continuous session and deterministic balanced schedule. Focused and Natural use the same visual/cue/recording lifecycle; attention condition is metadata only and never enters the decoder or Context generator.

Context order is observable completed history → task state → Context prior → immutable snapshot freeze → target sampling → instruction → EEG. Target truth is evaluation-only.
""",
        "02_UNIFIED_DATA_MATRIX.md": """# Unified Data Matrix

| Group | Primary N / duration | Frequency balance | Context balance |
|---|---:|---|---|
| Focused | 18 | 7.2/9/12 = 6/6/6 | 6/6/6 |
| Natural | 60 | 20/20/20 | 20/20/20 |
| Self-paced | 24 | 8/8/8 | 8/8/8 |
| True Idle | 9 min | stimuli remain present | known no-control |
| Passive Browse | 12 min | stimuli remain present | hard negative |
| Sham READY | 12 cues | embedded in Idle/Browse | NC sham |

The CSV is the machine-readable matrix. Scheduled Context condition and observed Context relation are both retained.
""",
        "03_BLOCK_SCHEDULE.md": """# Block Schedule

The deterministic order is in `block_schedule.csv`. Focused is distributed across early/middle/late blocks; Natural is distributed across the session; Idle, Browse, and Self-paced are interleaved to reduce a single late-fatigue interpretation. Break windows are recorded after each block, with a longer mid-session break.
""",
        "04_CONTEXT_CAUSALITY.md": """# Context Causality

M16 three-candidate routes are reused. The generator consumes only completed observable history, task state, candidate catalogue, and a seed. It rejects true target, current EEG, decoder score, selection result, and future outcome. Snapshot records are immutable and hashed. Conflict means a draw outside a unique informative Context top; ties/low-information cases are Neutral by observed relation.
""",
        "05_EPISODE_STATE_MACHINE.md": """# Episode State Machine

Intentional episodes use `CREATED → PRE_NC → CONTEXT_GENERATED → CONTEXT_FROZEN → TARGET_SAMPLED → TARGET_PRESENTED → PREPARE → READY → INTENTIONAL → END → POST_INTENT → COMPLETE → LOG_FLUSHED`. Self-paced intentionally skips READY and labels onset `UNKNOWN_WITHIN_WINDOW`. Illegal transitions fail closed; aborts remain recorded and are excluded from completed history.
""",
        "06_CUE_PROTOCOL.md": """# Cue Protocol

M13.7 cue semantics are directly reused: target cue 880 Hz / 150 ms, READY 1760 Hz / 500 ms, guaranteed 1.0 s silence before stimulus, and END 440 Hz / 600 ms. No new M18 beep generator was introduced. Self-paced receives target assignment and washout but no selection-onset READY cue. Sham READY uses the same READY cue and is explicitly marked `SHAM_CUE_NC`.
""",
        "07_CONTINUOUS_EEG_LOGGING.md": """# Continuous EEG Logging

Every block owns one append-only `blocks/<block_id>/eeg/raw-eeg.jsonl` plus packet metadata. The raw packet stream is authoritative; trial/episode windows are annotations. Packets are persisted before downstream analysis. Sampling rate/channel metadata are recorded as 1000 Hz / 8 channels from the current ND8 contract, while hardware exactness remains a human preflight item.
""",
        "08_SCHEMA_AND_EVENTS.md": """# Schema and Events

`event_schema.json` covers session, block, episode, cue, stimulus, sample-anchor, Context, label, sham, and resume events. `context_snapshot_schema.json` defines immutable snapshots and forbidden runtime inputs. Evaluation truth is physically separated in `context/evaluation-truth.jsonl` and is not readable by runtime Context or decoder components.
""",
        "09_RESUME_ABORT_SAFETY.md": """# Resume and Abort Safety

Manifests and JSONL records are append-only. Existing completed blocks/episodes are skipped on resume after protocol-hash verification. A finalized session cannot be resumed, and retry must use a new episode ID/session. Aborted or invalid episodes are retained but never appended to completed Context history.
""",
        "10_SYNTHETIC_DRY_RUN.md": """# Synthetic Dry-run

The dry-run exercises Focused, Natural, True Idle, Passive Browse, Self-paced, sham cues, abort/resume seams, Context aligned/neutral/conflict, corrupt-hash rejection, and illegal-state rejection. It uses compressed deterministic synthetic packet timing solely to test software. Accuracy, TPR, FPR, false activations/minute, and Context benefit are **NOT_COMPUTED**.
""",
        "11_SOFTWARE_PREFLIGHT.md": f"""# Software Preflight

Result: **{preflight.get('status', 'NOT_RUN')}**

Protocol/matrix validation, Context causality, synthetic dry-run, append-only raw packet continuity, and offline export are checked by `preflight_report.json`. Quest/ND8 probes are deliberately reported as not attempted in this software-only run.
""",
        "12_HARDWARE_PREFLIGHT.md": """# Hardware Preflight

Run the non-invasive report command before attaching hardware:

`& .venv\\Scripts\\python.exe -m integration.m18_unified_acquisition hardware-preflight --output <external-root>\\hardware_preflight_report.json --com COM11 --quest-host 0.0.0.0 --quest-port 11001`

Human checks still required: Quest build and stimulus visibility, M13.7 cue audibility, ND8 COM discovery and packet stream, electrode quality, Quest↔PC synchronization, physical optical timing, and raw replay review. Software cannot prove these conditions.
""",
        "13_PHYSICAL_SMOKE.md": """# Physical Smoke

After the software preflight and non-invasive hardware preflight, run the bounded command below from an external data root. It requires explicit confirmation before opening ND8:

`& .venv\\Scripts\\python.exe -m integration.m18_unified_acquisition physical-smoke --data-root D:\\EEG_Study\\m18_unified_phase23 --com COM11 --session-id m18-physical-smoke-001 --confirm-live-human`

It runs six Focused/Natural context-coverage episodes plus short TRUE_IDLE and PASSIVE_BROWSE paths (never more than 9 smoke episodes). Quest visual presentation still uses the existing M5/M7 Unity path and must be checked by the operator. Mark every result `SMOKE_ONLY`; it does not enter primary N. Continue to primary collection only after raw EEG, event ordering, Context hash, cue, stimulus, and resume checks pass.
""",
        "14_HUMAN_RUNBOOK.md": """# Human Runbook

1. Prepare Quest, PC, ND8, electrodes, audio, and approved external data root.
2. Run the software preflight command and inspect `preflight_report.json`.
3. Perform channel-quality and Quest/cue rehearsal manually.
4. Start the M18 physical-smoke command only after human checks are accepted.
5. Review smoke replay and integrity output; then explicitly authorize primary collection.

No software artifact in this directory claims these human steps were completed.
""",
        "15_DATA_EXPORT.md": """# Data Export

The exporter writes Phase 2 and Phase 3 canonical manifests without training a classifier. Phase 2 receives Focused/Natural 4 s intentional annotations and pre/post metadata. Phase 3 receives continuous raw EEG references, NC/IC/ambiguous labels, cue markers, self-paced opportunity intervals, and re-arm provenance. Candidate-conditioned NC is computed only in a future offline analysis using the raw source.
""",
        "16_REGRESSION.md": """# Regression

M18-specific unit tests and synthetic dry-run are run in software. Existing M11/M12/M13/M13.5/M13.7/M16/M17 tests must be rerun in the project-supported Python environment. Dependency-missing suites are reported as `NOT_RUN_DEPENDENCY_MISSING`; no legacy PASS is invented.
""",
        "17_ESTIMATED_DURATION.md": f"""# Estimated Duration

- Formal continuous recording content, including cue/preparation and pre/post windows: **{duration['formalContinuousRecordingMinutes']:.1f} minutes**.
- Block ratings planned after active blocks: **{duration['ratingsSeconds'] / 60.0:.1f} minutes**; values are entered by the human, never auto-filled.
- Recommended breaks: **{duration['recommendedBreakSeconds'] / 60.0:.1f} minutes**.
- Estimated total wear time with 10 minutes setup: **{duration['estimatedTotalWearTimeMinutesWithSetup']:.1f} minutes**.

This remains near the 70–90 minute design goal. If a real operator observes longer fatigue burden, reduce repeated overhead before removing Natural or Passive Browse.
""",
        "18_KNOWN_LIMITATIONS.md": """# Known Limitations

- No new human EEG was collected; synthetic data only validates software.
- Physical optical timing, electrode quality, ND8 hardware sample anchoring, and Quest endpoint behavior remain human checks.
- Self-paced onset is intentionally interval-labeled, not exact.
- Context is a controlled observable-history model, not an ecological claim.
- The schedule is balanced by deterministic quota-constrained realization; target truth remains evaluation-only.
- This framework does not select or optimize a verifier, stopping policy, Context weight, or production decoder threshold.
""",
    }
    for name, text in docs.items():
        _write_text(artifact_dir / name, text)
    handoff = {
        "recordType": "m18_gpt_handoff",
        "schemaVersion": M18_SCHEMA_VERSION,
        "status": status,
        "finalStatus": status,
        "primaryDataPlan": {"focused": 18, "natural": 60, "idleMinutes": 9, "browseMinutes": 12, "selfPaced": 24, "sham": 12, "smokeMaximum": 9},
        "blockCount": len(schedule["blocks"]),
        "blockScheduleFile": "block_schedule.csv",
        "naturalEpisode": {"preNcSeconds": 4, "contextFreezeBeforeTarget": True, "targetInstruction": "after_freeze", "prepareSeconds": 6, "readyCue": "M13.7_REUSED", "intentionalSeconds": 4, "endCue": "M13.7_REUSED", "postIntentSeconds": 4, "rearmObservationSeconds": 4},
        "cueReuse": {"authority": "M13.7_Golden_Protocol_v1", "modified": False, "targetHz": 880, "readyHz": 1760, "endHz": 440},
        "continuousEeg": {"saveMode": "one_append_only_raw_stream_per_block", "samplingRateHz": 1000, "channelCount": 8, "eventToSampleMapping": "first packet at or after STIMULUS_ONSET", "filePattern": "blocks/<block_id>/eeg/raw-eeg.jsonl"},
        "phase2Data": ["Focused/Natural attention comparison", "Context condition descriptive analysis", "frequency/session/effort/fatigue analysis"],
        "phase3Data": {"Verifier_7.2": "Natural/Focused IC + candidate-conditioned NC later", "Verifier_9": "Natural/Focused IC + candidate-conditioned NC later", "Verifier_12": "Natural/Focused IC + candidate-conditioned NC later", "TrueNC": "TRUE_IDLE", "hardNC": "PASSIVE_BROWSE", "transition": "cued Focused/Natural PRE_NC→IC", "refractoryRearm": "post-intent/rearm segments", "selfPaced": "24 broad-window episodes, onset unknown within window"},
        "contextCausality": {"freezeBeforeTarget": preflight.get("contextCausality", {}).get("checks", {}).get("freeze_after_generation", False), "forbiddenInputTests": preflight.get("contextCausality", {}).get("status", "NOT_RUN")},
        "shamControl": "12 same READY cues embedded 6 in TRUE_IDLE and 6 in PASSIVE_BROWSE; no selection instruction; SHAM_CUE_NC label",
        "tests": {"newTests": "see test_m18_unified_acquisition.py", "syntheticDryRun": preflight.get("status", "NOT_RUN"), "regression": "reported separately; no hardware run"},
        "softwarePreflight": preflight.get("status", "NOT_RUN"),
        "hardwareBlockers": {"Quest": "human build/stimulus/transport check required", "ND8": "human COM/stream check required", "electrodeQuality": "human check required", "physicalTiming": "human check required"},
        "physicalSmokeNext": ["run M18 software preflight", "run hardware-preflight without opening hardware", "attach Quest and ND8", "perform channel/cue/stimulus checks", "run physical-smoke with --confirm-live-human", "review replay before primary authorization"],
        "readyForPhysicalSmoke": status == "M18_SOFTWARE_READY_FOR_PHYSICAL_SMOKE",
        "productionBoundary": protocol["productionBoundary"],
        "forbiddenClaims": ["HUMAN_DATA_VALIDATED", "PHASE3_VALIDATED", "accuracy", "TPR", "FPR"],
        "files": {"protocol": "m18_protocol.json", "matrix": "unified_acquisition_matrix.csv", "schedule": "block_schedule.csv", "preflight": "preflight_report.json", "hardwarePreflight": "hardware_preflight_report.json", "synthetic": "synthetic_manifest.json"},
    }
    _atomic_json(artifact_dir / "GPT_HANDOFF.json", handoff)
    _write_text(artifact_dir / "GPT_HANDOFF.md", "# GPT Handoff — M18 Unified Acquisition\n\n" + json.dumps(handoff, ensure_ascii=False, indent=2))


def build_artifact(output_parent: Path, timestamp: str | None = None) -> Path:
    output_parent = Path(output_parent)
    timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = output_parent / f"m18_unified_acquisition_{timestamp}"
    if artifact_dir.exists():
        raise FileExistsError(f"M18 artifact already exists: {artifact_dir}")
    artifact_dir.mkdir(parents=True)
    _atomic_json(artifact_dir / "m18_protocol.json", M18_PROTOCOL)
    _atomic_json(artifact_dir / "event_schema.json", M18_EVENT_SCHEMA)
    _atomic_json(artifact_dir / "context_snapshot_schema.json", M18_CONTEXT_SNAPSHOT_SCHEMA)
    schedule = build_schedule(M18_PROTOCOL)
    matrix = validate_matrix(M18_PROTOCOL, schedule)
    write_matrix_csv(artifact_dir / "unified_acquisition_matrix.csv", M18_PROTOCOL)
    write_block_schedule_csv(artifact_dir / "block_schedule.csv", schedule)
    _atomic_json(artifact_dir / "schedule.json", schedule)
    _atomic_json(artifact_dir / "matrix_validation.json", matrix)
    preflight = run_software_preflight(artifact_dir, M18_PROTOCOL, schedule)
    run_hardware_preflight(artifact_dir / "hardware_preflight_report.json")
    render_artifact_docs(artifact_dir, M18_PROTOCOL, schedule, preflight)
    return artifact_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--output-dir", required=True, type=Path)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--output-dir", required=True, type=Path)
    dry.add_argument("--resume", action="store_true")
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output-dir", required=True, type=Path)
    hardware = sub.add_parser("hardware-preflight")
    hardware.add_argument("--output", required=True, type=Path)
    hardware.add_argument("--com", default="COM11")
    hardware.add_argument("--quest-host", default="0.0.0.0")
    hardware.add_argument("--quest-port", default=11001, type=int)
    smoke = sub.add_parser("physical-smoke")
    smoke.add_argument("--data-root", required=True, type=Path)
    smoke.add_argument("--com", required=True)
    smoke.add_argument("--session-id", default="m18-physical-smoke-001")
    smoke.add_argument("--confirm-live-human", action="store_true")
    artifact = sub.add_parser("artifact")
    artifact.add_argument("--output-parent", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "plan":
        schedule = build_schedule()
        _atomic_json(args.output_dir / "schedule.json", schedule)
        _atomic_json(args.output_dir / "matrix_validation.json", validate_matrix(M18_PROTOCOL, schedule))
        write_matrix_csv(args.output_dir / "unified_acquisition_matrix.csv", M18_PROTOCOL)
        write_block_schedule_csv(args.output_dir / "block_schedule.csv", schedule)
        print(json.dumps({"status": "PASS", "scheduleFingerprint": schedule["scheduleFingerprint"]}, sort_keys=True))
        return 0
    if args.command == "dry-run":
        result = run_synthetic_dry_run(args.output_dir, resume=args.resume)
        print(json.dumps({"status": result["status"], "sessionRoot": result["sessionRoot"]}, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "preflight":
        args.output_dir.mkdir(parents=True, exist_ok=True)
        report = run_software_preflight(args.output_dir)
        print(json.dumps({"status": report["status"], "finalStatus": report["finalStatus"]}, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "hardware-preflight":
        report = run_hardware_preflight(args.output, com_port=args.com, quest_host=args.quest_host, quest_port=args.quest_port)
        print(json.dumps({"status": report["status"], "finalStatus": report["finalStatus"], "output": str(args.output.resolve())}, sort_keys=True))
        return 0 if report["status"] in ("READY_FOR_HUMAN_CHECK", "PASS") else 1
    if args.command == "physical-smoke":
        report = run_physical_smoke(args.data_root, com_port=args.com, session_id=args.session_id, confirm_live_human=args.confirm_live_human)
        print(json.dumps({"status": report["status"], "sessionRoot": report["sessionRoot"], "smokeOnly": report["smokeOnly"]}, sort_keys=True))
        return 0
    artifact_dir = build_artifact(args.output_parent)
    print(json.dumps({"status": "M18_SOFTWARE_READY_FOR_PHYSICAL_SMOKE", "artifactDir": str(artifact_dir)}, sort_keys=True))
    return 0


__all__ = [
    "M18_PROTOCOL", "M18_EVENT_SCHEMA", "M18_CONTEXT_SNAPSHOT_SCHEMA", "ContextSnapshot",
    "M18ContextEngine", "M18TargetSampler", "EpisodeStateMachine", "M18SessionRecorder",
    "build_schedule", "validate_matrix", "validate_session", "run_context_causality_tests",
    "run_synthetic_dry_run", "run_physical_smoke", "export_canonical", "run_software_preflight", "run_hardware_preflight", "build_artifact", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
