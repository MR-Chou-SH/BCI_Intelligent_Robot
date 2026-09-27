import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.sample_association.models import PacketContinuityRecord
from integration.m13_7_golden_session import (
    AudioCueSystem,
    AudioTimingError,
    HumanSessionRecorder,
    NullLoggingAudioBackend,
    SLOT_TO_FREQUENCY_HZ,
    _PerfCounterClock,
    _FakeClock,
    _append_jsonl,
    _jsonl_read,
    build_golden_protocol,
    run_audio_rehearsal,
    run_no_nd8_dry_run,
    validate_golden_protocol,
    verify_session,
    wait_for_timeline_duration,
)
from integration.m13_7_live_launcher import run_full_simulation


def _packet(sequence=0, sample_count=20):
    return Nd8Packet.from_sdk_payload(
        {"timestamp": float(sequence * sample_count), "data": np.zeros((8, sample_count)).tolist()},
        packet_sequence=sequence,
        receive_monotonic_ns=1_000_000_000 + sequence * sample_count * 1_000_000,
        receive_utc="2026-01-01T00:00:00Z",
    )


class AudioCueTests(unittest.TestCase):
    def test_real_clock_formal_timing_soak_preserves_silence_and_preparation(self):
        timing = build_golden_protocol()["timing"]
        clock = _PerfCounterClock()
        events = []
        cue = AudioCueSystem(
            NullLoggingAudioBackend(clock.monotonic_ns),
            lambda event_type, **values: events.append({
                "eventType": event_type,
                "monotonicNs": clock.monotonic_ns(),
                **values,
            }),
            monotonic_ns=clock.monotonic_ns,
            utc_now=clock.utc_now,
            sleep=clock.sleep,
            required_silence_seconds=timing["guaranteedSilenceBeforeStimulusSeconds"],
            timing_configuration=timing,
        )
        for repetition, slot in enumerate((0, 1, 2), start=1):
            trial_id = "real-clock-soak-{}".format(repetition)
            target_end_before = clock.monotonic_ns()
            cue.target_cue(trial_id, "real-clock-soak", slot, SLOT_TO_FREQUENCY_HZ[slot])
            wait_for_timeline_duration(clock, timing["preparationSeconds"])
            preparation_finished = clock.monotonic_ns()
            cue.ready_cue(trial_id, "real-clock-soak")
            cue.wait_for_stimulus_silence(trial_id)
            cue.stimulus_onset(trial_id, "real-clock-soak", slot, SLOT_TO_FREQUENCY_HZ[slot], "soak")
            cue.stimulus_offset(trial_id, "real-clock-soak")
            cue.end_cue(trial_id, "real-clock-soak", "soak")
            self.assertGreaterEqual(
                preparation_finished - target_end_before,
                int(round((timing["preparationSeconds"] + timing["targetGroupSeparationSeconds"]) * 1_000_000_000.0)),
            )
        onset_by_trial = {
            item["trialId"]: item["monotonicNs"]
            for item in events
            if item["eventType"] == "STIMULUS_ONSET"
        }
        ready_by_trial = {
            item["trialId"]: item["monotonicNs"]
            for item in events
            if item["eventType"] == "READY_CUE_END"
        }
        self.assertEqual(3, len(onset_by_trial))
        self.assertTrue(
            all(onset_by_trial[trial_id] - ready_by_trial[trial_id] >= 1_000_000_000 for trial_id in onset_by_trial)
        )

    def test_rehearsal_preserves_one_two_three_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_audio_rehearsal(Path(directory) / "rehearsal.json")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["mode"], "silent")
        self.assertEqual(result["mapping"], {"0": 7.2, "1": 9.0, "2": 12.0})
        self.assertEqual(result["backend"]["beepCount"], 12)
        self.assertEqual(
            [call["frequencyHz"] for call in result["backend"]["calls"]],
            [
                880.0,
                1760.0,
                440.0,
                880.0,
                880.0,
                1760.0,
                440.0,
                880.0,
                880.0,
                880.0,
                1760.0,
                440.0,
            ],
        )
        self.assertEqual(
            [call["durationSeconds"] for call in result["backend"]["calls"]],
            [0.15, 0.5, 0.6, 0.15, 0.15, 0.5, 0.6, 0.15, 0.15, 0.15, 0.5, 0.6],
        )
        for slot in range(3):
            trial_events = [item for item in result["events"] if item["trialId"] == "rehearsal-{}".format(slot)]
            self.assertEqual(
                [item["eventType"] for item in trial_events],
                [
                    "TRIAL_PREPARATION_STARTED",
                    "TARGET_CUE_START",
                    "TARGET_CUE_END",
                    "TRIAL_PREPARATION_FINISHED",
                    "READY_CUE_START",
                    "READY_CUE_END",
                    "STIMULUS_ONSET",
                    "ANALYSIS_WINDOW_OPEN",
                    "ANALYSIS_WINDOW_CLOSE",
                    "STIMULUS_OFFSET",
                    "END_CUE_START",
                    "END_CUE_END",
                ],
            )
            self.assertEqual(
                trial_events[1]["beepCount"],
                slot + 1,
            )
            target_end = next(item for item in trial_events if item["eventType"] == "TARGET_CUE_END")
            ready_start = next(item for item in trial_events if item["eventType"] == "READY_CUE_START")
            self.assertGreaterEqual(
                (ready_start["monotonicNs"] - target_end["monotonicNs"]) / 1_000_000_000.0,
                1.7,
            )
            if slot < 2:
                end_end = next(item for item in trial_events if item["eventType"] == "END_CUE_END")
                next_trial_start = next(
                    item
                    for item in result["events"]
                    if item["eventType"] == "TRIAL_PREPARATION_STARTED"
                    and item["trialId"] == "rehearsal-{}".format(slot + 1)
                )
                self.assertGreaterEqual(
                    (next_trial_start["monotonicNs"] - end_end["monotonicNs"]) / 1_000_000_000.0,
                    1.2,
                )

    def test_audible_rehearsal_selects_pc_backend_and_keeps_analysis_silent(self):
        class FakePcBackend(NullLoggingAudioBackend):
            name = "test_pc_backend"

        with tempfile.TemporaryDirectory() as directory:
            with patch("integration.m13_7_golden_session.PcToneAudioBackend", FakePcBackend):
                result = run_audio_rehearsal(
                    Path(directory) / "audible-rehearsal.json",
                    audible=True,
                    clock=_FakeClock(),
                )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["mode"], "audible_pc")
        self.assertEqual(result["backend"]["backend"], "test_pc_backend")
        self.assertEqual(result["backend"]["beepCount"], 12)
        self.assertEqual(result["backend"]["failOnBackendError"], True)

    def test_fail_fast_audio_mode_does_not_turn_backend_failure_into_silent_pass(self):
        class BrokenBackend:
            def beep(self, frequency_hz, duration_seconds):
                raise RuntimeError("speaker unavailable")

            def summary(self):
                return {"backend": "broken"}

        clock = _FakeClock()
        cue = AudioCueSystem(
            BrokenBackend(),
            lambda event_type, **values: None,
            monotonic_ns=clock.monotonic_ns,
            sleep=clock.sleep,
            fail_on_backend_error=True,
        )
        with self.assertRaises(AudioTimingError):
            cue.target_cue("trial", "session", 0, 7.2)

    def test_analysis_guard_and_silence_are_enforced(self):
        clock = _FakeClock()
        events = []
        backend = NullLoggingAudioBackend(clock.monotonic_ns)

        def sink(event_type, **values):
            events.append({"eventType": event_type, "monotonicNs": clock.monotonic_ns(), **values})

        cue = AudioCueSystem(backend, sink, monotonic_ns=clock.monotonic_ns, sleep=clock.sleep)
        cue.target_cue("trial", "session", 2, 12.0)
        cue.ready_cue("trial", "session")
        cue.wait_for_stimulus_silence()
        cue.stimulus_onset("trial", "session", 2, 12.0, "aligned")
        with self.assertRaises(AudioTimingError):
            cue.end_cue("trial", "session")
        cue.stimulus_offset("trial", "session")
        cue.end_cue("trial", "session", "decision")
        self.assertEqual(len(backend.calls), 5)
        onset = next(item for item in events if item["eventType"] == "STIMULUS_ONSET")
        self.assertGreaterEqual(onset["silenceBeforeStimulusSeconds"], 1.0)
        self.assertEqual(
            [item["eventType"] for item in events],
            [
                "TARGET_CUE_START",
                "TARGET_CUE_END",
                "READY_CUE_START",
                "READY_CUE_END",
                "STIMULUS_ONSET",
                "ANALYSIS_WINDOW_OPEN",
                "ANALYSIS_WINDOW_CLOSE",
                "STIMULUS_OFFSET",
                "END_CUE_START",
                "END_CUE_END",
            ],
        )

    def test_stimulus_silence_wait_reaches_deadline_after_scheduler_undersleep(self):
        """A scheduler that undersleeps must not make a valid trial fail at onset."""

        class UndersleepClock(_FakeClock):
            def sleep(self, seconds):
                requested = float(seconds)
                if requested <= 0.0:
                    return
                underslept = requested - 0.002 if requested > 0.002 else requested * 0.5
                self.ns += max(1, int(underslept * 1_000_000_000.0))

        clock = UndersleepClock()
        cue = AudioCueSystem(
            NullLoggingAudioBackend(clock.monotonic_ns),
            lambda event_type, **values: None,
            monotonic_ns=clock.monotonic_ns,
            sleep=clock.sleep,
        )
        cue.ready_cue("undersleep-trial", "session")
        cue.wait_for_stimulus_silence()
        cue.stimulus_onset("undersleep-trial", "session", 0, 7.2, "static")


