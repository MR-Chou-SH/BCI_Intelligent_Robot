import json
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from integration.m13_7_golden_session import HumanSessionRecorder, RecordedND8Replay, _FakeClock, _jsonl_read
from integration.m13_7_live_launcher import (
    GoldenLauncherError,
    GoldenLiveSessionLauncher,
    GoldenPacketPipeline,
    LiveND8Source,
    build_execution_slice,
    load_golden_protocol,
    run_full_simulation,
    run_preflight,
)
from eeg.sample_association.models import PacketContinuityRecord


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "protocols" / "M13_7_Golden_Protocol_v1.json"
PROTOCOL_TIMING = json.loads(PROTOCOL.read_text(encoding="utf-8"))["timing"]


class _FakeDevice:
    def __init__(self, host_callback, eeg_callback):
        self.host_callback = host_callback
        self.eeg_callback = eeg_callback
        self.calls = []

    def start(self):
        self.calls.append("start")

    def host_mac_info(self):
        self.calls.append("host_mac_info")
        self.host_callback("001122334455")

    def eeg_channel_config(self, rate):
        self.calls.append(("config", rate))

    def eeg_channel_enable(self):
        self.calls.append("enable")

    def eeg_disable(self):
        self.calls.append("disable")

    def close(self):
        self.calls.append("close")

    def emit(self):
        self.eeg_callback({"timestamp": 1000, "data": [[float(i)] * 20 for i in range(8)]})


class _FakePcBackend:
    name = "test_pc_backend"

    def __init__(self):
        self.calls = []

    def beep(self, frequency_hz, duration_seconds):
        self.calls.append((float(frequency_hz), float(duration_seconds)))

    def summary(self):
        return {"backend": self.name, "available": True, "beepCount": len(self.calls)}


class _RawFirstPipeline(GoldenPacketPipeline):
    def __init__(self, raw_path):
        super().__init__()
        self.raw_path = raw_path
        self.raw_records_seen_before_downstream = None

    def __call__(self, packet, continuity):
        self.raw_records_seen_before_downstream = len(_jsonl_read(self.raw_path))
        super().__call__(packet, continuity)


