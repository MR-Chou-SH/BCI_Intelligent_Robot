"""Opt-in active M13 controller for the existing live ND8/M8 callback seam.

The default M8 live controller remains unchanged. This controller only adds
the explicitly selected ``--m13-mode active`` path: each real NumPy FBCCA
window is converted through the frozen M12 fusion API, evaluated by the frozen
M13 policy, and returned to the existing M8 bridge for Quest submission.
The pre-EEG campaign uses a uniform context prior and records that provenance;
it must not be interpreted as a context-aware human experiment.
"""

import math
import threading
import time

import numpy as np

from eeg.decoder.pseudo_online import ReplayPacket, RollingEegBuffer, DEFAULT_PSEUDO_ONLINE_CONFIG
from integration.m11_context_prediction import ContextPrior
from integration.m12_context_eeg_fusion import fuse_fbcca_score_vector
from integration.m13_5_logging import M135SessionLogger
from integration.m13_5_runtime import M13_5_POLICY, build_default_active_candidates, mapping_public
from integration.m13_dynamic_stopping import DynamicStoppingInputError, DynamicStoppingPolicy, DynamicStoppingSnapshot, WINDOW_GRID_SECONDS
from integration.m13_m8_selection_integration import dynamic_stopping_decision_to_m8_final_decision


LIVE_SOURCE_TYPE = "real_nd8_floating_electrodes_no_human_eeg"
UNIFORM_CONTEXT_LOGICAL_IDS = ("block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04")


def uniform_pre_eeg_context_prior():
    """Return an explicit no-context M11-shaped prior for pre-EEG validation."""
    probabilities = tuple((logical_id, 0.25) for logical_id in UNIFORM_CONTEXT_LOGICAL_IDS)
    return ContextPrior(
        observable_history=(),
        available_logical_block_ids=UNIFORM_CONTEXT_LOGICAL_IDS,
        step_index=0,
        candidate_task_count=1,
        task_hypotheses=(),
        next_target_probabilities=probabilities,
        top_targets=UNIFORM_CONTEXT_LOGICAL_IDS,
        tie=True,
        entropy=math.log(4.0),
        terminal=False,
        valid=True,
    )


