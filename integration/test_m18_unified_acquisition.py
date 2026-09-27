import json
from pathlib import Path
import tempfile
import unittest

from integration.m18_unified_acquisition import (
    M18_PROTOCOL,
    EpisodePlan,
    EpisodeStateMachine,
    M18ContextEngine,
    M18SessionRecorder,
    build_schedule,
    export_canonical,
    run_context_causality_tests,
    run_synthetic_dry_run,
    _runtime_episode_plan,
    validate_matrix,
    validate_session,
    write_block_schedule_csv,
    write_matrix_csv,
)


class M18UnifiedAcquisitionTests(unittest.TestCase):
    def test_final_matrix_is_exact_and_deterministic(self):
        first = build_schedule()
        second = build_schedule()
        self.assertEqual(first["scheduleFingerprint"], second["scheduleFingerprint"])
        self.assertEqual(validate_matrix(M18_PROTOCOL, first)["status"], "PASS")
        self.assertEqual(len(first["episodes"]), 102)
        self.assertEqual(len(first["blocks"]), 20)

    def test_context_is_causal_immutable_and_rejects_forbidden_inputs(self):
        report = run_context_causality_tests()
        self.assertEqual(report["status"], "PASS")
        engine = M18ContextEngine()
        with self.assertRaises(ValueError):
            engine.generate(
                completed_history=(),
                task_state_id="state",
                scenario_seed=1,
                generated_at_monotonic_ns=1,
                current_decoder_score=0.9,
            )

    def test_frozen_snapshot_rejects_nested_candidate_mutation(self):
        snapshot = M18ContextEngine().generate(
            completed_history=(),
            task_state_id="state",
            scenario_seed=1,
            generated_at_monotonic_ns=10,
            frozen_at_monotonic_ns=11,
        )
        with self.assertRaises(TypeError):
            snapshot.candidate_set[0]["logicalBlockId"] = "mutated"

    def test_recorder_rejects_clock_regression_and_post_finalize_append(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = M18SessionRecorder(Path(directory) / "session", "session-1", M18_PROTOCOL, utc_now_provider=lambda: "2030-01-01T00:00:00+00:00")
            recorder.record_event("SESSION_STARTED", monotonicNs=10)
            event = json.loads((Path(directory) / "session" / "events" / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["utcTimestamp"], "2030-01-01T00:00:00+00:00")
            with self.assertRaises(ValueError):
                recorder.record_event("CLOCK_REGRESSION", monotonicNs=9)
            recorder.finalize()
            manifest = json.loads((Path(directory) / "session" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["finalizedUtc"], "2030-01-01T00:00:00+00:00")
            with self.assertRaises(RuntimeError):
                recorder.record_event("AFTER_FINALIZE", monotonicNs=11)

    def test_aborted_episode_is_preserved_and_cannot_be_reused_as_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "session"
            recorder = M18SessionRecorder(root, "session-1", M18_PROTOCOL)
            recorder.record_event("EPISODE_STARTED", episodeId="episode-1", monotonicNs=10)
            recorder.record_event("EPISODE_ABORTED", episodeId="episode-1", monotonicNs=11, abortReason="operator_abort")
            self.assertNotIn("episode-1", recorder.completed_episodes)
            resumed = M18SessionRecorder(root, "session-1", M18_PROTOCOL, resume=True)
            self.assertIn("episode-1", resumed.aborted_episodes)
            with self.assertRaises(ValueError):
                resumed.record_event("EPISODE_COMPLETED", episodeId="episode-1", monotonicNs=12)

    def test_runtime_context_history_uses_only_completed_episodes(self):
        schedule = build_schedule()
        first = schedule["episodes"][0]
        second = schedule["episodes"][1]
        remaining = {
            slot: int(M18_PROTOCOL["frequencyBalance"][second["group"]][str((7.2, 9.0, 12.0)[slot])])
            for slot in range(3)
        }

        def as_plan(value):
            return EpisodePlan(
                episode_id=value["episodeId"], block_id=value["blockId"], ordinal=value["ordinal"],
                group=value["group"], attention_condition=value["attentionCondition"],
                scheduled_context_condition=value["scheduledContextCondition"],
                slot_index=value["slotIndex"], frequency_hz=value["frequencyHz"],
                target_id=value["targetId"], logical_block_id=value["logicalBlockId"],
                observed_context_relation=value["observedContextRelation"],
                intent_onset_ground_truth=value["intentOnsetGroundTruth"],
                control_state_ground_truth=value["controlStateGroundTruth"],
                context_snapshot=value["contextSnapshot"], target_sample=value["targetSample"],
            )

        no_completed = _runtime_episode_plan(as_plan(second), completed_history=(), remaining_slot_counts=remaining)
        self.assertEqual(no_completed.context_snapshot["completedHistory"], [])
        with_completed = _runtime_episode_plan(as_plan(second), completed_history=(first["logicalBlockId"],), remaining_slot_counts=remaining)
        self.assertEqual(with_completed.context_snapshot["completedHistory"], [first["logicalBlockId"]])

    def test_state_machine_fails_closed(self):
        machine = EpisodeStateMachine("episode-1")
        machine.move("PRE_NC", "start")
        with self.assertRaises(RuntimeError):
            machine.move("INTENTIONAL", "illegal_skip")
        machine.abort("operator_abort")
        with self.assertRaises(RuntimeError):
            machine.move("COMPLETE", "after_abort")

    def test_csv_outputs_are_machine_readable(self):
        schedule = build_schedule()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_matrix_csv(root / "matrix.csv", M18_PROTOCOL)
            write_block_schedule_csv(root / "blocks.csv", schedule)
            self.assertGreater((root / "matrix.csv").stat().st_size, 0)
            self.assertEqual(len((root / "blocks.csv").read_text(encoding="utf-8").splitlines()), 21)

    def test_synthetic_dry_run_has_no_performance_claim_and_is_resume_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = run_synthetic_dry_run(root)
            self.assertEqual(first["status"], "PASS")
            self.assertEqual(first["validation"]["contextSnapshotCount"], 102)
            self.assertEqual(first["validation"]["evaluationTruthCount"], 102)
            self.assertGreater(first["validation"]["rawPacketCount"], 0)
            manifest = json.loads((root / "synthetic_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["accuracy"], "NOT_COMPUTED")
            self.assertEqual(manifest["tpr"], "NOT_COMPUTED")
            events_path = Path(first["sessionRoot"]) / "events" / "events.jsonl"
            before = events_path.read_text(encoding="utf-8")
            events = [json.loads(line) for line in before.splitlines() if line.strip()]
            anchors = [event for event in events if event["eventType"] == "TRIAL_SAMPLE_ANCHOR"]
            self.assertEqual(len(anchors), 102)
            self.assertTrue(all(isinstance(event["sampleAnchorSampleIndex"], int) for event in anchors))
            resumed = run_synthetic_dry_run(root, resume=True)
            after = events_path.read_text(encoding="utf-8")
            self.assertEqual(resumed["status"], "PASS")
            self.assertEqual(before, after)

            snapshot_path = Path(first["sessionRoot"]) / "context" / "context-snapshots.jsonl"
            snapshot_lines = snapshot_path.read_text(encoding="utf-8").splitlines()
            first_snapshot = json.loads(snapshot_lines[0])
            first_snapshot["snapshotCanonicalSha256"] = "0" * 64
            snapshot_lines[0] = json.dumps(first_snapshot, sort_keys=True)
            snapshot_path.write_text("\n".join(snapshot_lines) + "\n", encoding="utf-8")
            corrupted = validate_session(Path(first["sessionRoot"]), build_schedule(), M18_PROTOCOL)
            self.assertEqual(corrupted["status"], "FAIL")
            self.assertFalse(corrupted["checks"]["context_snapshot_hashes_valid"])

    def test_canonical_export_keeps_truth_out_of_phase_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_synthetic_dry_run(root)
            schedule = build_schedule()
            export = export_canonical(Path(result["sessionRoot"]), Path(result["sessionRoot"]) / "exports", schedule)
            self.assertEqual(export["status"], "PASS")
            phase2 = json.loads((Path(result["sessionRoot"]) / "exports" / "phase2_export.json").read_text(encoding="utf-8"))
            self.assertEqual(len(phase2["rows"]), 78)
            self.assertTrue(all(row["target"] == "EVALUATION_ONLY_IN_CONTEXT_TRUTH_FILE" for row in phase2["rows"]))
            phase3 = json.loads((Path(result["sessionRoot"]) / "exports" / "phase3_export.json").read_text(encoding="utf-8"))
            self.assertTrue(phase3["rows"][0]["sampleAnchor"]["anchorBasis"] == "first_packet_received_at_or_after_stimulus_onset")
            self.assertIn("READY_CUE_END", phase3["rows"][0]["cueMarkers"])
            self.assertIn("REARM_OBSERVATION_FINISHED", phase3["rows"][0]["eventTypes"])
            self.assertNotIn("trueTarget", phase3["rows"][0])


if __name__ == "__main__":
    unittest.main()
