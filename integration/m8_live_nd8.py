"""M8.2b live-ND8 orchestration built from the frozen M6 runtime boundaries.

This module owns session/trial ordering only.  ND8 acquisition, channel admission,
NumPy FBCCA and 2-Consecutive stabilization stay in their existing M6 modules; the
Quest remains the only authority that resolves a class index to a TargetId.
"""
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid

try:
    import winsound
except ImportError:  # pragma: no cover - exercised on non-Windows development hosts.
    winsound = None

import numpy as np

from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from eeg.decoder.formal_online import (
    RAIL_FRACTION_LIMIT,
    RAIL_VALUE,
    channel_admission,
    synthetic_warmup,
)
from eeg.decoder.live_online import LiveOnlineController
from eeg.decoder.pseudo_online import DecoderBackend
from eeg.sample_association.jsonl import AppendOnlyJsonl
from integration.m8_selection_orchestration import (
    M8LiveTrialBridge,
    M8SelectionOrchestrator,
    QuestSelectionTcpServer,
)


M8_LIVE_TRIAL_SPECS = (
    (0, 0, 7.2, "target_left"),
    (1, 1, 9.0, "target_center"),
    (2, 2, 12.0, "target_right"),
)
M6_PREFLIGHT_SAMPLES = 60000
M8_FIRST_MEASUREMENT_PREPARATION_SECONDS = 10
M8_SUBSEQUENT_MEASUREMENT_PREPARATION_SECONDS = 5
M8_LIVE_EVIDENCE_FILES = (
    "raw-eeg.jsonl",
    "packet-metadata.jsonl",
    "channel-admission.json",
    "synthetic-warmup.json",
    "predictions.jsonl",
    "decisions.jsonl",
    "m8-orchestration.jsonl",
    "m8-trial-results.jsonl",
    "m8-session-events.jsonl",
)


class M8WindowsAudibleCue:
    """Best-effort Windows cues; audio failure must never change trial state."""

    def __init__(self, beep=None, output=print):
        self._beep = winsound.Beep if beep is None and winsound is not None else beep
        self._output = output
        self._emitted_cues = []
        self._warnings = []

    def _emit(self, name, frequency, duration_ms):
        self._emitted_cues.append(name)
        if self._beep is None:
            self._warn("{} unavailable on this host".format(name))
            return
        try:
            self._beep(frequency, duration_ms)
        except (OSError, RuntimeError) as error:
            self._warn("{} failed: {}".format(name, error))

    def _warn(self, message):
        self._warnings.append(message)
        self._output("WARNING: M8 audible cue {}".format(message))

    def preparation_started(self):
        self._emit("preparation_started", 440, 140)

    def countdown_tick(self, remaining):
        self._emit("countdown_{}".format(int(remaining)), 700, 100)

    def observation_started(self):
        self._emit("observation_started", 1200, 350)

    def trial_ended(self):
        self._emit("trial_ended", 320, 450)

    def summary(self):
        return {
            "available": self._beep is not None,
            "emittedCueCount": len(self._emitted_cues),
            "warningCount": len(self._warnings),
        }


def m8_preparation_seconds_for_measurement(measurement_number):
    """Return the frozen operator preparation duration for one session measurement."""
    return (M8_FIRST_MEASUREMENT_PREPARATION_SECONDS if int(measurement_number) == 1
            else M8_SUBSEQUENT_MEASUREMENT_PREPARATION_SECONDS)


def run_m8_preparation_countdown(seconds, cue, sleep=time.sleep, output=print, on_cue_failure=None):
    """Emit the 10 s first-measurement or 5 s later-measurement preparation cues."""
    seconds = int(seconds)

    def emit(method, *args):
        try:
            getattr(cue, method)(*args)
        except Exception as error:  # cue is advisory and cannot abort a trial.
            message = "{} failed: {}".format(method, error)
            if on_cue_failure is not None:
                on_cue_failure(message)
            else:
                output("WARNING: M8 audible cue {}".format(message))

    if seconds <= 0:
        return
    emit("preparation_started")
    for remaining in range(seconds, 0, -1):
        if remaining in {seconds, 5}:
            output("Preparation countdown: {} s remaining".format(remaining), flush=True)
        if seconds > M8_SUBSEQUENT_MEASUREMENT_PREPARATION_SECONDS and remaining == 5:
            emit("countdown_tick", remaining)
        sleep(1.0)


