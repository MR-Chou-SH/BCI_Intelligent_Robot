"""Operator launcher and end-to-end M19 orchestration acceptance tests."""

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integration.m8_selection_orchestration import QuestSelectionTcpServer
from integration.m8_selection_transport.simulated_batch_consumer import BatchIdempotentConsumer
from integration.m9_mujoco_execution import (
    DEFAULT_M9_SCENE_BINDINGS,
    create_fr3_umi_mujoco_adapter,
)
from integration.m9_virtual_e2e import make_confirmed_batch
from integration.m19_eeg_backends import M19SyntheticEegBackend
from integration.m19_paged_live_eeg_demo import (
    M19AppendOnlyEventLog,
    M19PagedLiveOrchestrator,
    M19RealtimeTelemetryPacer,
    _create_mujoco_dispatcher,
    run_synthetic_acceptance,
)
from integration.m19_eeg_backends import M19BackendError


def _write_line(stream, payload):
    stream.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
    stream.flush()


def _read_line(stream):
    value = stream.readline()
    if not value:
        raise RuntimeError("Quest server closed before responding")
    return json.loads(value.decode("utf-8"))


def _request(selection_id="service-selection-01"):
    return {
        "contractId": "m19_decode_request_v1",
        "protocolVersion": 1,
        "messageType": "eeg_decode_request",
        "trialId": "service-trial-01",
        "selectionId": selection_id,
        "pageId": "m16-page-1",
        "pageIndex": 0,
        "pageEpoch": 3,
        "createdUtc": "2026-09-22T12:00:00+00:00",
        "presentationPolicy": "demo_preview",
        "activeSlotCount": 3,
        "slots": [
            {"slotIndex": 0, "frequencyHz": 7.2, "targetId": "m9-vblock-yellow-01"},
            {"slotIndex": 1, "frequencyHz": 9.0, "targetId": "m9-vblock-blue-01"},
            {"slotIndex": 2, "frequencyHz": 12.0, "targetId": "m9-vblock-green-01"},
        ],
    }


def _confirmed_batch(target_ids):
    batch_id = "m19-visual-batch"
    payload = make_confirmed_batch(
        target_ids[0],
        batch_id=batch_id,
        selection_id="m19-visual-selection-0",
        slot_index=0,
    )
    for index, target_id in enumerate(target_ids[1:], start=1):
        next_payload = make_confirmed_batch(
            target_id,
            batch_id=batch_id,
            selection_id="m19-visual-selection-{}".format(index),
            slot_index=index,
        )
        payload["confirmedBatch"]["selections"].extend(
            next_payload["confirmedBatch"]["selections"]
        )
    return payload


