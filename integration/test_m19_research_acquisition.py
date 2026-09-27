"""Software-only invariants for the M19 Phase-2 Research boundary."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.sample_association.models import PacketContinuityRecord
from integration.m13_7_live_launcher import GoldenPacketPipeline
from integration.m19_research_acquisition import (
    CONDITIONS,
    FakeContinuousSource,
    M19LiveResearchContinuousSource,
    M19ResearchAcquisitionService,
    M19ResearchSession,
    NullLoggingAudioBackend,
    ResearchProtocolError,
    _continuous_resume_offsets,
    build_plan,
    build_smoke_plan,
    save_or_load_plan,
    serve_research_session,
    validate_smoke_plan,
    software_preflight,
    software_resume_preflight,
    validate_plan,
)
from integration.m18_unified_acquisition import SyntheticClock
from integration.m8_selection_orchestration import QuestSelectionTcpServer, QuestSelectionTransportError


class M19PlanContractTests(unittest.TestCase):
    def test_phase2_plan_is_balanced_and_context_relations_replay(self):
        plan = build_plan(190019)
        report = validate_plan(plan)
        self.assertEqual(90, report["formalTrials"])
        self.assertEqual(18, report["focused"])
        self.assertEqual(72, report["natural"])
        natural = [row for row in plan["rows"] if row["group"] == "NATURAL_GAZE"]
        self.assertEqual(Counter({"DEV": 36, "LOCKED_TEST": 36}), Counter(row["split"] for row in natural))
        self.assertEqual(9, len({(row["slotIndex"], row["contextCondition"]) for row in natural if row["split"] == "DEV"}))
        self.assertEqual(9, len({(row["slotIndex"], row["contextCondition"]) for row in natural if row["split"] == "LOCKED_TEST"}))
        for condition in CONDITIONS:
            self.assertEqual(24, sum(row["contextCondition"] == condition for row in natural))

    def test_malformed_target_relation_is_rejected(self):
        plan = build_plan(190019)
        plan["rows"][0]["frequencyHz"] = 99.0
        with self.assertRaises(ResearchProtocolError):
            validate_plan(plan)

    def test_nested_m18_packet_metadata_restores_global_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory) / "packet-metadata.jsonl"
            metadata_path.write_text(json.dumps({
                "packet": {"packet_sequence": 17, "sample_count": 20},
                "continuity": {"packet_sequence": 17, "cumulative_first_sample_index": 340},
            }) + "\n", encoding="utf-8")
            self.assertEqual((18, 360), _continuous_resume_offsets(metadata_path))

    def test_smoke_qc_plan_is_limited_and_never_formal(self):
        plan = build_smoke_plan(190019)
        report = validate_smoke_plan(plan)
        self.assertEqual(6, report["smokeQcTrials"])
        self.assertEqual(0, report["formalTrials"])
        self.assertEqual(Counter({0: 2, 1: 2, 2: 2}), Counter(row["slotIndex"] for row in plan["rows"]))
        self.assertTrue(all(row["formal"] is False for row in plan["rows"]))
        plan["rows"].append(dict(plan["rows"][0], trialId="m19-smoke-qc-over-limit", ordinal=7))
        plan["smokeQcTrialCount"] = 7
        with self.assertRaises(ResearchProtocolError):
            validate_smoke_plan(plan)


class M19ResearchTransportTests(unittest.TestCase):
    def test_live_capture_anchors_after_trigger_arm_and_collects_fixed_4000_samples(self):
        pipeline = GoldenPacketPipeline(selected_channels=tuple(range(8)))

        def inject(sequence, receive_ns):
            first_sample = sequence * 500
            packet = Nd8Packet.from_sdk_payload(
                {
                    "timestamp": first_sample,
                    "data": [[0.0] * 500 for _ in range(8)],
                },
                packet_sequence=sequence,
                receive_monotonic_ns=receive_ns,
                receive_utc="2026-09-27T00:00:00+00:00",
            )
            pipeline(packet, PacketContinuityRecord(
                sequence,
                first_sample,
                "initial" if sequence == 0 else "continuous",
                (),
            ))

        inject(0, 100)
        inject(1, 200)
        source = object.__new__(M19LiveResearchContinuousSource)
        source.started = True
        source.closed = False
        source.pipeline = pipeline
        source.channel_admission = {"verdict": "READY_TEST_FIXTURE"}
        producer_errors = []

        def produce_post_trigger_packets():
            deadline = time.monotonic() + 2.0
            while not pipeline._anchor_capture_armed and time.monotonic() < deadline:
                time.sleep(0.001)
            if not pipeline._anchor_capture_armed:
                producer_errors.append(AssertionError("M19 capture did not arm its segment anchor"))
                return
            trigger_arm_ns = pipeline.next_segment_anchor_metadata["stimulusOnsetMonotonicNs"]
            for sequence in range(2, 10):
                # Packets follow the production adapter's perf-counter clock;
                # TRIAL_ZERO deliberately carries an unrelated test timestamp.
                inject(sequence, trigger_arm_ns + sequence)

        producer = threading.Thread(target=produce_post_trigger_packets, daemon=True)
        producer.start()
        capture = source.capture({"sequence": 42, "monotonicNs": 10**18}, slot=1)
        producer.join(2.0)

        self.assertFalse(producer.is_alive())
        self.assertFalse(producer_errors)
        self.assertEqual(2, capture["anchorPacketSequence"])
        self.assertEqual(1000, capture["startSampleIndexInclusive"])
        self.assertEqual(5000, capture["endSampleIndexExclusive"])
        self.assertEqual(4000, capture["sampleCount"])
        self.assertLess(capture["stimulusOnsetMonotonicNs"], 10**18)
        self.assertEqual("first_packet_received_at_or_after_m19_trigger_arm", capture["anchorBasis"])

    def test_live_preflight_fails_closed_without_confirmation_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "new-session"
            report = software_preflight(root, source="live-nd8", com_port="COM11")
            self.assertEqual("FAIL_CLOSED", report["status"])
            self.assertFalse(report["checks"]["liveConfirmationPresent"])
            self.assertFalse(report["COMOpened"])
            self.assertFalse(root.exists())

    def test_resume_preflight_requires_exact_persisted_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "session"
            plan = save_or_load_plan(root, seed=190019)
            session = M19ResearchSession(root, plan, "resume-test", source_type="synthetic")
            report = software_resume_preflight(root, session_id="resume-test", seed=190019, source="synthetic")
            self.assertEqual("PASS", report["status"])
            wrong_id = software_resume_preflight(root, session_id="different", seed=190019, source="synthetic")
            self.assertEqual("FAIL_CLOSED", wrong_id["status"])
            self.assertFalse(wrong_id["checks"]["resumeSessionIdMatches"])

    def test_pc_offer_allowlist_rejects_nested_hidden_context(self):
        server = QuestSelectionTcpServer()
        server_side, peer_side = socket.socketpair()
        server._connection = server_side
        server._connection_accepted = True
        valid = {
            "protocolVersion": 1,
            "messageType": "m19_research_offer",
            "sessionId": "session-1",
            "trialId": "trial-1",
            "attemptId": "attempt-1",
            "blockId": "FOCUSED_01",
            "ordinal": 1,
            "slotIndex": 1,
            "frequencyHz": 9.0,
            "targetLabel": "BLUE",
            "cueBeepCount": 2,
        }
        try:
            server.send_research_message(valid)
            self.assertEqual("m19_research_offer", json.loads(peer_side.recv(2048).decode("utf-8").strip())["messageType"])
            hidden = dict(valid)
            hidden["metadata"] = {"contextCondition": "ALIGNED"}
            with self.assertRaises(QuestSelectionTransportError):
                server.send_research_message(hidden)
        finally:
            server.close()
            peer_side.close()

    def test_service_pretrigger_cancel_retry_trigger_and_duplicate_are_fail_closed(self):
        class FakeTransport:
            def __init__(self):
                self.messages = []
                self.evidence = {"peer": "software-test"}

            def send_research_message(self, payload):
                self.messages.append(dict(payload))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "research"
            plan = save_or_load_plan(root, seed=190019)
            session = M19ResearchSession(root, plan, "service-test", source_type="synthetic")
            source = FakeContinuousSource(session.stream_recorder, SyntheticClock())
            transport = FakeTransport()
            service = M19ResearchAcquisitionService(session, source, transport, NullLoggingAudioBackend())
            service.cue.sleep = lambda _seconds: None

            service.handle_event({"messageType": "m19_research_ready"})
            first = dict(transport.messages[-1])
            self.assertEqual("m19_research_offer", first["messageType"])
            source.tick(6.0)
            self.assertFalse(any(item.get("eventType") == "TRIAL_ZERO" for item in session._events()))
            service.handle_event({"messageType": "m19_research_ready"})
            service.handle_event({"messageType": "m19_research_cancel", **{key: first[key] for key in ("sessionId", "trialId", "attemptId")}, "reason": "test_cancel"})
            service.handle_event({"messageType": "m19_research_ready"})
            second = dict(transport.messages[-1])
            self.assertEqual(first["trialId"], second["trialId"])
            self.assertNotEqual(first["attemptId"], second["attemptId"])
            trigger = {"messageType": "m19_research_trigger", **{key: second[key] for key in ("sessionId", "trialId", "attemptId")}, "softwareFrame": 300}
            service.handle_event(trigger)
            service.handle_event(trigger)
            self.assertEqual(1, session.next_index)
            completed = [item for item in session._events() if item.get("eventType") == "EPISODE_COMPLETED"]
            self.assertEqual(1, len(completed))
            self.assertEqual(4000, [item for item in session._events() if item.get("eventType") == "TRIAL_CAPTURE_COMPLETE"][0]["sampleCount"])
            trial_zero = next(item for item in session._events() if item.get("eventType") == "TRIAL_ZERO")
            capture_record = json.loads((root / "attempts" / (second["trialId"] + "-attempt-0002.json")).read_text(encoding="utf-8"))
            self.assertEqual(trial_zero["sequence"], capture_record["trialZero"]["eventSequence"])
            self.assertGreaterEqual(capture_record["sampleRange"]["startSampleIndexInclusive"], 6000)
            self.assertTrue(any(item.get("eventType") == "DUPLICATE_OR_STALE_TRIGGER_REJECTED" for item in session._events()))
            forbidden = {"context", "contextcondition", "condition", "split", "targetid", "eeg"}
            self.assertFalse(any(forbidden.intersection(key.lower() for key in message) for message in transport.messages))
            source.close()

    def test_smoke_service_closes_six_nonformal_qc_trials_separately(self):
        class FakeTransport:
            def __init__(self):
                self.messages = []
                self.evidence = {"peer": "software-test"}

            def send_research_message(self, payload):
                self.messages.append(dict(payload))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "smoke-qc"
            plan = build_smoke_plan(190019)
            session = M19ResearchSession(root, plan, "smoke-test", source_type="synthetic")
            source = FakeContinuousSource(session.stream_recorder, SyntheticClock())
            transport = FakeTransport()
            service = M19ResearchAcquisitionService(session, source, transport, NullLoggingAudioBackend())
            service.cue.sleep = lambda _seconds: None
            for _ in range(6):
                service.handle_event({"messageType": "m19_research_ready"})
                offer = dict(transport.messages[-1])
                service.handle_event({"messageType": "m19_research_trigger", **{key: offer[key] for key in ("sessionId", "trialId", "attemptId")}})
            episodes = [item for item in session._events() if item.get("eventType") == "EPISODE_COMPLETED"]
            self.assertTrue(service.completed_session)
            self.assertEqual(6, len(episodes))
            self.assertTrue(all(item.get("formal") is False and item.get("group") == "SMOKE_QC" for item in episodes))
            self.assertEqual(0, session.metadata["completedFormalTrials"])
            self.assertEqual(6, session.metadata["completedQcTrials"])
            self.assertTrue(all("condition" not in key.lower() for message in transport.messages for key in message))
            source.close()

    def test_smoke_capture_rejection_pauses_instead_of_reoffering_same_trial(self):
        class FakeTransport:
            def __init__(self):
                self.messages = []
                self.evidence = {"peer": "software-test"}
                self.poll_count = 0

            def send_research_message(self, payload):
                self.messages.append(dict(payload))

            def accept_research_peer(self):
                return None

            def poll_controller_events(self, _timeout):
                self.poll_count += 1
                if self.poll_count == 1:
                    return [{"messageType": "m19_research_ready"}]
                if self.poll_count == 2:
                    offer = next(item for item in self.messages if item.get("messageType") == "m19_research_offer")
                    identity = {key: offer[key] for key in ("sessionId", "trialId", "attemptId")}
                    return [{"messageType": "m19_research_trigger", **identity}]
                return []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "smoke-qc-failure"
            plan = build_smoke_plan(190019)
            session = M19ResearchSession(root, plan, "smoke-failure-test", source_type="synthetic")
            source = FakeContinuousSource(session.stream_recorder, SyntheticClock(), fault="recorder_failure")
            transport = FakeTransport()
            service = M19ResearchAcquisitionService(session, source, transport, NullLoggingAudioBackend())
            service.cue.sleep = lambda _seconds: None

            service.run_forever()

            offers = [item for item in transport.messages if item.get("messageType") == "m19_research_offer"]
            rejected = [item for item in session._events() if item.get("eventType") == "TRIAL_CAPTURE_REJECTED"]
            self.assertTrue(service.capture_failure_paused)
            self.assertEqual("PAUSED_RESUMABLE", session.metadata["status"])
            self.assertEqual("TRIAL_CAPTURE_REJECTED", session.metadata["pauseReason"])
            self.assertEqual(1, len(offers))
            self.assertEqual(1, len(rejected))
            self.assertEqual(0, session.next_index)
            self.assertEqual(2, transport.poll_count)
            self.assertTrue(source.closed)

    def test_smoke_service_full_local_tcp_rehearsal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tcp-smoke"
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
            reservation.close()
            reports = []
            failures = []

            def run_server():
                try:
                    reports.append(serve_research_session(
                        session_root=root,
                        session_id="tcp-smoke-session",
                        seed=190019,
                        source_name="synthetic",
                        com_port=None,
                        confirm_live_human=False,
                        quest_host="127.0.0.1",
                        quest_port=port,
                        resume=False,
                        cue_backend_name="null",
                        session_kind="SMOKE_QC",
                    ))
                except Exception as error:  # Surface worker-thread failures in the test thread.
                    failures.append(error)

            worker = threading.Thread(target=run_server, daemon=True)
            worker.start()
            client = None
            deadline = time.monotonic() + 5.0
            while client is None and time.monotonic() < deadline:
                try:
                    client = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                except OSError:
                    time.sleep(0.02)
            self.assertIsNotNone(client, "Research service did not open its local software-only listener")
            client.settimeout(8.0)
            received_offers = []
            intermediate_completion_messages = 0
            final_message = None
            with client:
                stream = client.makefile("rwb")
                stream.write((json.dumps({"protocolVersion": 1, "messageType": "m19_research_ready"}) + "\n").encode("utf-8"))
                stream.flush()
                while True:
                    try:
                        line = stream.readline()
                    except socket.timeout:
                        event_path = root / "events.jsonl"
                        observed = event_path.read_text(encoding="utf-8") if event_path.exists() else "<no events file>"
                        stream.close()
                        try:
                            client.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        client.close()
                        worker.join(5.0)
                        self.fail("no Research TCP offer; worker_alive={} reports={} failures={} events={}".format(worker.is_alive(), reports, failures, observed[-6000:]))
                    payload = json.loads(line.decode("utf-8"))
                    message_type = payload["messageType"]
                    if message_type == "m19_research_offer":
                        received_offers.append(payload)
                        self.assertFalse(any(key.lower() in {"context", "contextcondition", "condition", "split", "targetid", "eeg"} for key in payload))
                        identity = {key: payload[key] for key in ("sessionId", "trialId", "attemptId")}
                        ack = {"protocolVersion": 1, "messageType": "m19_research_offer_ack", "accepted": True, **identity}
                        if len(received_offers) == 1:
                            cancel = {"protocolVersion": 1, "messageType": "m19_research_cancel", "reason": "transport_buffer_regression", **identity}
                            ready = {"protocolVersion": 1, "messageType": "m19_research_ready"}
                            stream.write((json.dumps(ack) + "\n" + json.dumps(cancel) + "\n" + json.dumps(ready) + "\n").encode("utf-8"))
                        else:
                            trigger = {"protocolVersion": 1, "messageType": "m19_research_trigger", "softwareFrame": 1000, **identity}
                            stream.write((json.dumps(ack) + "\n" + json.dumps(trigger) + "\n").encode("utf-8"))
                        stream.flush()
                    elif message_type == "m19_research_trial_complete":
                        if payload.get("status") == "complete":
                            intermediate_completion_messages += 1
                        if intermediate_completion_messages <= 5:
                            stream.write((json.dumps({"protocolVersion": 1, "messageType": "m19_research_ready"}) + "\n").encode("utf-8"))
                            stream.flush()
                    elif message_type == "m19_research_session_complete":
                        final_message = payload
                        break
                    else:
                        self.fail("unexpected Research server message: {}".format(message_type))
            worker.join(30.0)
            self.assertFalse(worker.is_alive(), "local smoke session did not complete")
            self.assertFalse(failures, "local smoke session failed: {}".format(failures))
            self.assertEqual(7, len(received_offers))
            self.assertEqual(received_offers[0]["trialId"], received_offers[1]["trialId"])
            self.assertNotEqual(received_offers[0]["attemptId"], received_offers[1]["attemptId"])
            self.assertEqual(5, intermediate_completion_messages)
            self.assertEqual(6, final_message["smokeQcTrialCount"])
            self.assertEqual(0, final_message["formalTrialCount"])
            self.assertEqual("PASS", reports[0]["status"])
            self.assertEqual(0, reports[0]["formalCompleted"])
            self.assertEqual(6, reports[0]["smokeQcCompleted"])
            self.assertFalse(reports[0]["liveHardwareOpened"])


if __name__ == "__main__":
    unittest.main()
