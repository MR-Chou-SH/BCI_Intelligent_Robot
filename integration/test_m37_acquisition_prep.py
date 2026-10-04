"""Focused software-only acceptance checks for M37 acquisition preparation."""

import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import numpy as np
from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.sample_association.models import PacketContinuityRecord

from integration.m37_acquisition_prep import (
    CHANNEL_COUNT,
    EPOCH_SAMPLES,
    M37Error,
    M37PacketRingBuffer,
    M37TrialStateMachine,
    M37Session,
    M37SyntheticClock,
    M37SyntheticPacketSource,
    M37TrialCoordinator,
    M37LiveContinuousSource,
    PRE_ONSET_SAMPLES,
    POST_ONSET_SAMPLES,
    SLOT_FREQUENCIES_HZ,
    build_formal_schedule,
    build_session_schedule,
    load_config,
    run_synthetic_rehearsal,
    validate_formal_schedule,
)
from integration.m8_selection_orchestration import QuestSelectionTcpServer


class M37AcquisitionPrepTests(unittest.TestCase):
    def test_fake_nd8_adapter_streams_through_live_source_and_persists_first_packet(self):
        class ImmediatePacketAdapter:
            def __init__(self, com_port, nominal_sampling_rate_hz, live_packet_observer):
                self.com_port = com_port
                self.nominal_sampling_rate_hz = nominal_sampling_rate_hz
                self.live_packet_observer = live_packet_observer
                self.state = SimpleNamespace(value="closed")
                self.host_mac_ready = True
                self.stop_called = False
                self.close_called = False

            def open_port(self):
                self.state.value = "open"

            def start_streaming(self):
                self.state.value = "streaming"
                for sequence in range(4):
                    samples = tuple(tuple(float(sequence + channel) for _ in range(20)) for channel in range(CHANNEL_COUNT))
                    packet = Nd8Packet(1000.0 + sequence * 20.0, samples, time.perf_counter_ns(), "2026-10-05T00:00:00Z", sequence, 1000.0)
                    continuity = PacketContinuityRecord(sequence, sequence * 20, "initial" if sequence == 0 else "continuous")
                    self.live_packet_observer(packet, continuity)

            def stop(self):
                self.stop_called = True
                self.state.value = "stopped"

            def close(self):
                self.close_called = True
                self.state.value = "closed"

        with tempfile.TemporaryDirectory(prefix="m37-live-fake-") as temporary:
            session_root = Path(temporary) / "session"
            source = M37LiveContinuousSource("COM11", session_root, adapter_factory=ImmediatePacketAdapter)
            source.open(timeout_seconds=0.2)
            self.assertEqual(4, source.ring.packet_count)
            self.assertEqual(0, source.ring.gap_count)
            self.assertTrue(source.recorder.manifest["hardwareBoundary"]["nd8Operated"])
            source.recorder.flush_through(source.ring.last_sequence, timeout_seconds=1.0)
            source.close()
            resumed = M37LiveContinuousSource("COM11", session_root, resume=True, adapter_factory=ImmediatePacketAdapter)
            resumed.open(timeout_seconds=0.2)
            self.assertEqual(80, resumed.ring.start_sample)
            self.assertEqual(160, resumed.ring.stop_sample)
            self.assertEqual(0, resumed.ring.gap_count)
            resumed.recorder.flush_through(resumed.ring.last_sequence, timeout_seconds=1.0)
            resumed.close()
            raw_lines = (session_root / "continuous_stream" / "raw-eeg.jsonl").read_text(encoding="utf-8").splitlines()
            metadata_lines = (session_root / "continuous_stream" / "packet-metadata.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(8, len(raw_lines))
            self.assertEqual(8, len(metadata_lines))
            self.assertEqual(CHANNEL_COUNT, json.loads(raw_lines[0])["channelCount"])
            self.assertTrue(source.closed)
            final_metadata = json.loads(metadata_lines[-1])
            self.assertEqual(7, final_metadata["packet"]["packet_sequence"])
            self.assertEqual(140, final_metadata["continuity"]["cumulative_first_sample_index"])

    def test_frozen_frequency_mapping_and_balanced_formal_schedule(self):
        self.assertEqual((7.2, 9.0, 12.0), SLOT_FREQUENCIES_HZ)
        self.assertEqual([7.2, 9.0, 12.0], [item["targetFrequencyHz"] for item in load_config()["slots"]])
        schedule = build_formal_schedule()
        report = validate_formal_schedule(schedule)
        self.assertEqual("PASS", report["status"])
        self.assertEqual(6 * 63, report["trialCount"])
        self.assertEqual(21, report["trialsPerSession"] // 3)
        second_session = build_session_schedule(schedule, 2)
        self.assertEqual(63, len(second_session["rows"]))
        self.assertTrue(all(row["trialId"].startswith("m37-s002-") for row in second_session["rows"]))
        self.assertEqual("PASS", validate_formal_schedule(second_session)["status"])

    def test_trigger_is_accepted_once_only_in_wait_trigger(self):
        state = M37TrialStateMachine()
        state.begin_trial("trial-01")
        state.transition("CUE")
        state.transition("WAIT_TRIGGER")
        self.assertTrue(state.accept_trigger("trial-01"))
        self.assertFalse(state.accept_trigger("trial-01"))
        self.assertFalse(state.accept_trigger("stale-trial"))
        self.assertEqual("PREP_1P5", state.state)
        self.assertEqual(2, state.rejected_trigger_count)
        with self.assertRaises(M37Error):
            state.transition("COMPLETE")

    def test_continuous_ring_anchors_after_onset_and_returns_exact_epoch(self):
        ring = M37PacketRingBuffer()
        sample = 0
        sequence = 0
        onset_ns = 5_000_000_000
        for _ in range(PRE_ONSET_SAMPLES // 20):
            data = np.full((CHANNEL_COUNT, 20), sequence, dtype=float)
            ring.append(data, sample, sequence, onset_ns - 10_000_000, "initial" if sequence == 0 else "continuous")
            sample += 20
            sequence += 1
        ring.mark_stimulus_onset(onset_ns)
        for _ in range(POST_ONSET_SAMPLES // 20):
            data = np.full((CHANNEL_COUNT, 20), sequence, dtype=float)
            ring.append(data, sample, sequence, onset_ns + sequence * 1_000_000, "continuous")
            sample += 20
            sequence += 1
        anchor = ring.wait_for_anchor(0.1)
        self.assertEqual(PRE_ONSET_SAMPLES, anchor["sampleAnchorSampleIndex"])
        ring.wait_for_stop_sample(anchor["sampleAnchorSampleIndex"] + POST_ONSET_SAMPLES, 0.1)
        epoch = ring.window(anchor["sampleAnchorSampleIndex"] - PRE_ONSET_SAMPLES, anchor["sampleAnchorSampleIndex"] + POST_ONSET_SAMPLES)
        self.assertEqual((CHANNEL_COUNT, EPOCH_SAMPLES), epoch.shape)
        self.assertEqual(0, ring.gap_count)

    def test_discontinuity_does_not_fabricate_samples(self):
        ring = M37PacketRingBuffer()
        ring.append(np.ones((CHANNEL_COUNT, 20)), 0, 0, 1_000_000_000, "initial")
        ring.append(np.ones((CHANNEL_COUNT, 20)), 40, 2, 1_020_000_000, "missing_packet")
        self.assertEqual(1, ring.gap_count)
        self.assertEqual(20, ring.missing_sample_count)
        with self.assertRaises(M37Error):
            ring.window(0, 40)

    def test_simulated_trigger_runs_shared_path_and_finalizes_4500_samples(self):
        with tempfile.TemporaryDirectory(prefix="m37-rehearsal-") as temporary:
            root = Path(temporary) / "session"
            report = run_synthetic_rehearsal(root, trials=3, seed=37005)
            self.assertEqual("PASS", report["status"])
            self.assertEqual(3, report["trialCount"])
            self.assertTrue(report["allEpochs4500PerChannel"])
            self.assertTrue(report["allPreOnset500"])
            self.assertTrue(report["allPostOnset4000"])

            events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            accepted = [item for item in events if item.get("eventType") == "TRIGGER_ACCEPTED"]
            rejected = [item for item in events if item.get("eventType") == "DUPLICATE_OR_STALE_TRIGGER_REJECTED"]
            target_cues = [item for item in events if item.get("eventType") == "TARGET_CUE_START"]
            finalized = [item for item in events if item.get("eventType") == "TRIAL_FINALIZED"]
            self.assertEqual(3, len(accepted))
            self.assertTrue(all(item["triggerSource"] == "pc_simulated_same_handler" for item in accepted))
            self.assertEqual(1, len(rejected))
            self.assertEqual(3, len(finalized))
            expected_tones = {0: 660.0, 1: 880.0, 2: 1320.0}
            self.assertEqual(3, len(target_cues))
            self.assertTrue(all(item["cueToneFrequencyHz"] == expected_tones[item["slot"]] and item["beepCount"] == item["slot"] + 1 for item in target_cues))
            for record in finalized:
                path = root / record["epochFile"]
                self.assertTrue(path.is_file())
                with np.load(path) as epoch:
                    self.assertEqual((CHANNEL_COUNT, EPOCH_SAMPLES), epoch["samples"].shape)
                    self.assertEqual(CHANNEL_COUNT, len(epoch["channel_ids"]))
            raw_lines = (root / "continuous_stream" / "raw-eeg.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertGreater(len(raw_lines), 0)
            self.assertEqual(CHANNEL_COUNT, json.loads(raw_lines[0])["channelCount"])

            by_trial = {}
            for item in events:
                if item.get("trialId"):
                    by_trial.setdefault(item["trialId"], []).append(item)
            for trial_events in by_trial.values():
                types = [item["eventType"] for item in trial_events]
                self.assertLess(types.index("TRIGGER_ACCEPTED"), types.index("TRIAL_PREPARATION_STARTED"))
                self.assertLess(types.index("TRIAL_PREPARATION_FINISHED"), types.index("STIMULUS_ONSET"))
                self.assertLess(types.index("STIMULUS_ONSET"), types.index("STIMULUS_OFFSET"))
                self.assertLess(types.index("STIMULUS_OFFSET"), types.index("TRIAL_FINALIZED"))

    def test_stop_restart_preserves_completed_trials_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="m37-resume-") as temporary:
            root = Path(temporary) / "session"
            first = run_synthetic_rehearsal(root, trials=3, seed=37005, stop_after=1)
            self.assertEqual("PASS", first["status"])
            self.assertEqual(1, first["trialCount"])
            self.assertFalse((root / ".m37-session.lock").exists())
            epoch_files_before = sorted(path.name for path in (root / "epochs").glob("*.npz"))
            raw_lines_before = len((root / "continuous_stream" / "raw-eeg.jsonl").read_text(encoding="utf-8").splitlines())

            second = run_synthetic_rehearsal(root, trials=3, seed=37005, stop_after=2, resume=True)
            self.assertEqual("PASS", second["status"])
            self.assertEqual(3, second["trialCount"])
            self.assertEqual(2, second["trialsCompletedThisRun"])
            epoch_files_after = sorted(path.name for path in (root / "epochs").glob("*.npz"))
            self.assertEqual(3, len(epoch_files_after))
            self.assertTrue(set(epoch_files_before).issubset(set(epoch_files_after)))
            self.assertGreater(len((root / "continuous_stream" / "raw-eeg.jsonl").read_text(encoding="utf-8").splitlines()), raw_lines_before)
            with self.assertRaises(FileExistsError):
                run_synthetic_rehearsal(root, trials=3, seed=37005)

    def test_mid_epoch_disconnect_is_invalid_without_fabricated_samples(self):
        class DisconnectAfterAnchorSource(M37SyntheticPacketSource):
            def wait(self, seconds, slot=None):
                if slot is not None and seconds > 0.021:
                    raise OSError("simulated ND8 disconnect")
                return super().wait(seconds, slot)

        with tempfile.TemporaryDirectory(prefix="m37-disconnect-") as temporary:
            root = Path(temporary) / "session"
            clock = M37SyntheticClock()
            rows = [{
                "trialId": "m37-disconnect-001", "sessionId": "m37-dummy", "blockId": "M37",
                "trialIndex": 1, "targetSlot": 0, "targetFrequencyHz": 7.2, "formal": False,
            }]
            schedule = {"recordType": "m37_nonformal_dummy_schedule", "protocolVersion": "m37-prospective-acquisition-v1", "seed": 1, "rows": rows}
            from integration.m37_acquisition_prep import _canonical_json, _sha256_bytes, load_config
            schedule["scheduleSha256"] = _sha256_bytes(_canonical_json(schedule).encode("utf-8"))
            session = M37Session(root, "m37-disconnect", load_config(), schedule, monotonic_provider=clock.monotonic_ns)
            source = DisconnectAfterAnchorSource(M37PacketRingBuffer(), clock)
            coordinator = M37TrialCoordinator(session, source, clock=clock)
            try:
                with self.assertRaisesRegex(OSError, "simulated ND8 disconnect"):
                    coordinator.run_one(rows[0], simulated_trigger=True)
            finally:
                source.close()
                session.finalize_session("PAUSED_RESUMABLE")

            events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            invalid = [item for item in events if item["eventType"] == "TRIAL_TECHNICAL_INVALID"]
            self.assertEqual(1, len(invalid))
            self.assertFalse(invalid[0]["fakeSamplesAdded"])
            self.assertFalse(any(item["eventType"] == "TRIAL_FINALIZED" for item in events))
            self.assertEqual([], list((root / "epochs").glob("*.npz")) if (root / "epochs").exists() else [])
            self.assertFalse((root / ".m37-session.lock").exists())

    def test_session_lock_rejects_concurrent_writer_and_releases_for_resume(self):
        with tempfile.TemporaryDirectory(prefix="m37-lock-") as temporary:
            root = Path(temporary) / "session"
            rows = [{
                "trialId": "m37-lock-001", "sessionId": "session_001", "targetSlot": 0,
                "targetFrequencyHz": 7.2, "trialIndex": 1, "formal": True,
            }]
            schedule = {"recordType": "m37_frozen_formal_schedule", "protocolVersion": "m37-prospective-acquisition-v1",
                        "seed": 1, "sessionCount": 1, "trialsPerSession": 3, "maxSameTargetRun": 1,
                        "rows": [{"trialId": "m37-lock-{:03d}".format(index), "sessionId": "session_001",
                                  "trialIndex": index, "targetSlot": index - 1,
                                  "targetFrequencyHz": (7.2, 9.0, 12.0)[index - 1], "formal": True}
                                 for index in range(1, 4)]}
            from integration.m37_acquisition_prep import _canonical_json, _sha256_bytes, load_config
            schedule["scheduleSha256"] = _sha256_bytes(_canonical_json(schedule).encode("utf-8"))
            active = M37Session(root, "m37-lock", load_config(), schedule)
            with self.assertRaises(FileExistsError):
                M37Session(root, "m37-lock", load_config(), schedule, resume=True)
            active.finalize_session("PAUSED_RESUMABLE")
            resumed = M37Session(root, "m37-lock", load_config(), schedule, resume=True)
            self.assertTrue((root / ".m37-session.lock").is_file())
            resumed.finalize_session("PAUSED_RESUMABLE")
            self.assertFalse((root / ".m37-session.lock").exists())

    def test_fake_quest_tcp_runs_offer_stimulus_ack_and_shared_trigger_path(self):
        with tempfile.TemporaryDirectory(prefix="m37-quest-tcp-") as temporary:
            root = Path(temporary) / "session"
            clock = M37SyntheticClock()
            rows = [{
                "trialId": "m37-tcp-001", "sessionId": "m37-tcp", "blockId": "M37",
                "trialIndex": 1, "targetSlot": 2, "targetFrequencyHz": 12.0, "formal": False,
            }]
            schedule = {"recordType": "m37_nonformal_dummy_schedule", "protocolVersion": "m37-prospective-acquisition-v1", "seed": 1, "rows": rows}
            from integration.m37_acquisition_prep import _canonical_json, _sha256_bytes, load_config
            schedule["scheduleSha256"] = _sha256_bytes(_canonical_json(schedule).encode("utf-8"))
            session = M37Session(root, "m37-tcp", load_config(), schedule, monotonic_provider=clock.monotonic_ns)
            transport = QuestSelectionTcpServer(host="127.0.0.1", port=0, accept_timeout_seconds=5).start()
            received = []
            peer_errors = []

            def fake_quest():
                try:
                    with socket.create_connection(("127.0.0.1", transport.port), timeout=3) as peer:
                        peer.settimeout(5)
                        peer.sendall(b'{"protocolVersion":1,"messageType":"m19_research_ready"}\n')
                        buffer = b""
                        while True:
                            chunk = peer.recv(4096)
                            if not chunk:
                                return
                            buffer += chunk
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                message = json.loads(line.decode("utf-8"))
                                received.append(message)
                                response_type = {
                                    "m19_research_offer": "m19_research_offer_ack",
                                    "m19_research_stimulus_start": "m19_research_stimulus_started",
                                    "m19_research_stimulus_stop": "m19_research_stimulus_stopped",
                                }.get(message.get("messageType"))
                                if response_type:
                                    response = dict(message)
                                    response.update({"messageType": response_type, "accepted": True, "eventMonotonicNs": 123, "softwareFrame": 456})
                                    peer.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
                except OSError as error:
                    peer_errors.append(str(error))

            thread = threading.Thread(target=fake_quest, daemon=True)
            thread.start()
            try:
                self.assertEqual("m19_research_ready", __import__("integration.m37_acquisition_prep", fromlist=["_wait_for_quest_ready"])._wait_for_quest_ready(transport, 4)["messageType"])
                source = M37SyntheticPacketSource(M37PacketRingBuffer(), clock, root)
                coordinator = M37TrialCoordinator(session, source, transport=transport, simulated_reaction_seconds=0.0, clock=clock)
                result = coordinator.run_one(rows[0], simulated_trigger=True)
                session.finalize_session("FINALIZED")
                source.close()
                self.assertEqual(EPOCH_SAMPLES, result["sampleCountPerChannel"])
            finally:
                transport.close()
                thread.join(timeout=3)

            message_types = [item["messageType"] for item in received]
            self.assertIn("m19_research_offer", message_types)
            self.assertIn("m19_research_stimulus_start", message_types)
            self.assertIn("m19_research_stimulus_stop", message_types)
            offer = next(item for item in received if item["messageType"] == "m19_research_offer")
            onset = next(item for item in received if item["messageType"] == "m19_research_stimulus_start")
            self.assertEqual("M37", offer["blockId"])
            self.assertEqual(3, offer["cueBeepCount"])
            self.assertTrue(onset["simulatedTrigger"])
            self.assertFalse(peer_errors)


if __name__ == "__main__":
    unittest.main()