class M8LiveNd8PreflightError(RuntimeError):
    """A required live-hardware condition was not met; no trial may start."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def validate_vendor_cpython39_runtime(version_info=None, implementation=None, architecture=None, importer=None):
    """Validate the documented external CPython 3.9 ND8 runtime without opening hardware."""
    version_info = sys.version_info if version_info is None else version_info
    implementation = platform.python_implementation().lower() if implementation is None else implementation.lower()
    architecture = platform.architecture()[0] if architecture is None else architecture
    importer = importlib.import_module if importer is None else importer
    if tuple(version_info[:2]) != (3, 9) or implementation != "cpython" or architecture != "64bit":
        raise M8LiveNd8PreflightError(
            "live-nd8 requires the verified external Windows x64 CPython 3.9 Neurodance runtime"
        )
    try:
        importer("neuro_dance.core")
        importer("neuro_dance.nd_device_process")
    except (ImportError, OSError) as error:
        raise M8LiveNd8PreflightError("Neurodance SDK import/runtime is unavailable: {}".format(error)) from error
    return {
        "pythonVersion": ".".join(str(value) for value in version_info[:3]),
        "implementation": implementation,
        "architecture": architecture,
        "executable": sys.executable,
        "vendorSdkImports": ["neuro_dance.core", "neuro_dance.nd_device_process"],
    }


def build_m8_live_trial_plan(session_id):
    """Fixed three-trial engineering smoke plan; expected labels are post-hoc evidence only."""
    if not session_id:
        raise ValueError("session_id is required")
    trials = []
    for index, (slot, class_index, frequency, label) in enumerate(M8_LIVE_TRIAL_SPECS, 1):
        trials.append({
            "sessionId": session_id,
            "trialId": "{}-trial-{:03d}".format(session_id, index),
            "selectionId": "{}-selection-{:03d}".format(session_id, index),
            "trialIndex": index,
            "slot": slot,
            "expectedClassIndex": class_index,
            "expectedLabel": label,
            "frequencyHz": frequency,
            "operatorPrompt": "Trial {}: look at slot {} / {} Hz".format(index, slot, frequency),
        })
    return trials


def build_m8_free_trial_plan(session_id, max_trials=3):
    """Build a plan with no expected class; each final decoder class is user-selected."""
    trials = limit_m8_live_trial_plan(build_m8_live_trial_plan(session_id), max_trials)
    free_trials = []
    for trial in trials:
        free_trial = dict(trial)
        free_trial.update({
            "slot": None,
            "expectedClassIndex": None,
            "expectedLabel": None,
            "frequencyHz": None,
            "operatorPrompt": "Trial {}: choose any available green slot (0/1/2)".format(trial["trialIndex"]),
        })
        free_trials.append(free_trial)
    return free_trials


def limit_m8_live_trial_plan(trials, max_trials):
    """Select an explicit prefix without altering the frozen trial definitions."""
    max_trials = int(max_trials)
    if max_trials < 1 or max_trials > len(trials):
        raise ValueError("max_trials must be between 1 and {}".format(len(trials)))
    return list(trials[:max_trials])


def _new_session_id(prefix):
    return "{}-{}-{}".format(prefix, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _create_session(data_root, session_prefix, dry_run=False, max_trials=3, selection_plan="fixed"):
    if selection_plan not in ("fixed", "free"):
        raise ValueError("selection_plan must be fixed or free")
    session_id = _new_session_id(session_prefix)
    root = Path(data_root) / session_id
    root.mkdir(parents=True, exist_ok=False)
    if selection_plan == "free":
        trials = build_m8_free_trial_plan(session_id, max_trials)
    else:
        trials = limit_m8_live_trial_plan(build_m8_live_trial_plan(session_id), max_trials)
    plan_mode = "m8_free_selection" if selection_plan == "free" else (
        "m8_final_single_trial" if len(trials) == 1 else "m8_2b_engineering_smoke"
    )
    plan = {"sessionId": session_id, "planMode": plan_mode, "trials": trials}
    manifest = {
        "recordType": "m8_2b_live_nd8_session",
        "sessionId": session_id,
        "createdUtc": _utc_now(),
        "mode": "m8_2b_live_nd8",
        "status": "prepared",
        "gitCommit": _git_commit(),
        "selectionPlan": selection_plan,
        "plannedTrialCount": len(trials),
        "trialOrder": [item["expectedClassIndex"] for item in trials],
        "plannedTrialIndices": [item["trialIndex"] for item in trials],
        "decoder": "numpy_fbcca",
        "frequenciesHz": [7.2, 9.0, 12.0],
        "guardSeconds": 0.5,
        "windowSeconds": 1.5,
        "stepSeconds": 0.2,
        "stabilizer": "2-Consecutive",
        "sampleRateHz": 1000,
        "groundTruthLeakage": False,
        "expectedClassUse": "post_hoc_evidence_only" if selection_plan == "fixed" else "not_applicable_free_selection",
        "nd8Started": False,
        "rawEegFile": "raw-eeg.jsonl",
        "packetMetadataFile": "packet-metadata.jsonl",
        "m8OrchestrationFile": "m8-orchestration.jsonl",
        "trialResultsFile": "m8-trial-results.jsonl",
        "files": list(M8_LIVE_EVIDENCE_FILES),
        "hardwareTimingVerified": False,
        "physicalOpticalTimingVerified": False,
        "hardwareSampleAnchorVerified": False,
        "dryRun": bool(dry_run),
    }
    _write_json(root / "manifest.json", manifest)
    _write_json(root / "m8-trial-plan.json", plan)
    return root, manifest, plan


def create_m8_live_dry_run(data_root, session_prefix="m8_2b-live", max_trials=3, selection_plan="fixed"):
    """Create a unique, evidence-shaped no-hardware dry-run session for CI/preflight review."""
    root, manifest, _ = _create_session(
        data_root, session_prefix, dry_run=True, max_trials=max_trials, selection_plan=selection_plan)
    manifest.update({"status": "dry_run_passed", "dryRunReason": "no_vendor_runtime_or_nd8_access_requested"})
    _write_json(root / "manifest.json", manifest)
    return root


class M8LiveTrialCoordinator:
    """Small bridge that never exposes expected target data to the M6 decoder controller."""
    def __init__(self, bridge):
        self.bridge = bridge
        self.active_trial = None

    def start_trial(self, trial, estimated_global_sample_index):
        if self.active_trial is not None:
            raise RuntimeError("another M8 live trial is already active")
        association = {
            "sessionId": trial["sessionId"],
            "trialId": trial["trialId"],
            "estimatedGlobalSampleIndex": int(estimated_global_sample_index),
            "startSampleSource": "nd8_packet_boundary_software",
        }
        if not self.bridge.start_trial(trial["selectionId"], association):
            return False
        self.active_trial = dict(trial)
        return True

    def finish_trial(self, reason="m8_trial_window_complete"):
        if self.active_trial is None:
            raise RuntimeError("no M8 live trial is active")
        trial, self.active_trial = self.active_trial, None
        result = self.bridge.stop_trial(reason)
        if result is None:
            raise RuntimeError("live controller returned no trial result")
        return {"trial": trial, **result}

    def abort_trial(self, reason):
        if self.active_trial is None:
            raise RuntimeError("no M8 live trial is active")
        trial, self.active_trial = self.active_trial, None
        result = self.bridge.abort_trial(trial["trialId"], reason)
        return {"trial": trial, **result}


class M8LiveNd8Session:
    """One external-CPython command for frozen M8.2b live-ND8 trial plans."""
    def __init__(self, args, adapter_factory=Nd8SerialAdapter, transport_factory=QuestSelectionTcpServer,
                 controller_factory=LiveOnlineController, runtime_validator=validate_vendor_cpython39_runtime,
                 countdown=None, sleep=time.sleep, monotonic=time.monotonic, cue=None):
        self.args = args
        self.adapter_factory = adapter_factory
        self.transport_factory = transport_factory
        self.controller_factory = controller_factory
        self.runtime_validator = runtime_validator
        self.countdown = countdown
        self.sleep = sleep
        self.monotonic = monotonic
        self.cue = M8WindowsAudibleCue() if cue is None else cue
        self._cue_warnings = []
        self._lock = threading.RLock()
        self._preflight_packets = []
        self._continuity = []
        self._packet_shapes = []
        self._latest_start_sample = None
        self._last_packet_time = None
        self._runtime_failure = None
        self._selected_channels = []
        self._controller = None

    def _record_cue_warning(self, message):
        self._cue_warnings.append(message)
        print("WARNING: M8 audible cue {}".format(message), flush=True)

    def _emit_cue(self, method, *args):
        try:
            getattr(self.cue, method)(*args)
        except Exception as error:  # injected/custom cue failures remain non-fatal.
            self._record_cue_warning("{} failed: {}".format(method, error))

    def _cue_summary(self):
        try:
            summary = dict(self.cue.summary())
        except Exception as error:
            self._record_cue_warning("summary failed: {}".format(error))
            summary = {}
        summary["warningCount"] = int(summary.get("warningCount", 0)) + len(self._cue_warnings)
        return summary

    def _event(self, event_type, **values):
        self.session_events.append({"recordType": "m8_live_nd8_event", "eventType": event_type,
                                    "sessionId": self.session_id, "createdUtc": _utc_now(), **values})

    def _save_manifest(self):
        _write_json(self.root / "manifest.json", self.manifest)

    def _record_packet(self, packet, continuity):
        values = np.asarray(packet.samples, dtype=float)
        with self._lock:
            self._packet_shapes.append(tuple(values.shape))
            self._continuity.append(continuity.status)
            self._last_packet_time = self.monotonic()
            self._latest_start_sample = int(continuity.cumulative_first_sample_index) + values.shape[1]
            if sum(item.shape[1] for item in self._preflight_packets) < M6_PREFLIGHT_SAMPLES:
                self._preflight_packets.append(values.copy())
            selected = list(self._selected_channels)
            controller = self._controller
        if selected:
            if continuity.status not in ("continuous", "anomaly"):
                self._runtime_failure = "nd8_continuity_failure"
                return
            selected_values = values[selected]
            if not np.isfinite(selected_values).all():
                self._runtime_failure = "frozen_channel_nonfinite"
                return
            if any(float(np.mean(np.isclose(np.abs(values[channel]), RAIL_VALUE, atol=1.0))) >= RAIL_FRACTION_LIMIT
                   for channel in selected):
                self._runtime_failure = "frozen_channel_near_rail"
                return
        if controller is not None:
            try:
                controller.ingest_packet(packet.to_metadata(), continuity, values)
            except Exception as error:  # callback must make the active trial fail closed
                self._runtime_failure = "decoder_exception:{}".format(error)

    def _preflight_sample_count(self):
        with self._lock:
            return sum(item.shape[1] for item in self._preflight_packets)

    def _preflight_ready(self):
        with self._lock:
            shapes_ok = bool(self._packet_shapes) and all(
                len(shape) == 2 and shape[0] == 8 and shape[1] > 0 for shape in self._packet_shapes[-5:]
            )
            return shapes_ok and self._preflight_sample_count() >= M6_PREFLIGHT_SAMPLES

    def _ensure_packet_liveness(self):
        with self._lock:
            last_packet_time = self._last_packet_time
        if last_packet_time is None or self.monotonic() - last_packet_time > self.args.packet_stall_seconds:
            return "nd8_packet_stall"
        if self.adapter.callback_errors:
            return "nd8_callback_error:{}".format(self.adapter.callback_errors[-1])
        return self._runtime_failure

    def _run_preflight(self):
        self.adapter.open_port()
        self.adapter.start_streaming()
        self.manifest["nd8Started"] = True
        self._event("nd8_streaming_started", comPort=self.args.com, hostMacReady=self.adapter.host_mac_ready)
        deadline = self.monotonic() + self.args.preflight_timeout_seconds
        while self.monotonic() < deadline:
            failure = self._ensure_packet_liveness() if self._last_packet_time is not None else None
            if failure:
                raise M8LiveNd8PreflightError(failure)
            if self._preflight_ready():
                break
            self.sleep(0.05)
        else:
            raise M8LiveNd8PreflightError("ND8/channel preflight timed out")
        with self._lock:
            samples = np.concatenate(self._preflight_packets, axis=1)[:, :M6_PREFLIGHT_SAMPLES]
            continuity = list(self._continuity)
        admission = channel_admission(samples, continuity, _git_commit(), _utc_now())
        _write_json(self.root / "channel-admission.json", admission)
        if admission["verdict"] != "READY":
            raise M8LiveNd8PreflightError("channel admission failed")
        self._selected_channels = list(admission["selectedChannels"])
        warmup = synthetic_warmup(self._selected_channels)
        _write_json(self.root / "synthetic-warmup.json", warmup)
        self._controller = self.controller_factory(
            DecoderBackend("numpy_fbcca"), self._selected_channels,
            AppendOnlyJsonl(self.root / "predictions.jsonl"), AppendOnlyJsonl(self.root / "decisions.jsonl"),
        )
        self.manifest.update({
            "status": "preflight_passed",
            "selectedChannels": self._selected_channels,
            "channelAdmission": admission,
            "syntheticWarmup": warmup,
            "nd8HostMacReady": self.adapter.host_mac_ready,
            "nd8HostMacSuffix": self.adapter.host_mac_suffix,
            "liveController": "LiveOnlineController",
            "m6EvidenceReference": {"sessionRoot": str(self.root), "rawEegFile": "raw-eeg.jsonl",
                                    "packetMetadataFile": "packet-metadata.jsonl"},
        })
        self._save_manifest()
        self._event("preflight_passed", selectedChannels=self._selected_channels)

    def _run_trial(self, coordinator, trial, measurement_number, before_selection_open=None):
        print(trial["operatorPrompt"], flush=True)
        preparation_seconds = m8_preparation_seconds_for_measurement(measurement_number)
        self._event("trial_preparation_started", trialId=trial["trialId"], selectionId=trial["selectionId"],
                    expectedClassIndex=trial["expectedClassIndex"], slot=trial["slot"], frequencyHz=trial["frequencyHz"],
                    measurementNumber=measurement_number, preparationSeconds=preparation_seconds)
        if self.countdown is None:
            run_m8_preparation_countdown(
                preparation_seconds,
                self.cue,
                sleep=self.sleep,
                on_cue_failure=self._record_cue_warning,
            )
        else:
            self._emit_cue("preparation_started")
            if measurement_number == 1:
                self._emit_cue("countdown_tick", 5)
            self.countdown(preparation_seconds)
        if before_selection_open is not None:
            before_selection_open()
        failure = self._ensure_packet_liveness()
        if failure:
            raise M8LiveNd8PreflightError(failure)
        with self._lock:
            start_sample = self._latest_start_sample
        if start_sample is None:
            raise M8LiveNd8PreflightError("no ND8 packet boundary was available for trial start")
        if not coordinator.start_trial(trial, start_sample):
            self.trial_results.append({
                "recordType": "m8_live_nd8_trial_result",
                "sessionId": self.session_id,
                "trialId": trial["trialId"],
                "selectionId": trial["selectionId"],
                "expectedClassIndex": trial["expectedClassIndex"],
                "expectedSlot": trial["slot"],
                "expectedFrequencyHz": trial["frequencyHz"],
                "expectedLabel": trial["expectedLabel"],
                "status": "failed",
                "failureReason": "selection_open_rejected",
                "m6FinalDecision": None,
                "finalClassIndex": None,
                "m8Selection": {"status": "selection_open_rejected"},
                "m6Nd8EvidenceReference": self.manifest["m6EvidenceReference"],
            })
            raise M8LiveNd8PreflightError("Quest selection_open was rejected")
        self._event("trial_started_after_quest_open_ack", trialId=trial["trialId"], selectionId=trial["selectionId"],
                    estimatedGlobalSampleIndex=start_sample)
        deadline = self.monotonic() + self.args.trial_window_seconds
        self._emit_cue("observation_started")
        while self.monotonic() < deadline:
            failure = self._ensure_packet_liveness()
            if failure:
                aborted = coordinator.abort_trial(failure)
                self._record_trial(aborted, "aborted", failure)
                self._emit_cue("trial_ended")
                raise M8LiveNd8PreflightError(failure)
            self.sleep(0.025)
        completed = coordinator.finish_trial()
        self._emit_cue("trial_ended")
        m8_result = completed["m8Selection"]
        status = m8_result.get("status")
        if status != "quest_accepted":
            self._record_trial(completed, "failed", status)
            raise M8LiveNd8PreflightError("terminal M8 result: {}".format(status))
        self._record_trial(completed, "accepted", None)
        return completed

    def _record_trial(self, completed, status, failure_reason):
        trial = completed["trial"]
        decoder = completed.get("decoderResult") or {
            key: completed.get(key) for key in (
                "sessionId", "trialId", "decisionMade", "finalDecisionLabel", "decisionPredictionIndex",
                "decisionRelativeTimeSeconds", "stabilizer", "reason",
            )
        }
        m8_selection = completed.get("m8Selection")
        self.trial_results.append({
            "recordType": "m8_live_nd8_trial_result",
            "sessionId": self.session_id,
            "trialId": trial["trialId"],
            "selectionId": trial["selectionId"],
            "expectedClassIndex": trial["expectedClassIndex"],
            "expectedSlot": trial["slot"],
            "expectedFrequencyHz": trial["frequencyHz"],
            "expectedLabel": trial["expectedLabel"],
            "status": status,
            "failureReason": failure_reason,
            "m6FinalDecision": decoder,
            "finalClassIndex": m8_selection.get("predictedClassIndex") if isinstance(m8_selection, dict) else None,
            "m8Selection": m8_selection,
            "m6Nd8EvidenceReference": self.manifest["m6EvidenceReference"],
        })

    def run(self):
        self.root, self.manifest, self.plan = _create_session(
            self.args.data_root,
            self.args.session_prefix,
            dry_run=bool(self.args.dry_run),
            max_trials=getattr(self.args, "max_trials", 3),
            selection_plan=getattr(self.args, "selection_plan", "fixed"),
        )
        self.session_id = self.manifest["sessionId"]
        self.session_events = AppendOnlyJsonl(self.root / "m8-session-events.jsonl")
        self.trial_results = AppendOnlyJsonl(self.root / "m8-trial-results.jsonl")
        self.orchestration_log = AppendOnlyJsonl(self.root / "m8-orchestration.jsonl")
        self._event("session_created", dataRoot=str(Path(self.args.data_root)))
        print("M8.2b session={}".format(self.root), flush=True)
        if self.args.dry_run:
            self.manifest.update({"status": "dry_run_passed", "nd8Started": False,
                                  "dryRunReason": "no_vendor_runtime_or_nd8_access_requested"})
            self._save_manifest()
            print("M8.2b dry-run prepared session={}".format(self.root), flush=True)
            return 0, self.root

        self.adapter = None
        status, failure = "incomplete", None
        try:
            self.manifest["vendorRuntime"] = self.runtime_validator()
            self._save_manifest()
            self.adapter = self.adapter_factory(
                self.args.com,
                metadata_log=AppendOnlyJsonl(self.root / "packet-metadata.jsonl"),
                raw_packet_log=AppendOnlyJsonl(self.root / "raw-eeg.jsonl"),
                live_packet_observer=self._record_packet,
            )
            self._run_preflight()
            if self.args.preflight_only:
                status = "preflight_passed"
                return 0, self.root
            with self.transport_factory(self.args.host, self.args.port, self.args.accept_timeout_seconds,
                                        self.args.ack_timeout_seconds) as transport:
                orchestrator = M8SelectionOrchestrator(
                    transport,
                    lambda record: self.orchestration_log.append({"m8SessionId": self.session_id, **record}),
                )
                coordinator = M8LiveTrialCoordinator(M8LiveTrialBridge(self._controller, orchestrator))
                print("M8.2b listener={} port={}; preflight passed; planned_trials={}".format(
                    self.args.host, transport.port, len(self.plan["trials"])), flush=True)
                for measurement_number, trial in enumerate(self.plan["trials"], 1):
                    completed = self._run_trial(
                        coordinator,
                        trial,
                        measurement_number,
                    )
            status = "completed"
            return 0, self.root
        except (M8LiveNd8PreflightError, RuntimeError, OSError, ValueError) as error:
            failure = str(error)
            self._event("session_failed", reason=failure)
            print("M8.2b live-nd8 failed closed: {}".format(failure), flush=True)
            return 2, self.root
        finally:
            if self.adapter is not None:
                if self.adapter.state.value == "streaming":
                    self.adapter.stop()
                self.adapter.close()
                self.manifest["callbackErrors"] = list(self.adapter.callback_errors)
                self.manifest["observedPacketCount"] = len(self.adapter.timeline.packets)
            self.manifest["audibleCue"] = self._cue_summary()
            self.manifest.update({"status": status, "failureReason": failure, "endedUtc": _utc_now()})
            self._save_manifest()


def run_live_nd8(args):
    """CLI entry used only after the external CPython 3.9 runtime is selected."""
    return M8LiveNd8Session(args).run()