class M19PagedLiveDemoTests(unittest.TestCase):
    def test_mujoco_dispatcher_requires_quest_telemetry_before_robot_execution(self):
        with patch("integration.m19_paged_live_eeg_demo.create_fr3_umi_mujoco_adapter") as create_adapter:
            with self.assertRaisesRegex(M19BackendError, "telemetry"):
                _create_mujoco_dispatcher(
                    SimpleNamespace(
                        operation="pick_and_place",
                        telemetry_host=None,
                        telemetry_port=11002,
                    ),
                    None,
                    Path("unused-output"),
                )
        create_adapter.assert_not_called()

    def test_mujoco_dispatcher_paces_the_existing_backend_and_targets_quest_tcp_11002(self):
        class FakeBackend:
            def __init__(self):
                self.data = SimpleNamespace(time=0.0)
                self._data = self.data
                self.callback = None

            def set_simulation_step_callback(self, callback):
                self.callback = callback

        backend = FakeBackend()
        adapter = SimpleNamespace(_backend=backend, execute=Mock())
        telemetry = Mock()
        pacer = Mock()
        args = SimpleNamespace(
            operation="pick_and_place",
            telemetry_host="192.0.2.10",
            telemetry_port=11002,
        )
        with patch(
            "integration.m19_paged_live_eeg_demo.create_fr3_umi_mujoco_adapter",
            return_value=adapter,
        ) as create_adapter, patch(
            "integration.m13_9_final_live_acceptance.M139TelemetryCapture",
            return_value=telemetry,
        ) as capture_class, patch(
            "integration.m19_paged_live_eeg_demo.M19RealtimeTelemetryPacer",
            return_value=pacer,
        ) as pacer_class:
            _dispatcher, returned_telemetry = _create_mujoco_dispatcher(
                args, None, Path("unused-output")
            )

        self.assertIs(returned_telemetry, telemetry)
        from integration.m13_6_visual_sync import (
            M13_6_MUJOCO_TABLE_TOP_Z,
            M13_6_VISUAL_STACK_TARGETS,
            M13_6_VISUAL_BLOCK_EDGE_MUJOCO,
            M13_6_VISUAL_BLOCK_EDGE_QUEST,
            m13_6_visual_runtime_anchor_positions_mujoco,
        )

        create_adapter.assert_called_once()
        adapter_options = create_adapter.call_args.kwargs
        self.assertTrue(adapter_options["persistent_world"])
        self.assertTrue(adapter_options["m13_6_visual_scene"])
        self.assertEqual(adapter_options["post_release_settle_steps"], 240)
        self.assertTrue(callable(adapter_options["before_execute_callback"]))
        self.assertTrue(callable(adapter_options["post_release_callback"]))
        phase_anchors = m13_6_visual_runtime_anchor_positions_mujoco()
        front_table_y = M13_6_VISUAL_STACK_TARGETS["block_sim_01"]["position"][1]
        expected_targets = {
            logical_id: (
                phase_anchors[logical_id][0],
                front_table_y,
                M13_6_MUJOCO_TABLE_TOP_Z + M13_6_VISUAL_BLOCK_EDGE_MUJOCO / 2.0,
            )
            for logical_id in DEFAULT_M9_SCENE_BINDINGS
        }
        self.assertEqual(
            adapter_options["placement_targets"],
            tuple(
                expected_targets[logical_id]
                for logical_id in sorted(DEFAULT_M9_SCENE_BINDINGS)
            ),
        )
        resolver = adapter_options["placement_target_resolver"]
        self.assertEqual(
            resolver(None, None, None, SimpleNamespace(logical_block_id="block_sim_03"), {}),
            expected_targets["block_sim_03"],
        )
        capture_class.assert_called_once_with(
            adapter, Path("unused-output") / "m13_6-robot-telemetry.jsonl",
            quest_host="192.0.2.10", quest_port=11002,
            phase_anchor_positions_mujoco=m13_6_visual_runtime_anchor_positions_mujoco(),
            visual_block_size_quest_meters=(M13_6_VISUAL_BLOCK_EDGE_QUEST,) * 3,
        )
        pacer_class.assert_called_once_with(backend.data, telemetry, rate_hz=30.0)
        self.assertIs(backend.callback, pacer.on_simulation_step)

    def _run_real_mujoco_batch(self, target_ids):
        import mujoco
        from integration.m13_9_final_live_acceptance import M139TelemetryCapture

        captures = []

        def create_capture(adapter, output_path, quest_host=None, quest_port=11002, **kwargs):
            # Exercise the real M13.6 sampler and JSONL path while keeping this
            # software rehearsal disconnected from Quest TCP 11002.
            capture = M139TelemetryCapture(
                adapter,
                output_path,
                quest_host=None,
                quest_port=quest_port,
                **kwargs,
            )
            captures.append(capture)
            return capture

        class FastPacer:
            def __init__(self, _data, telemetry, rate_hz=30.0):
                self.telemetry = telemetry
                self.backend = created_adapters[0]._backend
                self.rate_hz = rate_hz
                self.steps = 0
                self.settling_sample_count = 0

            def on_simulation_step(self):
                self.steps += 1
                # 17 x 2 ms is the existing approximate 30 Hz M13.6 cadence.
                if self.steps % 17 == 0:
                    self.telemetry.emit("executing")
                    planner = self.backend._last_planner
                    if planner is not None and planner.phase == "DONE":
                        self.settling_sample_count += 1

        class RecordingEventSink:
            def __init__(self):
                self.events = []

            def emit(self, event_name, **values):
                if event_name == "robot_telemetry_association":
                    self.events.append(values)

        created_adapters = []
        real_factory = create_fr3_umi_mujoco_adapter

        def create_adapter(*args, **kwargs):
            adapter = real_factory(*args, **kwargs)
            created_adapters.append(adapter)
            return adapter

        telemetry_class_path = "integration.m13_9_final_live_acceptance.M139TelemetryCapture"
        event_sink = RecordingEventSink()
        args = SimpleNamespace(
            operation="pick_and_place",
            telemetry_host="127.0.0.1",
            telemetry_port=11002,
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "integration.m19_paged_live_eeg_demo.create_fr3_umi_mujoco_adapter",
            side_effect=create_adapter,
        ), patch(telemetry_class_path, side_effect=create_capture), patch(
            "integration.m19_paged_live_eeg_demo.M19RealtimeTelemetryPacer", FastPacer
        ):
            dispatcher, _telemetry = _create_mujoco_dispatcher(args, event_sink, Path(folder))
            adapter = created_adapters[0]
            backend = adapter._backend
            initial_positions = {}
            for target_id, logical_id in {
                "m9-vblock-blue-01": "block_sim_03",
                "m9-vblock-green-01": "block_sim_02",
                "m9-vblock-red-01": "block_sim_01",
            }.items():
                body_id = mujoco.mj_name2id(
                    backend._model,
                    mujoco.mjtObj.mjOBJ_BODY,
                    DEFAULT_M9_SCENE_BINDINGS[logical_id],
                )
                initial_positions[target_id] = backend._data.xpos[body_id].copy()
            receipt = BatchIdempotentConsumer().accept(_confirmed_batch(target_ids))
            result = dispatcher.dispatch(receipt)

        return result, adapter, initial_positions, event_sink.events, captures[0].frames

    def test_m19_two_item_batch_preserves_blue_after_green_executes(self):
        import numpy as np

        result, adapter, initial, events, frames = self._run_real_mujoco_batch(
            ["m9-vblock-blue-01", "m9-vblock-green-01"]
        )
        self.assertTrue(result.success, result.to_public_dict())
        blue_complete = next(
            item["telemetryFrame"] for item in events
            if item["targetId"] == "m9-vblock-blue-01" and item["simulationState"] == "completed"
        )
        green_start = next(
            item["telemetryFrame"] for item in events
            if item["targetId"] == "m9-vblock-green-01" and item["simulationState"] == "executing"
        )
        blue_id = "block_sim_03"
        blue_pose_after_first = next(
            block["positionMujoco"] for block in blue_complete["blocks"]
            if block["logicalBlockId"] == blue_id
        )
        blue_pose_at_green_start = next(
            block["positionMujoco"] for block in green_start["blocks"]
            if block["logicalBlockId"] == blue_id
        )
        green_complete = next(
            item["telemetryFrame"] for item in events
            if item["targetId"] == "m9-vblock-green-01" and item["simulationState"] == "completed"
        )
        blue_pose_after_green = next(
            block["positionMujoco"] for block in green_complete["blocks"]
            if block["logicalBlockId"] == blue_id
        )
        self.assertLess(np.linalg.norm(np.asarray(blue_pose_after_first) - blue_pose_at_green_start), 0.005)
        self.assertLess(np.linalg.norm(np.asarray(blue_pose_after_first) - blue_pose_after_green), 0.005)
        self.assertGreater(np.linalg.norm(np.asarray(blue_pose_after_green) - initial["m9-vblock-blue-01"]), 0.05)
        self.assertTrue(adapter._backend._persistent_world)
        self.assertEqual(240, adapter._backend._post_release_settle_steps)
        pacer = adapter._backend._simulation_step_callback.__self__
        self.assertGreater(pacer.settling_sample_count, 0)
        self.assertTrue(np.allclose(frames[0]["visualBlockSizeQuestMeters"], (0.056,) * 3))

    def test_m19_three_item_batch_leaves_blue_green_red_at_resting_final_poses(self):
        import mujoco
        import numpy as np
        from integration.m13_6_visual_sync import (
            M13_6_MUJOCO_TABLE_TOP_Z,
            M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST,
            M13_6_VISUAL_BLOCK_EDGE_MUJOCO,
            M13_6_VISUAL_BLOCK_EDGE_QUEST,
            MujocoToQuestTransform,
        )

        result, adapter, initial, _events, frames = self._run_real_mujoco_batch(
            ["m9-vblock-blue-01", "m9-vblock-green-01", "m9-vblock-red-01"]
        )
        self.assertTrue(result.success, result.to_public_dict())
        self.assertEqual(
            ["m9-vblock-blue-01", "m9-vblock-green-01", "m9-vblock-red-01"],
            [item.target_id for item in result.executions],
        )
        self.assertTrue(adapter._backend._persistent_world)
        final_frame = frames[-1]
        self.assertEqual("completed", final_frame["simulationState"])
        for frame in frames:
            self.assertTrue(np.allclose(
                frame["visualBlockSizeQuestMeters"],
                (M13_6_VISUAL_BLOCK_EDGE_QUEST,) * 3,
            ))
        logical_ids = {
            "m9-vblock-blue-01": "block_sim_03",
            "m9-vblock-green-01": "block_sim_02",
            "m9-vblock-red-01": "block_sim_01",
        }
        for target_id, logical_id in logical_ids.items():
            body_id = mujoco.mj_name2id(
                adapter._backend._model,
                mujoco.mjtObj.mjOBJ_BODY,
                DEFAULT_M9_SCENE_BINDINGS[logical_id],
            )
            position = adapter._backend._data.xpos[body_id].copy()
            self.assertGreater(
                np.linalg.norm(position - initial[target_id]), 0.05,
                "{} returned to its initial scene pose".format(target_id),
            )
            self.assertAlmostEqual(
                M13_6_MUJOCO_TABLE_TOP_Z + M13_6_VISUAL_BLOCK_EDGE_MUJOCO / 2.0,
                position[2], delta=0.006,
            )
            final_block = next(
                block["positionMujoco"] for block in final_frame["blocks"]
                if block["logicalBlockId"] == logical_id
            )
            self.assertAlmostEqual(position[2], final_block[2], delta=0.002)
        self.assertLess(
            max(adapter._backend.world_state_evidence()["finalLinearSpeedsMetersPerSecond"].values()),
            0.05,
        )

        anchors = {
            block["logicalBlockId"]: block["positionMujoco"]
            for block in frames[0]["blocks"]
        }
        transform = MujocoToQuestTransform()
        for index, logical_id in enumerate(sorted(anchors)):
            position = anchors[logical_id]
            quest_position = transform.position(position)
            expected = (
                M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST[index][0],
                M13_6_VISUAL_BLOCK_EDGE_QUEST / 2.0,
                M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST[index][2],
            )
            self.assertLess(np.linalg.norm(np.asarray(quest_position) - expected), 1e-5)

    def test_realtime_telemetry_paces_mujoco_steps_and_restarts_for_next_action(self):
        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        class FakeSimulationData:
            time = 0.0

        class FakeTelemetry:
            def __init__(self, clock):
                self.clock = clock
                self.emissions = []

            def emit(self, state):
                self.emissions.append((state, self.clock.monotonic()))

        clock = FakeClock()
        data = FakeSimulationData()
        telemetry = FakeTelemetry(clock)
        pacer = M19RealtimeTelemetryPacer(
            data,
            telemetry,
            rate_hz=30.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        for step in range(91):
            data.time = step * 0.01
            pacer.on_simulation_step()

        first_action_emissions = len(telemetry.emissions)
        self.assertGreaterEqual(first_action_emissions, 26)
        self.assertLessEqual(first_action_emissions, 28)
        self.assertTrue(all(state == "executing" for state, _when in telemetry.emissions))
        intervals = [right[1] - left[1] for left, right in zip(
            telemetry.emissions, telemetry.emissions[1:]
        )]
        self.assertTrue(all(abs(interval - 1.0 / 30.0) <= 0.01 for interval in intervals))
        self.assertAlmostEqual(0.9, clock.monotonic(), delta=0.02)

        data.time = 0.0
        pacer.on_simulation_step()
        data.time = 0.04
        pacer.on_simulation_step()
        self.assertEqual(first_action_emissions + 1, len(telemetry.emissions))

    def test_synthetic_free_order_queue_m9_and_telemetry_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            report = run_synthetic_acceptance(folder)

        self.assertEqual("PASS", report["status"])
        self.assertEqual(
            ["m9-vblock-blue-01", "m9-vblock-red-01", "m9-vblock-green-01"],
            report["committedTargetOrder"],
        )
        self.assertEqual(report["committedTargetOrder"], report["m9ExecutionTargetOrder"])
        self.assertEqual([3], report["internalBatchSizes"])
        self.assertEqual(["m9-vblock-blue-01"], report["selectedPersistenceAfterPageSwitch"])
        self.assertEqual("m9-vblock-yellow-01", report["undoTargetId"])
        associations = report["telemetryAssociations"]
        self.assertEqual(6, len(associations))
        self.assertEqual(
            ["m19-e2e-selection-01", "m19-e2e-selection-02", "m19-e2e-selection-04"],
            [item["selectionId"] for item in associations[::2]],
        )
        self.assertFalse(report["hardwareBoundary"]["comOpened"])
        self.assertFalse(report["hardwareBoundary"]["nd8Operated"])

    def test_runtime_protocol_acknowledges_trigger_then_returns_class_only(self):
        transport = QuestSelectionTcpServer(
            "127.0.0.1", 0, accept_timeout_seconds=3.0, ack_timeout_seconds=3.0
        ).start()
        observed = []
        peer_errors = []
        service = M19PagedLiveOrchestrator(
            transport,
            M19SyntheticEegBackend((1,)),
            event_sink=lambda event_type, payload: observed.append((event_type, payload)),
        )

        def quest_peer():
            try:
                with socket.create_connection(("127.0.0.1", transport.port), timeout=3.0) as client:
                    stream = client.makefile("rwb")
                    _write_line(stream, _request())
                    ack = _read_line(stream)
                    observed.append(("quest_decode_ack", ack))
                    result = _read_line(stream)
                    observed.append(("quest_selection", result))
                    _write_line(stream, {
                        "protocolVersion": 1,
                        "messageType": "selection_ack",
                        "selectionId": result["selectionId"],
                        "accepted": True,
                        "resolvedSlot": 1,
                        "resolvedTargetId": "m9-vblock-blue-01",
                        "rejectionReason": "None",
                    })
            except Exception as error:
                peer_errors.append(error)

        worker = threading.Thread(target=quest_peer)
        worker.start()
        try:
            transport._ensure_connection()
            self.assertEqual(1, service.poll_once(0.1))
        finally:
            transport.close()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], peer_errors)
        ack = next(payload for name, payload in observed if name == "quest_decode_ack")
        selection = next(payload for name, payload in observed if name == "quest_selection")
        self.assertEqual("eeg_decode_ack", ack["messageType"])
        self.assertTrue(ack["accepted"])
        self.assertEqual("eeg_selection", selection["messageType"])
        self.assertEqual(1, selection["predictedClassIndex"])
        self.assertNotIn("targetId", selection)
        self.assertNotIn("resolvedTargetId", selection)
        self.assertIn("selection_acknowledged", [name for name, _payload in observed])

    def test_malformed_runtime_request_is_rejected_before_backend_decode(self):
        transport = QuestSelectionTcpServer(
            "127.0.0.1", 0, accept_timeout_seconds=3.0, ack_timeout_seconds=3.0
        ).start()
        backend = M19SyntheticEegBackend((1,))
        service = M19PagedLiveOrchestrator(transport, backend)
        peer_records = []
        peer_errors = []
        malformed = _request("malformed-selection-01")
        del malformed["slots"]

        def quest_peer():
            try:
                with socket.create_connection(("127.0.0.1", transport.port), timeout=3.0) as client:
                    stream = client.makefile("rwb")
                    _write_line(stream, malformed)
                    peer_records.append(_read_line(stream))
            except Exception as error:
                peer_errors.append(error)

        worker = threading.Thread(target=quest_peer)
        worker.start()
        try:
            transport._ensure_connection()
            self.assertEqual(1, service.poll_once(0.1))
        finally:
            transport.close()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], peer_errors)
        self.assertEqual(1, len(peer_records))
        self.assertEqual("eeg_decode_ack", peer_records[0]["messageType"])
        self.assertFalse(peer_records[0]["accepted"])
        self.assertEqual("invalid_request", peer_records[0]["rejectionReason"])
        self.assertEqual([], backend.calls)

    def test_runtime_no_decision_sends_abort_without_class_index(self):
        transport = QuestSelectionTcpServer(
            "127.0.0.1", 0, accept_timeout_seconds=3.0, ack_timeout_seconds=3.0
        ).start()
        peer_records = []
        peer_errors = []
        service = M19PagedLiveOrchestrator(
            transport,
            M19SyntheticEegBackend((None,)),
        )

        def quest_peer():
            try:
                with socket.create_connection(("127.0.0.1", transport.port), timeout=3.0) as client:
                    stream = client.makefile("rwb")
                    _write_line(stream, _request())
                    peer_records.append(_read_line(stream))
                    peer_records.append(_read_line(stream))
                    _write_line(stream, {
                        "protocolVersion": 1,
                        "messageType": "selection_ack",
                        "selectionId": "service-selection-01",
                        "accepted": True,
                        "rejectionReason": "None",
                    })
            except Exception as error:
                peer_errors.append(error)

        worker = threading.Thread(target=quest_peer)
        worker.start()
        try:
            transport._ensure_connection()
            self.assertEqual(1, service.poll_once(0.1))
        finally:
            transport.close()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], peer_errors)
        ack, abort = peer_records
        self.assertEqual("eeg_decode_ack", ack["messageType"])
        self.assertEqual("selection_abort", abort["messageType"])
        self.assertFalse(abort["decisionMade"])
        self.assertNotIn("predictedClassIndex", abort)
        self.assertNotIn("targetId", abort)

    def test_event_log_reopens_by_appending_with_monotonic_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            first = M19AppendOnlyEventLog(path)
            first.emit("first")
            first.close()
            second = M19AppendOnlyEventLog(path)
            second.emit("second")
            second.close()
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([0, 1], [item["sequence"] for item in records])
        self.assertEqual(["first", "second"], [item["eventType"] for item in records])


if __name__ == "__main__":
    unittest.main()