class M137LauncherTests(unittest.TestCase):
    def test_execution_slice_uses_protocol_block_totals_and_default_is_full_protocol(self):
        protocol = load_golden_protocol(PROTOCOL)
        full = build_execution_slice(protocol, 0)
        continuation = build_execution_slice(protocol, 2)
        self.assertEqual(147, full["plannedSegments"])
        self.assertEqual(2651.76, full["executionProjectedDurationSeconds"])
        self.assertEqual([0, 1, 2, 3, 4, 5, 6], full["executedBlockIndices"])
        self.assertEqual(87, continuation["plannedSegments"])
        self.assertEqual([2, 3, 4, 5, 6], continuation["executedBlockIndices"])
        self.assertEqual([0, 1], continuation["skippedBlockIndices"])
        self.assertEqual(1548.96, continuation["executionProjectedDurationSeconds"])
        self.assertEqual(
            ["m13_active", "context_conditions", "no_intent_rejection", "closed_loop_tasks", "controlled_artifact"],
            continuation["executedBlockIds"],
        )

    def test_block_two_continuation_is_a_new_slice_with_truthful_provenance(self):
        """A continuation must execute complete blocks 2-6 into a new raw session."""
        old_session_id = "m13.7-golden-live-20260918T094423Z"
        new_session_id = "m13.7-golden-continuation-part2-test"
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            old_root = directory / old_session_id
            old_root.mkdir(parents=True)
            old_raw = old_root / "raw-eeg.jsonl"
            old_raw.write_text("immutable-old-session\n", encoding="utf-8")

            result = run_full_simulation(
                PROTOCOL,
                directory / "study",
                session_id=new_session_id,
                start_block_index=2,
                continuation_from_session_id=old_session_id,
                continuation_reason="reuse_valid_partial_block0_block1_after_timing_hotfix",
                packet_samples=2000,
            )

            self.assertEqual("PASS", result["status"])
            self.assertEqual(2, result["startBlockIndex"])
            self.assertEqual([2, 3, 4, 5, 6], result["executedBlockIndices"])
            self.assertEqual([0, 1], result["skippedBlockIndices"])
            self.assertEqual(87, result["plannedSegments"])
            self.assertEqual(87, result["executedSegments"])
            self.assertEqual(1548.96, result["executionProjectedDurationSeconds"])

            session_root = Path(result["sessionRoot"])
            manifest = json.loads((session_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(new_session_id, manifest["sessionId"])
            self.assertEqual("GOLDEN_CONTINUATION", manifest["sessionCategory"])
            self.assertEqual(2, manifest["startBlockIndex"])
            self.assertEqual([2, 3, 4, 5, 6], manifest["executedBlockIndices"])
            self.assertEqual([0, 1], manifest["skippedBlockIndices"])
            self.assertEqual(old_session_id, manifest["continuationFromSessionId"])
            self.assertEqual(
                "reuse_valid_partial_block0_block1_after_timing_hotfix",
                manifest["continuationReason"],
            )
            self.assertEqual(3.0, manifest["continuationFromTiming"]["preparationSeconds"])
            self.assertEqual(6.0, manifest["formalProtocolTiming"]["preparationSeconds"])
            self.assertTrue(manifest["neverMergeRawSessions"])
            self.assertEqual(
                ["m13_active", "context_conditions", "no_intent_rejection", "closed_loop_tasks", "controlled_artifact"],
                manifest["completedBlocks"],
            )
            self.assertFalse((session_root / "manifest.json").resolve() == old_root.resolve())
            self.assertEqual("immutable-old-session\n", old_raw.read_text(encoding="utf-8"))

            events = _jsonl_read(session_root / "events.jsonl")
            first_block = next(item for item in events if item["eventType"] == "BLOCK_STARTED")
            self.assertEqual(2, first_block["blockIndex"])
            first_trial = next(item for item in events if item["eventType"] == "TRIAL_STARTED")
            self.assertEqual("m13.7-golden-v1-trial-061", first_trial["trialId"])
            self.assertEqual("m13_active", first_trial["protocolBlock"])
            trial_events = [item for item in events if item["trialId"] == first_trial["trialId"]]
            preparation = next(item for item in trial_events if item["eventType"] == "TRIAL_PREPARATION_STARTED")
            self.assertEqual(6.0, preparation["preparationSeconds"])
            ready_end = next(item for item in trial_events if item["eventType"] == "READY_CUE_END")
            onset = next(item for item in trial_events if item["eventType"] == "STIMULUS_ONSET")
            self.assertGreaterEqual((onset["monotonicNs"] - ready_end["monotonicNs"]) / 1_000_000_000.0, 1.0)

    def test_m13_7_scheduler_has_no_literal_13_second_sleep(self):
        live_source = Path(__file__).resolve().parents[0] / "m13_7_live_launcher.py"
        golden_source = Path(__file__).resolve().parents[0] / "m13_7_golden_session.py"
        for path in (live_source, golden_source):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("sleep(13.0", source)
            self.assertNotIn("sleep(13)", source)
        self.assertIn('self.protocol["timing"]["preparationSeconds"]', live_source.read_text(encoding="utf-8"))
        self.assertIn('protocol_timing["preparationSeconds"]', golden_source.read_text(encoding="utf-8"))

    def test_full_timing_regression_keeps_onset_anchor_and_replay_semantics(self):
        """Preparation length changes must not move the EEG epoch or truncate raw data."""

        class TimingSource:
            source_type = "synthetic_nd8_replay"

            def __init__(self, sink, owner):
                self.sink = sink
                self.clock = owner.clock
                self.requested_duration = None
                self._emit(0, 0, 1000)

            def _emit(self, sequence, first_sample, sample_count):
                packet = Nd8Packet.from_sdk_payload(
                    {
                        "timestamp": float(first_sample),
                        "data": [[float(channel)] * sample_count for channel in range(8)],
                    },
                    packet_sequence=sequence,
                    receive_monotonic_ns=self.clock.monotonic_ns(),
                    receive_utc=self.clock.utc_now(),
                )
                continuity = PacketContinuityRecord(
                    sequence,
                    first_sample,
                    "initial" if sequence == 0 else "continuous",
                    (),
                )
                self.sink(packet, continuity)

            def open_port(self):
                return None

            def start_streaming(self):
                return None

            def acquire_baseline(self, duration_seconds):
                return None

            def acquire_trial(self, spec, duration_seconds):
                self.requested_duration = float(duration_seconds)
                self._emit(1, 1000, 4000)
                self.clock.sleep(duration_seconds)

            def stop(self):
                return None

            def close(self):
                return None

        def run_once(protocol_path, root, session_id, protocol_override=None):
            holder = {}

            def factory(sink, owner):
                holder["source"] = TimingSource(sink, owner)
                return holder["source"]

            if protocol_override is None:
                launcher = GoldenLiveSessionLauncher(
                    protocol_path,
                    root,
                    mode="simulation",
                    session_id=session_id,
                    clock=_FakeClock(),
                    source_factory=factory,
                )
            else:
                with patch(
                    "integration.m13_7_live_launcher.load_golden_protocol",
                    return_value=protocol_override,
                ):
                    launcher = GoldenLiveSessionLauncher(
                        protocol_path,
                        root,
                        mode="simulation",
                        session_id=session_id,
                        clock=_FakeClock(),
                        source_factory=factory,
                    )
            spec = launcher.protocol["trials"][0]
            result = launcher.run(specs=[spec])
            self.assertEqual("PASS", result["status"])
            self.assertEqual(PROTOCOL_TIMING["stimulusAndAnalysisSeconds"], holder["source"].requested_duration)
            session_root = Path(result["sessionRoot"])
            events = _jsonl_read(session_root / "events.jsonl")
            decoder = _jsonl_read(session_root / "decoder-evidence.jsonl")
            raw = _jsonl_read(session_root / "raw-eeg.jsonl")
            manifest = json.loads((session_root / "manifest.json").read_text(encoding="utf-8"))
            anchor = next(item for item in events if item.get("eventType") == "TRIAL_SAMPLE_ANCHOR")
            replay_pipeline = GoldenPacketPipeline()
            replay_pipeline.mark_next_segment(
                anchor["stimulusOnsetMonotonicNs"],
                stimulus_onset_event_sequence=anchor["stimulusOnsetEventSequence"],
            )
            RecordedND8Replay(session_root, speed="max").replay(replay_pipeline)
            replay_anchor = replay_pipeline.next_segment_anchor
            replay_pipeline.backend.predict = lambda epoch: (1, [0.1, 0.8, 0.1])
            replay_decoded = replay_pipeline.predict(replay_anchor)
            return result, events, decoder, raw, manifest, replay_decoded

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            altered = directory / "protocol-preparation-13.json"
            protocol_value = json.loads(PROTOCOL.read_text(encoding="utf-8"))
            protocol_value["timing"]["preparationSeconds"] = 13.0
            preparation_delta = 13.0 - PROTOCOL_TIMING["preparationSeconds"]
            breakdown = protocol_value["durationBreakdown"]
            for row in breakdown["blocks"]:
                row["trialDurationSeconds"] = round(
                    row["trialDurationSeconds"] + row["trialCount"] * preparation_delta,
                    3,
                )
                row["totalSeconds"] = round(
                    row["trialDurationSeconds"] + row["restAfterSeconds"],
                    3,
                )
            breakdown["normalTrialSeconds"] = round(
                breakdown["normalTrialSeconds"] + len(protocol_value["trials"]) * preparation_delta,
                3,
            )
            breakdown["projectedDurationSeconds"] = round(
                breakdown["projectedDurationSeconds"] + len(protocol_value["trials"]) * preparation_delta,
                3,
            )
            protocol_value["projectedDurationSeconds"] = breakdown["projectedDurationSeconds"]
            protocol_value["projectedDurationMinutes"] = round(
                protocol_value["projectedDurationSeconds"] / 60.0,
                2,
            )
            altered.write_text(json.dumps(protocol_value), encoding="utf-8")

            first = run_once(PROTOCOL, directory / "formal-6", "timing-formal-6")
            second = run_once(altered, directory / "old-13", "timing-old-13", protocol_value)

        for result, events, decoder, raw, manifest, replay_decoded in (first, second):
            trial_id = "m13.7-golden-v1-trial-001"
            trial_events = [item for item in events if item.get("trialId") == trial_id]
            by_type = {item["eventType"]: item for item in trial_events}
            lifecycle = [
                item["eventType"]
                for item in trial_events
                if item["eventType"]
                in {
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
                }
            ]
            self.assertEqual(
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
                lifecycle,
            )
            self.assertGreaterEqual(
                by_type["STIMULUS_ONSET"]["monotonicNs"] - by_type["READY_CUE_END"]["monotonicNs"],
                int(round(PROTOCOL_TIMING["guaranteedSilenceBeforeStimulusSeconds"] * 1_000_000_000.0)),
            )
            self.assertEqual(
                int(round(PROTOCOL_TIMING["stimulusAndAnalysisSeconds"] * 1_000_000_000.0)),
                by_type["ANALYSIS_WINDOW_CLOSE"]["monotonicNs"]
                - by_type["ANALYSIS_WINDOW_OPEN"]["monotonicNs"],
            )
            anchor = by_type["TRIAL_SAMPLE_ANCHOR"]
            onset = by_type["STIMULUS_ONSET"]
            self.assertEqual(onset["sequence"], anchor["stimulusOnsetEventSequence"])
            self.assertEqual(onset["monotonicNs"], anchor["stimulusOnsetMonotonicNs"])
            self.assertGreaterEqual(
                anchor["monotonicNs"], by_type["ANALYSIS_WINDOW_OPEN"]["monotonicNs"]
            )
            self.assertEqual(1000, anchor["sampleAnchorSampleIndex"])
            self.assertEqual([1000, 4000], [len(item["samples"][0]) for item in raw])
            evidence = decoder[0]
            self.assertEqual(1000, evidence["sampleAnchorSampleIndex"])
            self.assertEqual(1500, evidence["analysisWindowStartSample"])
            self.assertEqual(1500, evidence["analysisWindowSampleCount"])
            self.assertEqual(4_000, evidence["samplesAvailableAtDecision"] - 1000)
            self.assertEqual(
                PROTOCOL_TIMING["stimulusAndAnalysisSeconds"],
                manifest["formalProtocolTiming"]["stimulusAndAnalysisSeconds"],
            )
            self.assertEqual(1000, replay_decoded["sampleAnchorSampleIndex"])
            self.assertEqual(1500, replay_decoded["analysisWindowStartSample"])
            self.assertEqual(1500, replay_decoded["analysisWindowSampleCount"])

        first_onset = next(item for item in first[1] if item.get("eventType") == "STIMULUS_ONSET")
        second_onset = next(item for item in second[1] if item.get("eventType") == "STIMULUS_ONSET")
        self.assertEqual(
            int(round(preparation_delta * 1_000_000_000.0)),
            abs(first_onset["monotonicNs"] - second_onset["monotonicNs"]),
        )

    def test_timing_anchor_is_armed_by_stimulus_onset_and_live_wait_uses_it(self):
        """The EEG t=0 anchor must not be the pre-onset buffer stop sample."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "timing-session"
            clock = _FakeClock()
            recorder = HumanSessionRecorder(
                root,
                "timing-anchor-test",
                "live_nd8",
                monotonic_ns=clock.monotonic_ns,
                utc_now=clock.utc_now,
            )

            class CapturingPipeline(GoldenPacketPipeline):
                def __init__(self):
                    super().__init__()
                    self.requested_stop_sample = None

                def wait_for_stop_sample(self, stop_sample, timeout_seconds):
                    self.requested_stop_sample = int(stop_sample)

            pipeline = CapturingPipeline()
            pre_onset = Nd8Packet.from_sdk_payload(
                {"timestamp": 0.0, "data": [[0.0] * 1000 for _ in range(8)]},
                packet_sequence=0,
                receive_monotonic_ns=1_000_000_000,
                receive_utc="2026-01-01T00:00:00Z",
            )
            pipeline(pre_onset, PacketContinuityRecord(0, 0, "initial", ()))

            onset_ns = 2_000_000_000
            pipeline.mark_next_segment(onset_ns, stimulus_onset_event_sequence=17)
            packet_before_onset = Nd8Packet.from_sdk_payload(
                {"timestamp": 1000.0, "data": [[0.0] * 1000 for _ in range(8)]},
                packet_sequence=1,
                receive_monotonic_ns=1_900_000_000,
                receive_utc="2026-01-01T00:00:01Z",
            )
            pipeline(packet_before_onset, PacketContinuityRecord(1, 1000, "continuous", ()))
            packet_at_onset = Nd8Packet.from_sdk_payload(
                {"timestamp": 2000.0, "data": [[0.0] * 1000 for _ in range(8)]},
                packet_sequence=2,
                receive_monotonic_ns=2_001_000_000,
                receive_utc="2026-01-01T00:00:02Z",
            )
            pipeline(packet_at_onset, PacketContinuityRecord(2, 2000, "continuous", ()))

            anchor = pipeline.next_segment_anchor
            self.assertEqual(anchor[1].cumulative_first_sample_index, 2000)
            self.assertEqual(pipeline.next_segment_anchor_metadata["stimulusOnsetEventSequence"], 17)
            self.assertEqual(pipeline.next_segment_anchor_metadata["stimulusOnsetMonotonicNs"], onset_ns)

            source = LiveND8Source(
                "COM11",
                recorder,
                pipeline,
                adapter_factory=lambda *args, **kwargs: object(),
            )
            source.acquire_trial({}, PROTOCOL_TIMING["stimulusAndAnalysisSeconds"])
            self.assertEqual(
                2000 + int(round(PROTOCOL_TIMING["stimulusAndAnalysisSeconds"] * 1000.0)),
                pipeline.requested_stop_sample,
            )

    def test_decoder_window_is_relative_to_onset_sample_anchor(self):
        pipeline = GoldenPacketPipeline()
        pre = Nd8Packet.from_sdk_payload(
            {"timestamp": 0.0, "data": [[0.0] * 1000 for _ in range(8)]},
            packet_sequence=0,
            receive_monotonic_ns=1_000_000_000,
            receive_utc="2026-01-01T00:00:00Z",
        )
        pipeline(pre, PacketContinuityRecord(0, 0, "initial", ()))
        pipeline.mark_next_segment(2_000_000_000, stimulus_onset_event_sequence=9)
        onset_packet = Nd8Packet.from_sdk_payload(
            {"timestamp": 1000.0, "data": [[float(channel)] * 2000 for channel in range(8)]},
            packet_sequence=1,
            receive_monotonic_ns=2_001_000_000,
            receive_utc="2026-01-01T00:00:02Z",
        )
        pipeline(onset_packet, PacketContinuityRecord(1, 1000, "continuous", ()))
        pipeline.backend.predict = lambda epoch: (1, [0.1, 0.8, 0.1])
        decoded = pipeline.predict(pipeline.next_segment_anchor)
        self.assertEqual(1500, decoded["firstEligibleSample"] - decoded["analysisWindowStartSample"])
        self.assertEqual(1000 + 500, decoded["analysisWindowStartSample"])

    def test_protocol_file_is_authority_and_contains_all_segments(self):
        protocol = load_golden_protocol(PROTOCOL)
        self.assertEqual(147, len(protocol["trials"]))
        self.assertEqual(protocol["generatedOrder"], [item["trialId"] for item in protocol["trials"]])
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "altered.json"
            value = json.loads(PROTOCOL.read_text(encoding="utf-8"))
            value["generatedOrder"] = list(reversed(value["generatedOrder"]))
            altered.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(GoldenLauncherError):
                load_golden_protocol(altered)

    def test_live_source_persists_raw_before_downstream_and_does_not_open_on_construct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "session"
            clock = _FakeClock()
            recorder = HumanSessionRecorder(
                root,
                "live-wiring-test",
                "live_nd8",
                monotonic_ns=clock.monotonic_ns,
                utc_now=clock.utc_now,
            )
            raw_path = root / "raw-eeg.jsonl"
            pipeline = _RawFirstPipeline(raw_path)
            holder = {}

            def factory(port, nominal_sampling_rate_hz, live_packet_observer):
                def device_factory(com, eeg_callback, host_callback):
                    holder["device"] = _FakeDevice(host_callback, eeg_callback)
                    return holder["device"]

                return Nd8SerialAdapter(
                    port,
                    nominal_sampling_rate_hz=nominal_sampling_rate_hz,
                    device_factory=device_factory,
                    live_packet_observer=live_packet_observer,
                )

            source = LiveND8Source(
                "COM11",
                recorder,
                pipeline,
                adapter_factory=factory,
            )
            self.assertEqual([], holder.get("device", _FakeDevice(lambda _: None, lambda _: None)).calls)
            source.open_port()
            source.start_streaming()
            holder["device"].emit()
            self.assertEqual(1, pipeline.raw_records_seen_before_downstream)
            self.assertEqual(1, pipeline.packet_count)
            source.close()

    def test_live_mode_requires_confirmation_without_constructing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def factory(*args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("source must not be constructed before live confirmation")

            launcher = GoldenLiveSessionLauncher(
                PROTOCOL,
                Path(directory) / "study",
                mode="live",
                session_id="confirmation-test",
                adapter_factory=factory,
            )
            result = launcher.run()
            self.assertEqual("CONFIRMATION_REQUIRED", result["status"])
            self.assertEqual("pc", result["audioMode"])
            self.assertEqual("pc_winsound", result["audioBackend"])
            self.assertEqual("protocol", result["executionTimingMode"])
            self.assertEqual([], calls)
            manifest = json.loads((Path(directory) / "study" / "confirmation-test" / "manifest.json").read_text())
            self.assertEqual("awaiting_live_confirmation", manifest["status"])
            self.assertEqual("LIVE HUMAN", manifest["mode"])
            self.assertEqual("protocol", manifest["executionTimingMode"])

    def test_live_source_preflight_also_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def factory(*args, **kwargs):
                calls.append(True)
                raise AssertionError("live preflight must not open a source without confirmation")

            launcher = GoldenLiveSessionLauncher(
                PROTOCOL,
                Path(directory) / "study",
                mode="preflight",
                source_mode="live-nd8",
                session_id="preflight-confirmation-test",
                adapter_factory=factory,
            )
            result = launcher.run(specs=[])
            self.assertEqual("CONFIRMATION_REQUIRED", result["status"])
            self.assertEqual([], calls)

    def test_synthetic_preflight_is_separate_from_golden_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(PROTOCOL, Path(directory) / "preflight", source_mode="synthetic")
            self.assertEqual("PASS", result["status"])
            self.assertEqual(4, result["executedSegments"])
            session_root = Path(result["sessionRoot"])
            manifest = json.loads((session_root / "manifest.json").read_text())
            self.assertEqual("PREFLIGHT", manifest["sessionCategory"])
            self.assertEqual("PREFLIGHT", manifest["mode"])
            self.assertEqual("compressed_preflight", manifest["executionTimingMode"])
            self.assertEqual(PROTOCOL_TIMING["preparationSeconds"], manifest["formalProtocolTiming"]["preparationSeconds"])
            self.assertEqual(PROTOCOL_TIMING["stimulusAndAnalysisSeconds"], manifest["formalProtocolTiming"]["stimulusAndAnalysisSeconds"])
            self.assertFalse(manifest["hardwareBoundary"]["com11Opened"])

    def test_synthetic_preflight_pc_audio_is_explicit_and_still_has_no_hardware_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = GoldenLiveSessionLauncher(
                PROTOCOL,
                Path(directory) / "preflight",
                mode="preflight",
                source_mode="synthetic",
                session_id="synthetic-pc-preflight",
                clock=_FakeClock(),
                audio_mode="pc",
            )
            by_slot = {}
            no_intent = None
            for item in launcher.protocol["trials"]:
                if item.get("slot") is None and no_intent is None:
                    no_intent = item
                elif item.get("slot") not in by_slot:
                    by_slot[item["slot"]] = item
            specs = [by_slot[index] for index in (0, 1, 2)] + [no_intent]
            with patch("integration.m13_7_live_launcher.PcToneAudioBackend", _FakePcBackend):
                result = launcher.run(specs=specs)
            self.assertEqual("PASS", result["status"])
            self.assertEqual("pc", result["audioMode"])
            self.assertEqual("pc_winsound", result["audioBackend"])
            self.assertEqual("compressed_preflight", result["audioTimingMode"])
            manifest = json.loads((Path(result["sessionRoot"]) / "manifest.json").read_text())
            self.assertEqual("PREFLIGHT", manifest["sessionCategory"])
            self.assertEqual("pc", manifest["audioConfiguration"]["mode"])
            self.assertEqual("pc_winsound", manifest["audioConfiguration"]["backend"])
            self.assertEqual("compressed_preflight", manifest["audioConfiguration"]["timingMode"])
            self.assertEqual("compressed_preflight", manifest["executionTimingMode"])
            self.assertFalse(manifest["hardwareBoundary"]["com11Opened"])

    def test_explicit_quest_preflight_uses_real_m8_tcp_ack_and_marks_quest_operated(self):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(("127.0.0.1", 0))
        quest_port = reservation.getsockname()[1]
        reservation.close()
        with tempfile.TemporaryDirectory() as directory:
            launcher = GoldenLiveSessionLauncher(
                PROTOCOL,
                Path(directory) / "preflight",
                mode="preflight",
                source_mode="synthetic",
                session_id="quest-m8-preflight",
                clock=_FakeClock(),
                quest_mode="m8_tcp",
                quest_host="127.0.0.1",
                quest_port=quest_port,
            )
            by_slot = {}
            for item in launcher.protocol["trials"]:
                if item.get("block") == "m13_active":
                    by_slot.setdefault(item["slot"], item)
            specs = [by_slot[index] for index in (0, 1, 2)]
            received = []

            def quest_client():
                deadline = time.monotonic() + 5.0
                client = None
                while client is None and time.monotonic() < deadline:
                    try:
                        client = socket.create_connection(("127.0.0.1", quest_port), timeout=1.0)
                    except OSError:
                        time.sleep(0.01)
                if client is None:
                    return
                with client:
                    stream = client.makefile("rwb")
                    for expected_type in ("selection_open", "eeg_selection") * 3:
                        request = json.loads(stream.readline().decode("utf-8"))
                        received.append(request)
                        self.assertEqual(expected_type, request["messageType"])
                        ack = {
                            "protocolVersion": 1,
                            "messageType": "selection_ack",
                            "selectionId": request["selectionId"],
                            "accepted": True,
                        }
                        stream.write((json.dumps(ack) + "\n").encode("utf-8"))
                        stream.flush()

            worker = threading.Thread(target=quest_client)
            worker.start()
            result = launcher.run(specs=specs)
            worker.join(2.0)

            self.assertEqual("PASS", result["status"])
            self.assertEqual(["selection_open", "eeg_selection"] * 3,
                             [item["messageType"] for item in received])
            self.assertTrue(result["hardwareBoundary"]["questOperated"])
            self.assertEqual("m8_selection_tcp", result["questIntegration"]["mode"])
            self.assertGreaterEqual(result["questIntegration"]["ackCount"], 6)
            self.assertEqual("PASS", result["verification"]["status"])

    def test_live_nd8_silent_audio_is_rejected_before_any_source_can_open(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(GoldenLauncherError):
                GoldenLiveSessionLauncher(
                    PROTOCOL,
                    Path(directory) / "live-study",
                    mode="live",
                    source_mode="live-nd8",
                    session_id="live-silent-rejected",
                    audio_mode="silent",
                )

    def test_interrupt_preserves_partial_session_without_finalizing_it(self):
        class InterruptingSource:
            source_type = "synthetic_nd8_replay"

            def acquire_baseline(self, duration_seconds):
                return None

            def acquire_trial(self, spec, duration_seconds):
                raise KeyboardInterrupt()

            def stop(self):
                return None

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            launcher = GoldenLiveSessionLauncher(
                PROTOCOL,
                Path(directory) / "partial",
                mode="simulation",
                session_id="partial-session",
                clock=_FakeClock(),
                source_factory=lambda sink, owner: InterruptingSource(),
            )
            result = launcher.run()
            self.assertEqual("ABORTED_PARTIAL", result["status"])
            manifest = json.loads((Path(result["sessionRoot"]) / "manifest.json").read_text())
            self.assertEqual("aborted_partial", manifest["status"])
            events = [json.loads(line) for line in (Path(result["sessionRoot"]) / "events.jsonl").read_text().splitlines() if line.strip()]
            self.assertTrue(any(item["eventType"] == "TRIAL_INTERRUPTED" for item in events))
            self.assertFalse(any(item["eventType"] == "SESSION_FINALIZED" for item in events))


if __name__ == "__main__":
    unittest.main()