class RecorderVerifierTests(unittest.TestCase):
    def _complete_session(self, root):
        clock = _FakeClock()
        recorder = HumanSessionRecorder(
            root,
            "test-session",
            "synthetic_nd8_replay",
            monotonic_ns=clock.monotonic_ns,
            utc_now=clock.utc_now,
        )
        packet = _packet()
        recorder.record_packet(packet, PacketContinuityRecord(0, 0, "initial", ()))
        recorder.record_event("TRIAL_STARTED", trialId="trial-001", slot=0, frequencyHz=7.2)
        cue = AudioCueSystem(
            NullLoggingAudioBackend(clock.monotonic_ns),
            recorder.record_event,
            monotonic_ns=clock.monotonic_ns,
            sleep=clock.sleep,
        )
        cue.target_cue("trial-001", "test-session", 0, 7.2)
        cue.ready_cue("trial-001", "test-session")
        cue.wait_for_stimulus_silence()
        cue.stimulus_onset("trial-001", "test-session", 0, 7.2, "static")
        recorder.record_decoder_evidence("trial-001", 0, rawScores=[1.0, 0.0, 0.0])
        recorder.record_context_evidence("trial-001", 0, relation="aligned")
        clock.sleep(4.0)
        cue.stimulus_offset("trial-001", "test-session")
        cue.end_cue("trial-001", "test-session", "decision")
        recorder.record_action("trial-001", actionType="selection")
        recorder.record_event("TRIAL_FINISHED", trialId="trial-001")
        recorder.finalize()
        return recorder

    def test_complete_session_is_durable_and_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._complete_session(Path(directory) / "session")
            result = verify_session(recorder.root)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(json.loads((recorder.root / "manifest.json").read_text())["status"], "finalized")
            self.assertTrue((recorder.root / "raw-eeg.jsonl").is_file())

    def test_real_live_packet_persistence_marks_new_eeg_even_when_session_aborts(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = _FakeClock()
            root = Path(directory) / "live-session"
            recorder = HumanSessionRecorder(
                root,
                "live-session",
                "live_nd8",
                monotonic_ns=clock.monotonic_ns,
                utc_now=clock.utc_now,
            )
            recorder.record_packet(_packet(), PacketContinuityRecord(0, 0, "initial", ()))
            boundary = json.loads((root / "manifest.json").read_text())["hardwareBoundary"]
            self.assertTrue(boundary["newRealEegCollected"])
            recorder._touch_manifest(status="failed_partial", partialSession=True)
            boundary = json.loads((root / "manifest.json").read_text())["hardwareBoundary"]
            self.assertTrue(boundary["newRealEegCollected"])

    def test_non_live_or_preflight_without_packets_does_not_mark_new_eeg(self):
        with tempfile.TemporaryDirectory() as directory:
            for source_type in ("synthetic_nd8_replay", "recorded_replay"):
                root = Path(directory) / source_type
                recorder = HumanSessionRecorder(root, source_type, source_type)
                self.assertFalse(
                    json.loads((root / "manifest.json").read_text())["hardwareBoundary"]["newRealEegCollected"]
                )

    def test_verifier_detects_audio_inside_analysis_and_missing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = self._complete_session(Path(directory) / "session")
            path = recorder.root / "events.jsonl"
            records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            opened = next(item for item in records if item["eventType"] == "ANALYSIS_WINDOW_OPEN")
            injected = dict(opened)
            injected["sequence"] = max(item["sequence"] for item in records) + 1
            injected["eventType"] = "BAD_AUDIO"
            injected["eventSource"] = "audio"
            injected["monotonicNs"] = opened["monotonicNs"]
            _append_jsonl(path, injected)
            result = verify_session(recorder.root)
            self.assertEqual(result["status"], "FAIL")
            names = {item["name"] for item in result["checks"] if item["status"] == "FAIL"}
            self.assertIn("audio_not_inside_analysis", names)
            self.assertEqual(verify_session(Path(directory) / "missing")["status"], "FAIL")


class ProtocolAndDryRunTests(unittest.TestCase):
    def test_frozen_json_exactly_matches_generator_and_projected_duration(self):
        frozen_path = Path(__file__).resolve().parents[1] / "docs" / "protocols" / "M13_7_Golden_Protocol_v1.json"
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        generated = build_golden_protocol(20260917, "m13.7-golden-v1")
        self.assertEqual(generated, frozen)
        self.assertEqual(2651.76, generated["projectedDurationSeconds"])
        self.assertEqual(44.2, generated["projectedDurationMinutes"])
        self.assertEqual(6.0, generated["timing"]["preparationSeconds"])
        self.assertEqual(2246.76, generated["durationBreakdown"]["normalTrialSeconds"])
        self.assertEqual(285.0, generated["durationBreakdown"]["restAfterBlockSeconds"])

    def test_golden_protocol_is_reproducible_and_balanced(self):
        first = build_golden_protocol(123, "golden")
        second = build_golden_protocol(123, "golden")
        self.assertEqual(first, second)
        self.assertTrue(validate_golden_protocol(first))
        self.assertEqual(first["counts"]["normalTrialSegmentTotal"], 147)
        self.assertEqual(first["counts"]["staticCalibrationTotal"], 60)
        self.assertEqual(first["counts"]["m13ActiveTotal"], 18)
        self.assertEqual(first["counts"]["contextTotal"], 27)
        self.assertEqual(first["counts"]["closedLoopIndividualSelectionSegments"], 24)
        self.assertGreater(first["projectedDurationMinutes"], 30.0)
        self.assertEqual(first["timing"]["targetBeepSeconds"], 0.15)
        self.assertEqual(first["timing"]["interTargetBeepSilenceSeconds"], 0.28)
        self.assertEqual(first["timing"]["targetGroupSeparationSeconds"], 0.7)
        self.assertEqual(first["timing"]["readyFrequencyHz"], 1760.0)
        self.assertEqual(first["timing"]["endFrequencyHz"], 440.0)

    def test_no_nd8_dry_run_records_verifies_and_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_no_nd8_dry_run(Path(directory) / "dry-run")
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["verification"]["status"], "PASS")
        self.assertEqual(summary["replayResult"]["packetCount"], 80)
        self.assertEqual(summary["protocol"]["threeClassesExercised"], [0, 1, 2])
        self.assertTrue(summary["protocol"]["rejectionPathExercised"])
        self.assertEqual(len(summary["closedLoopTasks"]), 6)
        self.assertTrue(all(item["successfulRobotExecutionCount"] == 4 for item in summary["closedLoopTasks"]))

    def test_complete_147_trial_deterministic_rehearsal_has_no_timing_drift(self):
        protocol_path = Path(__file__).resolve().parents[1] / "docs" / "protocols" / "M13_7_Golden_Protocol_v1.json"
        timing = json.loads(protocol_path.read_text(encoding="utf-8"))["timing"]
        with tempfile.TemporaryDirectory() as directory:
            summary = run_full_simulation(protocol_path, Path(directory) / "full-rehearsal")
            events = _jsonl_read(Path(summary["sessionRoot"]) / "events.jsonl")
        self.assertEqual("PASS", summary["status"])
        self.assertEqual(147, summary["executedSegments"])
        self.assertEqual(0, summary["startBlockIndex"])
        self.assertEqual([0, 1, 2, 3, 4, 5, 6], summary["executedBlockIndices"])
        self.assertEqual([], summary["skippedBlockIndices"])
        self.assertEqual(2651.76, summary["executionProjectedDurationSeconds"])
        self.assertEqual("PASS", summary["verification"]["status"])
        starts = [item for item in events if item["eventType"] == "TRIAL_STARTED"]
        self.assertEqual(147, len(starts))
        per_trial = {}
        for item in events:
            if item.get("trialId"):
                per_trial.setdefault(item["trialId"], {})[item["eventType"]] = item
        for trial_id in [item["trialId"] for item in starts]:
            timeline = per_trial[trial_id]
            self.assertEqual(
                int(round(
                    (
                        timing["preparationSeconds"]
                        + (timing["targetGroupSeparationSeconds"] if timeline["TARGET_CUE_END"]["beepCount"] else 0.0)
                    )
                    * 1_000_000_000.0
                )),
                timeline["TRIAL_PREPARATION_FINISHED"]["monotonicNs"]
                - timeline["TARGET_CUE_END"]["monotonicNs"]
            )
            self.assertGreaterEqual(
                timeline["STIMULUS_ONSET"]["monotonicNs"]
                - timeline["READY_CUE_END"]["monotonicNs"],
                int(round(timing["guaranteedSilenceBeforeStimulusSeconds"] * 1_000_000_000.0)),
            )
            self.assertEqual(
                int(round(timing["stimulusAndAnalysisSeconds"] * 1_000_000_000.0)),
                timeline["ANALYSIS_WINDOW_CLOSE"]["monotonicNs"]
                - timeline["ANALYSIS_WINDOW_OPEN"]["monotonicNs"],
            )
        rests_started = [item for item in events if item["eventType"] == "REST_STARTED"]
        rests_completed = [item for item in events if item["eventType"] == "REST_COMPLETED"]
        self.assertEqual(146, len(rests_started))
        self.assertEqual(146, len(rests_completed))
        self.assertEqual(
            [item["restType"] for item in rests_started],
            [item["restType"] for item in rests_completed],
        )
        for started, completed in zip(rests_started, rests_completed):
            self.assertGreaterEqual(
                completed["monotonicNs"] - started["monotonicNs"],
                int(round(started["durationSeconds"] * 1_000_000_000.0)),
            )


if __name__ == "__main__":
    unittest.main()