class M135LiveOnlineController:
    """Thread-safe active M13 controller with the same callback seam as M6."""

    accepts_selection_id = True

    def __init__(self, backend, selected_channels, prediction_log=None, decision_log=None,
                 config=None, session_root=None, session_id=None, software_commit="unavailable"):
        self.backend = backend
        self.selected_channels = tuple(selected_channels)
        self.config = config or DEFAULT_PSEUDO_ONLINE_CONFIG
        self.prediction_log = prediction_log
        self.decision_log = decision_log
        self.buffer = RollingEegBuffer()
        self._lock = threading.RLock()
        self.active = None
        self._seen_trials = set()
        self._last_packet_sequence = None
        self._generation = 0
        self._session_closed = False
        self.candidates = build_default_active_candidates()
        self.context_prior = uniform_pre_eeg_context_prior()
        if session_root is None or session_id is None:
            raise ValueError("active M13 live controller requires a session root and session ID")
        self.session_logger = M135SessionLogger(
            session_root,
            session_id,
            "active",
            LIVE_SOURCE_TYPE,
            software_commit,
            M13_5_POLICY,
            mapping_public(self.candidates),
        )

    @property
    def decision_ready(self):
        with self._lock:
            return bool(self.active is not None and self.active["policy"].decision is not None)

    def start_trial(self, association):
        session_id = association.get("sessionId")
        trial_id = association.get("trialId")
        selection_id = association.get("selectionId")
        if not session_id or not trial_id or not selection_id:
            return False
        identity = (str(session_id), str(trial_id))
        with self._lock:
            if self.active is not None or identity in self._seen_trials:
                return False
            self._seen_trials.add(identity)
            self._generation += 1
            start_sample = int(association["estimatedGlobalSampleIndex"])
            self.active = {
                "sessionId": str(session_id),
                "trialId": str(trial_id),
                "selectionId": str(selection_id),
                "startSample": start_sample,
                "nextStop": start_sample + self.config.onset_guard_samples + self.config.analysis_sample_count,
                "generation": self._generation,
                "policy": DynamicStoppingPolicy(),
                "predictions": [],
                "invalidReason": None,
            }
            state = self.active
        self.session_logger.append(
            "trial_started",
            trialId=state["trialId"],
            selectionId=state["selectionId"],
            openAccepted=True,
            mappingValid=True,
            mapping=list(mapping_public(self.candidates)),
            stateReset={
                "consecutiveCount": True,
                "candidateTarget": True,
                "previousDecision": True,
                "windowIndex": True,
                "previousEvidence": True,
            },
            m13Invoked=True,
            contextProvenance="uniform_no_context_pre_eeg",
        )
        return True

    def _window_record(self, state, evaluation, raw_scores, prediction_index, predicted_class, compute_duration_ns):
        public = evaluation.to_public_dict()
        fused = public.get("fusedEvidence", {})
        raw_top = public.get("eegTopLogicalBlockIds", [])
        fused_top = public.get("fusedTopLogicalBlockIds", [])
        values = sorted((float(value) for value in fused.values()), reverse=True)
        return {
            "sessionId": state["sessionId"],
            "trialId": state["trialId"],
            "selectionId": state["selectionId"],
            "predictionIndex": prediction_index,
            "predictedClass": predicted_class,
            "candidateScores": {str(index): float(value) for index, value in enumerate(raw_scores)},
            "rawEegEvidence": dict(evaluation.eeg_scores),
            "eegTopLogicalBlockIds": list(raw_top),
            "contextPrior": public.get("contextPrior", []),
            "fusedEvidence": fused,
            "fusedTopLogicalBlockIds": list(fused_top),
            "top1": values[0] if values else None,
            "top2": values[1] if len(values) > 1 else None,
            "margin": public.get("margin"),
            "thresholdPass": public.get("thresholdPass"),
            "marginPass": public.get("marginPass"),
            "eegConfirmPass": public.get("eegConfirmationPass"),
            "consecutiveCount": public.get("consecutiveCount"),
            "m13State": "early_stop" if public.get("stop") else public.get("reason"),
            "m13Evaluation": public,
            "computeDurationNs": compute_duration_ns,
            "sourceType": LIVE_SOURCE_TYPE,
        }

    def ingest_packet(self, packet_metadata, continuity, samples):
        packet = ReplayPacket(
            np.asarray(samples, dtype=float),
            int(continuity.cumulative_first_sample_index),
            int(packet_metadata.packet_sequence),
            int(packet_metadata.pc_receive_monotonic_ns),
            continuity.status,
        )
        with self._lock:
            if self._last_packet_sequence is not None and packet.packet_sequence <= self._last_packet_sequence:
                return []
            self._last_packet_sequence = packet.packet_sequence
            self.buffer.append(packet)
            state = self.active
            if state is None:
                return []
            if state["policy"].decision is not None:
                return []
            if continuity.status not in ("continuous", "anomaly"):
                state["invalidReason"] = "continuity_failure"
                return []
            jobs = []
            formal_horizon_samples = int(max(WINDOW_GRID_SECONDS) * self.config.input_sampling_rate_hz)
            while state["nextStop"] <= state["startSample"] + formal_horizon_samples and self.buffer.stop_sample >= state["nextStop"]:
                jobs.append((state["nextStop"] - self.config.analysis_sample_count, state["nextStop"], packet, state["generation"]))
                state["nextStop"] += 200
        emitted = []
        for start, stop, latest, generation in jobs:
            with self._lock:
                if self.active is not state or state["generation"] != generation:
                    break
                try:
                    data = self.buffer.window(start, stop)[list(self.selected_channels)]
                except ValueError:
                    continue
            begun = time.perf_counter_ns()
            predicted_index, raw_scores = self.backend.predict(data)
            compute_duration_ns = time.perf_counter_ns() - begun
            predicted_class = ("target_left", "target_center", "target_right")[predicted_index]
            window_index = len(state["predictions"])
            effective_seconds = (stop - state["startSample"]) / self.config.input_sampling_rate_hz
            try:
                fused = fuse_fbcca_score_vector(self.context_prior, self.candidates, raw_scores)
                snapshot = DynamicStoppingSnapshot.from_m12(
                    window_index,
                    fused,
                    analysis_window_seconds=self.config.analysis_duration_seconds,
                    effective_acquisition_seconds=effective_seconds,
                    provenance={
                        "source": "real_nd8_live_window",
                        "decoder": "numpy_fbcca",
                        "context": "uniform_no_context_pre_eeg",
                        "electrodes": "floating_no_human_eeg",
                        "packetSequence": str(latest.packet_sequence),
                    },
                )
                evaluation = state["policy"].observe(snapshot)
            except (ValueError, TypeError, DynamicStoppingInputError) as error:
                evaluation = state["policy"].reject_invalid(window_index, effective_seconds, "invalid_evidence")
                state["invalidReason"] = "invalid_evidence:{}".format(type(error).__name__)
            record = self._window_record(state, evaluation, raw_scores, window_index, predicted_class, compute_duration_ns)
            with self._lock:
                if self.active is not state or state["generation"] != generation:
                    break
                state["predictions"].append(record)
                emitted.append(record)
            self.session_logger.append(
                "window_evaluated",
                trialId=state["trialId"],
                selectionId=state["selectionId"],
                windowIndex=window_index,
                windowLengthSeconds=effective_seconds,
                rawEegEvidence=record["rawEegEvidence"],
                eegTop=record["eegTopLogicalBlockIds"],
                contextPrior=record["contextPrior"],
                softenedContext=record["contextPrior"],
                fusedEvidence=record["fusedEvidence"],
                fusedTop=record["fusedTopLogicalBlockIds"],
                top1=record["top1"],
                top2=record["top2"],
                margin=record["margin"],
                thresholdPass=record["thresholdPass"],
                marginPass=record["marginPass"],
                eegConfirmPass=record["eegConfirmPass"],
                consecutiveCount=record["consecutiveCount"],
                m13State=record["m13State"],
                hypotheticalDecision=bool(state["policy"].decision),
                sourceType=LIVE_SOURCE_TYPE,
            )
            if self.prediction_log is not None:
                self.prediction_log.append(record)
            if state["policy"].decision is not None:
                break
        return emitted

    def stop_trial(self, reason="stimulus_stopped"):
        with self._lock:
            state, self.active = self.active, None
        if state is None:
            return None
        decision = state["policy"].finalize()
        result = dynamic_stopping_decision_to_m8_final_decision(decision, state["trialId"], state["sessionId"])
        result.update({
            "predictionTimeline": list(state["predictions"]),
            "m13Decision": decision.to_public_dict(),
            "sourceType": LIVE_SOURCE_TYPE,
            "reason": decision.stop_reason or reason,
        })
        if self.decision_log is not None:
            self.decision_log.append(result)
        self.session_logger.append(
            "decision_ready",
            trialId=state["trialId"],
            selectionId=state["selectionId"],
            decisionMade=bool(decision.decision_made),
            stopReason=decision.stop_reason,
            earlyStop=bool(decision.early_stop),
            stopWindow=decision.stop_window,
            effectiveAcquisitionSeconds=decision.effective_acquisition_seconds,
            evaluatedWindows=decision.evaluated_windows,
            provenance=dict(decision.provenance),
            sourceType=LIVE_SOURCE_TYPE,
        )
        return result

    def record_final_submission(self, m8_result):
        """Record the existing M8/Quest terminal ACK without sending anything."""
        if not isinstance(m8_result, dict):
            return
        trial_id = m8_result.get("trialId")
        selection_id = m8_result.get("selectionId")
        decision = m8_result.get("m13Decision")
        self.session_logger.append(
            "final_submission",
            trialId=trial_id,
            selectionId=selection_id,
            decisionMade=bool(m8_result.get("decisionMade")),
            submitAttempted=True,
            submitSuccess=m8_result.get("status") in ("quest_accepted", "no_decision", "aborted"),
            submissionSource="m13_active",
            m8Result=m8_result,
            duplicateSuppressed=False,
        )
        self.session_logger.append(
            "trial_closed",
            trialId=trial_id,
            selectionId=selection_id,
            result=m8_result.get("status"),
            selectedTarget=m8_result.get("m13SelectedTargetId"),
            selectedLogicalBlockId=m8_result.get("m13SelectedLogicalBlockId"),
            selectedSlot=decision.get("selectedSlotIndex") if isinstance(decision, dict) else None,
            earlyStop=bool(decision.get("earlyStop")) if isinstance(decision, dict) else False,
            stopWindow=decision.get("stopWindow") if isinstance(decision, dict) else None,
            effectiveAcquisitionSeconds=decision.get("effectiveAcquisitionSeconds") if isinstance(decision, dict) else None,
            m13DecisionMade=bool(decision.get("decisionMade")) if isinstance(decision, dict) else bool(m8_result.get("decisionMade")),
            m13EarlyStop=bool(decision.get("earlyStop")) if isinstance(decision, dict) else False,
            m13StopWindow=decision.get("stopWindow") if isinstance(decision, dict) else None,
            m13EffectiveAcquisitionSeconds=decision.get("effectiveAcquisitionSeconds") if isinstance(decision, dict) else None,
            m13StopReason=decision.get("stopReason") if isinstance(decision, dict) else m8_result.get("reason"),
            m13EvaluatedWindows=decision.get("evaluatedWindows") if isinstance(decision, dict) else 0,
            baselineFinalLogicalBlockId=None,
            m12FullWindowLogicalBlockId=None,
            mappingValid=True,
            runtimeFaults=[],
            submitAttempted=True,
            submitSuccess=m8_result.get("status") in ("quest_accepted", "no_decision", "aborted"),
            duplicateSuppressed=False,
            noDecisionReason=None if m8_result.get("decisionMade") else m8_result.get("reason"),
            sourceType=LIVE_SOURCE_TYPE,
        )

    def close_session(self, status, failure_reason=None):
        if self._session_closed:
            return
        self._session_closed = True
        self.session_logger.append("session_ended", status=status, failureReason=failure_reason, sourceType=LIVE_SOURCE_TYPE)


__all__ = ["LIVE_SOURCE_TYPE", "M135LiveOnlineController", "uniform_pre_eeg_context_prior"]
