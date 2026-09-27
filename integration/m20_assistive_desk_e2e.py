"""Synthetic M20 full-chain regression on the canonical assistive desk.

The runner uses the production M16 queue, M19 in-process decode/registry seam,
M8 confirmed-batch validation, M9 request/dispatch adapter, and existing FR3/UMI
MuJoCo planner. The only synthetic element is the injected EEG class sequence.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np

from integration.m16_paged_queue import (
    Candidate,
    CommitPlan,
    CommittedBatch,
    PagedSelectionQueue,
    confirmed_batch_payloads,
)
from integration.m19_eeg_backends import M19SyntheticEegBackend
from integration.m19_paged_live_eeg_demo import M19InProcessQuestSession
from integration.m8_selection_transport.simulated_batch_consumer import BatchIdempotentConsumer
from integration.m9_batch_dispatch import M9BatchDispatcher
from integration.m9_mujoco_execution import (
    DEFAULT_M9_SCENE_BINDINGS,
    RobotOperation,
    SceneBindingRegistry,
    create_fr3_umi_mujoco_adapter,
)
from integration.m11_context_prediction import ContextPrior
from integration.m12_context_eeg_fusion import (
    ActiveSsvepCandidate,
    fuse_context_and_eeg,
)
from integration.m20_assistive_scene_contract import (
    DEFAULT_SPEC_PATH,
    build_mujoco_scene,
    load_spec,
    validate_spec,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ORDER = (
    "assist_medicine_box",
    "assist_storage_box",
    "assist_phone",
    "assist_button_switch",
    "assist_wireless_charger",
    "assist_user_zone",
)
PHONE_AFFORDANCE_WEIGHTS = (0.40, 0.30, 0.15, 0.15)
PHONE_AFFORDANCE_ORDER = (
    "assist_wireless_charger",
    "assist_storage_box",
    "assist_medicine_box",
    "assist_button_switch",
)


class M20ActionResolutionError(ValueError):
    """An invalid or unsupported source/destination selection pair."""


def _entities(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["semanticId"]: item for item in spec["entities"]}


def candidates_from_spec(spec: dict[str, Any]) -> tuple[Candidate, ...]:
    entities = _entities(spec)
    order = tuple(spec.get("candidateOrderFarToNearLeftToRight", ()))
    if order != EXPECTED_ORDER:
        raise ValueError("M20 candidate ordering changed from the frozen visual order")
    candidates = []
    for semantic_id in order:
        item = entities[semantic_id]
        if not item.get("selectable") or not item.get("slotEligible"):
            raise ValueError("every frozen M20 candidate must remain selectable and slot-eligible")
        candidates.append(Candidate(
            logical_block_id=item["logicalBlockId"],
            target_id=item["targetId"],
            label=item["displayLabel"],
        ))
    return tuple(candidates)


def resolve_source_destination(ordered_selections, spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve the first selected movable source and second compatible destination."""
    selections = tuple(ordered_selections)
    if len(selections) != 2:
        raise M20ActionResolutionError("exactly one source and one destination are required")
    entities = _entities(spec)
    by_target = {
        item["targetId"]: item for item in entities.values()
        if item.get("selectable")
    }
    source = by_target.get(selections[0].target_id)
    destination = by_target.get(selections[1].target_id)
    if source is None or destination is None:
        raise M20ActionResolutionError("selection pair contains an unknown TargetId")
    if source.get("role") != "movable_source" or not source.get("grasp", {}).get("enabled"):
        raise M20ActionResolutionError("first selection is not an enabled movable source")
    if destination.get("role") != "fixed_destination":
        raise M20ActionResolutionError("second selection is not a fixed placement destination")
    placement = next((
        item for item in source.get("placements", ())
        if item.get("targetId") == destination["targetId"]
    ), None)
    if placement is None:
        raise M20ActionResolutionError("source/destination pair is not in the canonical placement allowlist")
    position = placement["positionMeters"]
    return {
        "sourceTargetId": source["targetId"],
        "sourceLogicalBlockId": source["logicalBlockId"],
        "sourceSemanticId": source["semanticId"],
        "destinationTargetId": destination["targetId"],
        "destinationSemanticId": destination["semanticId"],
        "placementPositionTableLocalMeters": [float(position[key]) for key in ("x", "y", "z")],
        "placementYawDegrees": float(placement.get("yawDegrees", 0.0)),
        "resolutionProvenance": "canonical_m20_source_placements_after_ordered_submit",
    }


def _scene_context_prior(history_logical_ids, spec: dict[str, Any]) -> ContextPrior:
    """Create an analysis-only M20 affordance prior from visible selected history."""
    entities = _entities(spec)
    by_logical = {
        item["logicalBlockId"]: item["semanticId"] for item in entities.values()
        if item.get("selectable")
    }
    history = tuple(history_logical_ids)
    if any(item not in by_logical for item in history):
        raise ValueError("Context history contains an unknown M20 logical ID")
    phone_selected = "assist_phone" in tuple(by_logical[item] for item in history)
    if phone_selected:
        probabilities = dict(zip(PHONE_AFFORDANCE_ORDER, PHONE_AFFORDANCE_WEIGHTS))
    else:
        probabilities = {semantic_id: 0.25 for semantic_id in PHONE_AFFORDANCE_ORDER}
    entity_by_semantic = entities
    logical_probabilities = tuple(
        (entity_by_semantic[semantic_id]["logicalBlockId"], float(probability))
        for semantic_id, probability in probabilities.items()
    )
    ordered = sorted(probabilities.items(), key=lambda item: (-item[1], item[0]))
    maximum = max(probabilities.values())
    top_semantics = tuple(
        semantic_id for semantic_id in PHONE_AFFORDANCE_ORDER
        if math.isclose(probabilities[semantic_id], maximum, abs_tol=1e-12)
    )
    entropy = -sum(value * math.log(value) for value in probabilities.values() if value > 0)
    return ContextPrior(
        observable_history=history,
        available_logical_block_ids=tuple(item["logicalBlockId"] for item in entities.values() if item.get("selectable")),
        step_index=len(history),
        candidate_task_count=1,
        task_hypotheses=(),
        next_target_probabilities=logical_probabilities,
        top_targets=tuple(entity_by_semantic[item]["logicalBlockId"] for item in top_semantics),
        tie=len(top_semantics) > 1,
        entropy=entropy,
        terminal=False,
        valid=True,
    )


def context_affordance_evidence(spec: dict[str, Any]) -> dict[str, Any]:
    """Record aligned, neutral, and strong-EEG-conflict Context examples."""
    entities = _entities(spec)
    page_one_candidates = tuple(
        ActiveSsvepCandidate(
            slot_index=slot,
            target_id=entities[semantic_id]["targetId"],
            logical_block_id=entities[semantic_id]["logicalBlockId"],
            nominal_frequency_hz=(7.2, 9.0, 12.0)[slot],
        )
        for slot, semantic_id in enumerate((
            "assist_medicine_box", "assist_storage_box", "assist_phone"
        ))
    )
    page_two_candidates = tuple(
        ActiveSsvepCandidate(
            slot_index=slot,
            target_id=entities[semantic_id]["targetId"],
            logical_block_id=entities[semantic_id]["logicalBlockId"],
            nominal_frequency_hz=(7.2, 9.0, 12.0)[slot],
        )
        for slot, semantic_id in enumerate((
            "assist_button_switch", "assist_wireless_charger", "assist_user_zone"
        ))
    )
    phone_logical_id = entities["assist_phone"]["logicalBlockId"]
    phone_prior = _scene_context_prior((phone_logical_id,), spec)
    global_probabilities = {
        entities[semantic_id]["logicalBlockId"]: probability
        for semantic_id, probability in zip(PHONE_AFFORDANCE_ORDER, PHONE_AFFORDANCE_WEIGHTS)
    }
    phone_rank = [
        {"targetId": semantic_id, "probability": probability}
        for semantic_id, probability in zip(PHONE_AFFORDANCE_ORDER, PHONE_AFFORDANCE_WEIGHTS)
    ]
    aligned = fuse_context_and_eeg(
        phone_prior,
        page_two_candidates,
        {
            entities["assist_button_switch"]["logicalBlockId"]: 0.30,
            entities["assist_wireless_charger"]["logicalBlockId"]: 0.45,
            entities["assist_user_zone"]["logicalBlockId"]: 0.25,
        },
    )
    neutral = fuse_context_and_eeg(
        None, page_two_candidates,
        {item.logical_block_id: 1.0 for item in page_two_candidates},
    )
    conflict = fuse_context_and_eeg(
        phone_prior,
        page_one_candidates,
        {
            entities["assist_medicine_box"]["logicalBlockId"]: 0.90,
            entities["assist_storage_box"]["logicalBlockId"]: 0.08,
            entities["assist_phone"]["logicalBlockId"]: 0.02,
        },
    )
    conflict_dict = conflict.to_public_dict()
    context_top = phone_prior.top_targets[0]
    return {
        "status": "PASS" if (
            [item["targetId"] for item in phone_rank] == list(PHONE_AFFORDANCE_ORDER)
            and math.isclose(phone_rank[2]["probability"], phone_rank[3]["probability"])
            and aligned.fusion_mode == "context_assisted"
            and aligned.top_target_ids == ("assist_wireless_charger",)
            and neutral.fusion_mode == "context_neutral"
            and conflict.is_strong_eeg
            and conflict.fusion_mode == "eeg_strong_override"
            and conflict_dict["finalPrediction"] == entities["assist_medicine_box"]["logicalBlockId"]
            and context_top != conflict_dict["finalPrediction"]
        ) else "FAIL",
        "sourceHistoryLogicalIds": [phone_logical_id],
        "phoneAffordancePrior": {
            "interpretation": "rule-based normalized semantic affordance weights; not calibrated probabilities",
            "vector": phone_rank,
            "requiredOrderPass": [item["targetId"] for item in phone_rank] == list(PHONE_AFFORDANCE_ORDER),
        },
        "alignedFusion": {
            "activeCandidates": [item.to_public_dict() for item in page_two_candidates],
            "rawSyntheticEegScores": {
                entities["assist_button_switch"]["logicalBlockId"]: 0.30,
                entities["assist_wireless_charger"]["logicalBlockId"]: 0.45,
                entities["assist_user_zone"]["logicalBlockId"]: 0.25,
            },
            "fusion": aligned.to_public_dict(),
            "contextAcceleratesRankingPass": aligned.fusion_mode == "context_assisted" and aligned.top_target_ids == ("assist_wireless_charger",),
        },
        "neutralFusion": neutral.to_public_dict(),
        "strongEegConflict": {
            "activeCandidates": [item.to_public_dict() for item in page_one_candidates],
            "contextPriorLogical": global_probabilities,
            "rawSyntheticEegScores": {
                entities["assist_medicine_box"]["logicalBlockId"]: 0.90,
                entities["assist_storage_box"]["logicalBlockId"]: 0.08,
                entities["assist_phone"]["logicalBlockId"]: 0.02,
            },
            "contextTopLogicalBlockId": context_top,
            "fusion": conflict_dict,
            "overridePass": conflict.is_strong_eeg and conflict.fusion_mode == "eeg_strong_override",
        },
    }


def _make_session(spec, decisions, selection_prefix):
    event_trace = []
    queue = PagedSelectionQueue(candidates_from_spec(spec))
    backend = M19SyntheticEegBackend(decisions)
    session = M19InProcessQuestSession(
        queue, backend, event_sink=lambda event, payload: event_trace.append({"event": event, **payload}),
        selection_prefix=selection_prefix,
    )
    return queue, backend, session, event_trace


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def selection_regression(spec: dict[str, Any]) -> dict[str, Any]:
    """Exercise real M16/M19 queue semantics, including an undone selection."""
    queue, backend, session, event_trace = _make_session(
        spec, (0, 1, 2), "m20-selection-regression"
    )
    pages = []
    triggers = []

    def record_page(action):
        page = queue.current_page
        pages.append({
            "action": action,
            "pageIndex": page.page_index,
            "pageId": page.page_id,
            "pageEpoch": page.page_epoch,
            "candidates": [{
                "slotIndex": item.slot_index,
                "targetId": item.target_id,
                "logicalBlockId": item.logical_block_id,
                "frequencyHz": item.nominal_frequency_hz,
                "active": item.active,
                "selected": item.selected,
            } for item in page.candidates],
            "globalQueue": list(queue.selected_target_ids),
        })

    record_page("initial")
    first = session.trigger()
    _assert(first["result"].get("accepted"), "synthetic EEG failed to select medicine")
    triggers.append(first)
    _assert(queue.selected_target_ids == ("assist_medicine_box",), "source selection order changed")
    record_page("medicine_selected")
    _assert(session.navigate_next(), "Next did not advance to Page 2")
    record_page("next_to_page_2")
    second = session.trigger()
    _assert(second["result"].get("accepted"), "synthetic EEG failed to select temporary charger")
    triggers.append(second)
    record_page("temporary_charger_selected")
    _assert(session.navigate_previous(), "Previous did not return to Page 1")
    record_page("previous_to_page_1")
    medicine = next(item for item in queue.current_page.candidates if item.target_id == "assist_medicine_box")
    _assert(medicine.selected and not medicine.active, "selected target did not persist across pages")
    undone = session.undo_last()
    _assert(undone is not None and undone.target_id == "assist_wireless_charger", "Undo Last did not remove the latest global item")
    record_page("undo_charger")
    _assert(session.navigate_next(), "Next after Undo did not advance")
    charger = next(item for item in queue.current_page.candidates if item.target_id == "assist_wireless_charger")
    _assert(charger.active and not charger.selected, "Undo did not restore the destination to an active browse state")
    record_page("charger_restored_active")
    third = session.trigger()
    _assert(third["result"].get("accepted"), "synthetic EEG failed to select USER ZONE")
    triggers.append(third)
    record_page("zone_selected")
    submitted = session.submit()
    _assert(submitted.accepted and submitted.plan is not None, "Submit did not commit the global queue")
    ordered = tuple(item.target_id for item in submitted.plan.ordered_selections)
    _assert(ordered == ("assist_medicine_box", "assist_user_zone"), "Submit changed global user order")
    _assert(tuple(item.slot_index for item in submitted.plan.ordered_selections) == (0, 2), "slot mapping changed")

    stale_queue, stale_backend, stale_session, stale_events = _make_session(
        spec, (), "m20-stale-result"
    )
    # Leave a trial pending, then navigate before its result is accepted.
    stale_trial = stale_queue.open_trial("m20-stale-after-navigation")
    old_candidate = stale_trial.candidate_for_slot(0)
    _assert(stale_session.navigate_next(), "stale-result test could not navigate")
    stale_after_navigation = stale_queue.accept_result(
        stale_trial.selection_id, 0,
        target_id=old_candidate.target_id,
        page_epoch=stale_trial.page_epoch,
    )
    _assert(not stale_after_navigation.accepted and stale_after_navigation.reason == "STALE_SELECTION_REJECTED", "stale page result was not rejected fail-closed")
    _assert(not stale_queue.selected_target_ids, "stale result mutated the queue")

    active_queue, active_backend, active_session, _active_events = _make_session(
        spec, (0,), "m20-active-slot-rearm"
    )
    active_trial = active_queue.open_trial("m20-active-slot-check")
    invalid_active = active_queue.accept_result(active_trial.selection_id, 3)
    _assert(not invalid_active.accepted and invalid_active.reason == "slot_invalid", "invalid active slot did not fail closed")
    valid_active = active_queue.accept_result(
        active_trial.selection_id, 1,
        target_id=active_trial.candidate_for_slot(1).target_id,
        page_epoch=active_trial.page_epoch,
    )
    _assert(valid_active.accepted, "valid active slot was blocked after invalid slot")
    rearmed = active_session.trigger()
    _assert(rearmed["result"].get("accepted"), "Trigger did not re-arm after a completed synthetic selection")
    _assert(len(active_backend.calls) == 1, "synthetic backend trigger accounting is inconsistent")

    return {
        "status": "PASS",
        "candidateOrder": [item.target_id for item in candidates_from_spec(spec)],
        "pageSize": 3,
        "slotFrequenciesHz": [7.2, 9.0, 12.0],
        "pagesAndQueueSnapshots": pages,
        "triggerTrace": [{
            "pageId": item["request"]["pageId"],
            "pageEpoch": item["request"]["pageEpoch"],
            "slots": item["request"]["slots"],
            "decodedClassIndex": item["decoder"].get("classIndex"),
            "accepted": item["result"].get("accepted"),
            "selectedTargetIds": item["selectedTargetIds"],
        } for item in triggers],
        "navigation": {"next": True, "previous": True, "crossPageSelection": True},
        "undo": {"removedTargetId": undone.target_id, "restoredActive": charger.active and not charger.selected},
        "submit": {"accepted": submitted.accepted, "orderedTargetIds": list(ordered), "state": queue.state},
        "staleResult": {
            "oldPageId": stale_trial.page_id,
            "oldPageEpoch": stale_trial.page_epoch,
            "currentPageId": stale_queue.current_page.page_id,
            "currentPageEpoch": stale_queue.page_epoch,
            "rejectedReason": stale_after_navigation.reason,
            "queueUnchanged": not stale_queue.selected_target_ids,
        },
        "activeSlotAndTriggerRearm": {
            "invalidSlotReason": invalid_active.reason,
            "validSlotAccepted": valid_active.accepted,
            "nextTriggerAccepted": rearmed["result"].get("accepted"),
        },
        "runtimeEvents": event_trace,
    }


def _select_pair(spec, source_target, destination_target, decisions, prefix):
    queue, backend, session, events = _make_session(spec, decisions, prefix)
    source_page_index = next(
        page_index for page_index in range(queue.page_count)
        if any(item.target_id == source_target for item in _page_for(queue, page_index).candidates)
    )
    source_slot = next(
        item.slot_index for item in _page_for(queue, source_page_index).candidates
        if item.target_id == source_target
    )
    if source_page_index != queue.page_index:
        _assert(queue.navigate_next(), "source page navigation failed")
        while queue.page_index < source_page_index:
            _assert(queue.navigate_next(), "source page navigation failed")
    source_result = session.trigger()
    _assert(source_result["result"].get("accepted"), "synthetic source selection failed")
    destination_page_index = next(
        page_index for page_index in range(queue.page_count)
        if any(item.target_id == destination_target for item in _page_for(queue, page_index).candidates)
    )
    while queue.page_index < destination_page_index:
        _assert(session.navigate_next(), "destination page navigation failed")
    destination_slot = next(
        item.slot_index for item in queue.current_page.candidates
        if item.target_id == destination_target
    )
    destination_result = session.trigger()
    _assert(destination_result["result"].get("accepted"), "synthetic destination selection failed")
    submitted = session.submit()
    _assert(submitted.accepted and submitted.plan is not None, "pair Submit failed")
    actual = tuple(item.target_id for item in submitted.plan.ordered_selections)
    _assert(actual == (source_target, destination_target), "pair Submit changed source/destination order")
    return queue, backend, session, events, submitted.plan, {
        "sourceClassIndex": source_slot,
        "destinationClassIndex": destination_slot,
        "sourceTrigger": source_result,
        "destinationTrigger": destination_result,
    }


def _page_for(queue, index):
    original = queue.page_index
    while queue.page_index < index:
        queue.navigate_next()
    while queue.page_index > index:
        queue.navigate_previous()
    result = queue.current_page
    while queue.page_index < original:
        queue.navigate_next()
    while queue.page_index > original:
        queue.navigate_previous()
    return result


def _new_attempt_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        attempt = output_dir / "attempt-{:02d}".format(index)
        if not attempt.exists():
            attempt.mkdir()
            return attempt
        index += 1


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite M20 evidence: {}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _make_mujoco_dispatcher(spec, action, model_data_holder):
    entities = _entities(spec)
    selectable = {key: value for key, value in entities.items() if value.get("selectable")}
    target_to_logical = {item["targetId"]: item["logicalBlockId"] for item in selectable.values()}
    logical_to_name = {item["logicalBlockId"]: key for key, item in selectable.items()}
    destination_target = action["destinationTargetId"]
    source_entity = selectable[action["sourceSemanticId"]]
    table_top = float(spec["table"]["mujocoTopSurfaceWorldZMeters"])

    def scene_builder():
        model, data = build_mujoco_scene(spec)
        model_data_holder["model"] = model
        model_data_holder["data"] = data
        return model, data

    def resolve_placement(_build_slot_index, _model, _data, simulator_object, _slot_bindings):
        if simulator_object.simulator_object_name != source_entity["semanticId"]:
            raise M20ActionResolutionError("M9 resolved an unexpected source object")
        placement = next((
            item for item in source_entity.get("placements", ())
            if item.get("targetId") == destination_target
        ), None)
        if placement is None:
            raise M20ActionResolutionError("canonical placement disappeared before execution")
        local = placement["positionMeters"]
        return (float(local["x"]), float(local["y"]), table_top + float(local["z"]))

    robot_root = str(ROOT / "robot_arm")
    if robot_root not in sys.path:
        sys.path.insert(0, robot_root)
    from control.gripper_planner import GripperGraspPlanner

    class M20AssistiveDeskGraspPlanner(GripperGraspPlanner):
        """Reuse M9 trajectories and model a confirmed thin-phone grasp."""

        def __init__(self, model, data, obj, **kwargs):
            # M9's UMI fingertip extends 76.6 mm below the pad center. Keep the
            # M20 fingers clear of the tabletop during its top-surface release.
            if kwargs.get("place"):
                kwargs["drop_clear"] = 0.08
            super().__init__(model, data, obj, **kwargs)
            if obj != "assist_phone":
                return

            for geom_id in range(int(model.ngeom)):
                body_id = int(model.geom_bodyid[geom_id])
                body_name = mujoco.mj_id2name(
                    model, mujoco.mjtObj.mjOBJ_BODY, body_id
                ) or ""
                if body_name.startswith("umi_") or body_name == "assist_phone":
                    model.geom_friction[geom_id][0] = 4.0
            self._m20_phone_weld_id = int(mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_EQUALITY, "m20_phone_grasp_weld"
            ))
            self._m20_gripper_body_id = int(mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, "umi_umi_gripper_base"
            ))
            if min(self._m20_phone_weld_id, self._m20_gripper_body_id, int(self.obj_bid)) < 0:
                raise RuntimeError("M20 phone grasp constraint is missing from the scene")
            self.m20PhoneWeldActivated = False
            self.m20PhoneWeldReleased = False
            self.m20PhoneWeldAnchorErrorMeters = None
            self.m20PhoneWeldOrientationErrorRadians = None
            self.m20PhoneBilateralContactBodies = []

        @staticmethod
        def _quat_multiply(left, right):
            lw, lx, ly, lz = (float(value) for value in left)
            rw, rx, ry, rz = (float(value) for value in right)
            return np.asarray((
                lw * rw - lx * rx - ly * ry - lz * rz,
                lw * rx + lx * rw + ly * rz - lz * ry,
                lw * ry - lx * rz + ly * rw + lz * rx,
                lw * rz + lx * ry - ly * rx + lz * rw,
            ), dtype=float)

        def _m20_phone_contact_sides(self):
            sides = set()
            for contact_index in range(int(self.data.ncon)):
                contact = self.data.contact[contact_index]
                if contact.geom1 in self.og:
                    other_geom = int(contact.geom2)
                elif contact.geom2 in self.og:
                    other_geom = int(contact.geom1)
                else:
                    continue
                body_name = mujoco.mj_id2name(
                    self.model,
                    mujoco.mjtObj.mjOBJ_BODY,
                    int(self.model.geom_bodyid[other_geom]),
                ) or ""
                if body_name in ("umi_left_finger_holder", "umi_right_finger_holder"):
                    sides.add(body_name)
            return sides

        def _m20_attach_phone_weld(self):
            gripper_body = self._m20_gripper_body_id
            phone_body = int(self.obj_bid)
            gripper_position = np.asarray(self.data.xpos[gripper_body], dtype=float)
            phone_position = np.asarray(self.data.xpos[phone_body], dtype=float)
            gripper_quaternion = np.asarray(self.data.xquat[gripper_body], dtype=float)
            phone_quaternion = np.asarray(self.data.xquat[phone_body], dtype=float)
            phone_rotation = np.asarray(
                self.data.xmat[phone_body], dtype=float
            ).reshape(3, 3)
            gripper_inverse = np.asarray((
                gripper_quaternion[0],
                -gripper_quaternion[1],
                -gripper_quaternion[2],
                -gripper_quaternion[3],
            ))
            phone_anchor_local = phone_rotation.T @ (gripper_position - phone_position)
            relative_quaternion = self._quat_multiply(
                gripper_inverse, phone_quaternion
            )
            equality_data = self.model.eq_data[self._m20_phone_weld_id]
            equality_data[0:3] = phone_anchor_local
            equality_data[3:6] = (0.0, 0.0, 0.0)
            equality_data[6:10] = relative_quaternion

            gripper_rotation = np.asarray(
                self.data.xmat[gripper_body], dtype=float
            ).reshape(3, 3)
            gripper_anchor_world = gripper_position + gripper_rotation @ equality_data[3:6]
            phone_anchor_world = phone_position + phone_rotation @ equality_data[0:3]
            anchor_error = float(np.linalg.norm(gripper_anchor_world - phone_anchor_world))
            orientation_error_quaternion = self._quat_multiply(
                self._quat_multiply(
                    np.asarray((
                        phone_quaternion[0], -phone_quaternion[1],
                        -phone_quaternion[2], -phone_quaternion[3],
                    )),
                    self._quat_multiply(gripper_quaternion, relative_quaternion),
                ),
                np.asarray((1.0, 0.0, 0.0, 0.0)),
            )
            orientation_error = 2.0 * math.acos(min(
                1.0, abs(float(orientation_error_quaternion[0]))
            ))
            if anchor_error > 1e-5 or orientation_error > 1e-4:
                raise RuntimeError("M20 phone grasp constraint would snap the object")
            self.data.eq_active[self._m20_phone_weld_id] = True
            mujoco.mj_forward(self.model, self.data)
            self.m20PhoneWeldActivated = True
            self.m20PhoneWeldAnchorErrorMeters = anchor_error
            self.m20PhoneWeldOrientationErrorRadians = orientation_error
            self.m20PhoneBilateralContactBodies = sorted(self._m20_phone_contact_sides())

        def _m20_release_phone_weld(self):
            self.data.eq_active[self._m20_phone_weld_id] = False
            mujoco.mj_forward(self.model, self.data)
            self.m20PhoneWeldReleased = True

        def step(self):
            previous_phase = str(self.phase)
            result = super().step()
            if self.obj != "assist_phone":
                return result

            if not self.m20PhoneWeldActivated and self.phase == "LIFT" and self.grabbed:
                contact_sides = self._m20_phone_contact_sides()
                required_sides = {"umi_left_finger_holder", "umi_right_finger_holder"}
                if not required_sides.issubset(contact_sides):
                    self.result = "grasp_fail"
                    self._next("DONE")
                else:
                    self._m20_attach_phone_weld()

            if (
                self.m20PhoneWeldActivated
                and not self.m20PhoneWeldReleased
                and self.phase == "RELEASE"
                and previous_phase == "RELEASE"
                and float(self.data.ctrl[7]) <= 0.020
            ):
                self._m20_release_phone_weld()
            return result

    adapter = create_fr3_umi_mujoco_adapter(
        scene_bindings=SceneBindingRegistry(logical_to_name),
        scene_builder=scene_builder,
        planner_class=M20AssistiveDeskGraspPlanner,
        persistent_world=True,
        placement_targets=(
            tuple(
                float(value)
                for value in (
                    action["placementPositionTableLocalMeters"][0],
                    action["placementPositionTableLocalMeters"][1],
                    table_top + action["placementPositionTableLocalMeters"][2],
                )
            ),
        ) * 4,
        placement_target_resolver=resolve_placement,
        post_release_settle_steps=600,
        capture_trajectory=True,
    )
    contact_trace = []
    gripper_table_clearance = {"minimumMeters": None, "phase": None, "gripperGeom": None}
    contact_phase = {"name": None, "entryZ": None, "minZ": None, "maxZ": None, "maxObjectGripperContacts": 0, "contactPairs": []}

    def capture_contact_step():
        backend = adapter._backend
        planner = backend._last_planner
        if planner is None:
            return
        phase = str(planner.phase)
        body_id = int(planner.obj_bid)
        object_z = float(model_data_holder["data"].xpos[body_id][2])
        contacts = []
        model = model_data_holder["model"]
        data = model_data_holder["data"]
        table_geom_id = int(mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, "assist_tabletop"
        ))
        for finger_body_name in ("umi_left_finger_holder", "umi_right_finger_holder"):
            finger_body_id = int(mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, finger_body_name
            ))
            for geom_id in range(int(model.ngeom)):
                if int(model.geom_bodyid[geom_id]) != finger_body_id:
                    continue
                if not (int(model.geom_contype[geom_id]) or int(model.geom_conaffinity[geom_id])):
                    continue
                distance = float(mujoco.mj_geomDistance(
                    model, data, geom_id, table_geom_id, 1.0, np.zeros(6, dtype=float)
                ))
                if (
                    gripper_table_clearance["minimumMeters"] is None
                    or distance < gripper_table_clearance["minimumMeters"]
                ):
                    gripper_table_clearance.update({
                        "minimumMeters": distance,
                        "phase": phase,
                        "gripperGeom": mujoco.mj_id2name(
                            model, mujoco.mjtObj.mjOBJ_GEOM, geom_id
                        ),
                    })
        object_geom_ids = set(planner.og)
        for index in range(int(data.ncon)):
            contact = data.contact[index]
            if contact.geom1 in object_geom_ids or contact.geom2 in object_geom_ids:
                other_geom = contact.geom2 if contact.geom1 in object_geom_ids else contact.geom1
                other_body = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[other_geom]) or ""
                other_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, other_geom) or ""
                if other_body.startswith("umi_"):
                    contacts.append({"body": other_body, "geom": other_name})
        if contact_phase["name"] != phase:
            if contact_phase["name"] is not None:
                contact_trace.append(dict(contact_phase))
            contact_phase.update({
                "name": phase,
                "entryZ": object_z,
                "minZ": object_z,
                "maxZ": object_z,
                "maxObjectGripperContacts": len(contacts),
                "contactPairs": contacts,
            })
        else:
            contact_phase["minZ"] = min(contact_phase["minZ"], object_z)
            contact_phase["maxZ"] = max(contact_phase["maxZ"], object_z)
            if len(contacts) > contact_phase["maxObjectGripperContacts"]:
                contact_phase["maxObjectGripperContacts"] = len(contacts)
                contact_phase["contactPairs"] = contacts

    import mujoco
    adapter._backend.set_simulation_step_callback(capture_contact_step)
    model_data_holder["contactTrace"] = contact_trace
    model_data_holder["gripperTableClearance"] = gripper_table_clearance
    dispatcher = M9BatchDispatcher(
        target_to_logical,
        adapter,
        requested_operation=RobotOperation.PICK_AND_PLACE,
        request_id_factory=lambda: "m20-request-" + action["sourceSemanticId"],
    )
    return dispatcher


def _execute_action(plan: CommitPlan, action: dict[str, Any], spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_entry = plan.ordered_selections[0]
    source_only_plan = CommitPlan(
        ordered_selections=(source_entry,),
        batches=(CommittedBatch(0, (source_entry,)),),
    )
    source_payload = confirmed_batch_payloads(
        source_only_plan, batch_id_prefix="m20-resolved-action-" + action["sourceSemanticId"]
    )[0]
    receipt = BatchIdempotentConsumer().accept(source_payload)
    holder: dict[str, Any] = {}
    dispatcher = _make_mujoco_dispatcher(spec, action, holder)
    dispatched = dispatcher.dispatch(receipt)
    world_evidence = dispatcher._robot_adapter.world_state_evidence()
    attempt = dispatched.executions[0] if dispatched.executions else None
    result = None if attempt is None else attempt.result
    model, data = holder["model"], holder["data"]
    import mujoco
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, action["sourceSemanticId"])
    body_position = [float(value) for value in data.xpos[body_id]]
    gripper_table_clearance = holder["gripperTableClearance"]
    linear_velocity = [float(value) for value in data.cvel[body_id][3:6]]
    speed = float(math.sqrt(sum(value * value for value in linear_velocity)))
    backend = dispatcher._robot_adapter._backend
    planner = backend._last_planner
    phone_grasp_evidence = None
    if planner is not None and action["sourceSemanticId"] == "assist_phone":
        phone_grasp_evidence = {
            "mechanism": "bilateral_contact_confirmed_runtime_body_weld",
            "activated": bool(planner.m20PhoneWeldActivated),
            "released": bool(planner.m20PhoneWeldReleased),
            "bilateralContactBodies": list(planner.m20PhoneBilateralContactBodies),
            "activationAnchorErrorMeters": planner.m20PhoneWeldAnchorErrorMeters,
            "activationOrientationErrorRadians": planner.m20PhoneWeldOrientationErrorRadians,
        }
    if backend._last_planner is not None:
        final_phase = str(backend._last_planner.phase)
        if holder["contactTrace"] and holder["contactTrace"][-1]["name"] == final_phase:
            holder["contactTrace"][-1] = dict(holder["contactTrace"][-1])
        else:
            holder["contactTrace"].append({
                "name": final_phase,
                "entryZ": float(data.xpos[body_id][2]),
                "minZ": float(data.xpos[body_id][2]),
                "maxZ": float(data.xpos[body_id][2]),
                "maxObjectGripperContacts": 0,
                "contactPairs": [],
            })
    target = action["placementPositionTableLocalMeters"]
    expected_position = [float(target[0]), float(target[1]), float(spec["table"]["mujocoTopSurfaceWorldZMeters"]) + float(target[2])]
    horizontal_error = math.hypot(body_position[0] - expected_position[0], body_position[1] - expected_position[1])
    vertical_error = abs(body_position[2] - expected_position[2])
    dimensions = source_entity_dimensions(spec, action["sourceSemanticId"])
    table_top = float(spec["table"]["mujocoTopSurfaceWorldZMeters"])
    bottom_clearance = body_position[2] - dimensions[2] / 2.0 - table_top
    _assert(dispatched.success, "M9 MuJoCo dispatch failed for {}: {}".format(
        action["sourceSemanticId"], json.dumps({
            "dispatch": dispatched.to_public_dict(),
            "finalPosition": body_position,
            "expectedPosition": expected_position,
            "horizontalErrorMeters": horizontal_error,
            "verticalErrorMeters": vertical_error,
            "bottomClearanceMeters": bottom_clearance,
            "finalSpeedMetersPerSecond": speed,
            "trajectoryEvidence": (world_evidence or {}).get("trajectoryEvidence", []),
            "graspContactTrace": holder.get("contactTrace", []),
            "gripperTableClearance": gripper_table_clearance,
            "phoneGraspEvidence": phone_grasp_evidence,
            "sideGraspIkErrors": getattr(backend._last_planner, "m20SideGraspIkErrors", None),
            "plannerDiagnostics": {
                "phase": str(backend._last_planner.phase),
                "phaseStep": int(backend._last_planner.n),
                "flangePreTarget": getattr(backend._last_planner, "flange_pre", np.asarray([])).tolist(),
                "flangeGraspTarget": getattr(backend._last_planner, "flange_pos", np.asarray([])).tolist(),
                "armPreTarget": getattr(backend._last_planner, "arm_pre", np.asarray([])).tolist(),
                "armGraspTarget": getattr(backend._last_planner, "arm_grasp", np.asarray([])).tolist(),
                "armQpos": [float(value) for value in data.qpos[:7]],
                "armCtrl": [float(value) for value in data.ctrl[:7]],
                "gripperWorldPosition": [float(value) for value in data.xpos[backend._last_planner.gripper_bid]],
                "contacts": [
                    {
                        "body1": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[data.contact[index].geom1])) or "",
                        "geom1": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(data.contact[index].geom1)) or "",
                        "body2": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[data.contact[index].geom2])) or "",
                        "geom2": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(data.contact[index].geom2)) or "",
                    }
                    for index in range(int(data.ncon))
                ],
            } if backend._last_planner is not None else None,
        }, sort_keys=True)
    ))
    _assert(result is not None and result.execution_provenance.endswith(":place_ok"), "existing planner did not report place_ok")
    _assert(horizontal_error <= 0.08, "placed object missed the canonical destination center")
    _assert(vertical_error <= 0.035, "placed object did not reach the expected destination height")
    _assert(bottom_clearance >= -0.004, "placed object penetrated the tabletop")
    _assert(
        gripper_table_clearance["minimumMeters"] is not None
        and gripper_table_clearance["minimumMeters"] >= -0.001,
        "M20 gripper penetrated the tabletop: {}".format(gripper_table_clearance),
    )
    _assert(speed <= 0.05, "placed object did not settle after release")
    return {
        "status": "PASS",
        "action": action,
        "sourceConfirmedBatch": source_payload,
        "m9Dispatch": dispatched.to_public_dict(),
        "finalObjectPoseWorldMeters": body_position,
        "expectedCenterWorldMeters": expected_position,
        "horizontalErrorMeters": horizontal_error,
        "verticalErrorMeters": vertical_error,
        "tableBottomClearanceMeters": bottom_clearance,
        "finalLinearSpeedMetersPerSecond": speed,
        "settlingSteps": 600,
        "placementStable": True,
        "plannerProvenance": result.execution_provenance,
        "trajectoryEvidence": (world_evidence or {}).get("trajectoryEvidence", []),
        "graspContactTrace": holder.get("contactTrace", []),
        "gripperTableClearance": gripper_table_clearance,
        "phoneGraspEvidence": phone_grasp_evidence,
        "sideGraspIkErrors": getattr(backend._last_planner, "m20SideGraspIkErrors", None),
    }


def source_entity_dimensions(spec, semantic_id):
    item = _entities(spec)[semantic_id]
    return tuple(float(item["dimensionsMeters"][axis]) for axis in ("x", "y", "z"))


def run_all_scenarios(spec_path: Path, output_dir: Path) -> dict[str, Any]:
    spec, spec_sha256 = load_spec(spec_path)
    validate_spec(spec)
    attempt_dir = _new_attempt_dir(output_dir)
    selection = selection_regression(spec)
    context = context_affordance_evidence(spec)
    _assert(context["status"] == "PASS", "M20 Context/affordance regression failed")
    _write_json(attempt_dir / "candidate-order-and-paging.json", {
        "status": "PASS",
        "candidateOrder": list(EXPECTED_ORDER),
        "pageSize": 3,
        "pages": [list(EXPECTED_ORDER[:3]), list(EXPECTED_ORDER[3:])],
        "slotFrequencyHz": [7.2, 9.0, 12.0],
    })
    _write_json(attempt_dir / "selection-regression.json", selection)
    _write_json(attempt_dir / "context-affordance-traces.json", context)

    scenarios = (
        ("medicine-to-user-zone", "assist_medicine_box", "assist_user_zone", (0, 2)),
        ("phone-to-user-zone", "assist_phone", "assist_user_zone", (2, 2)),
        ("phone-to-charger", "assist_phone", "assist_wireless_charger", (2, 1)),
    )
    scenario_reports = []
    for scenario_id, source_id, destination_id, decisions in scenarios:
        queue, backend, session, events, plan, trigger_info = _select_pair(
            spec, source_id, destination_id, decisions,
            "m20-" + scenario_id,
        )
        action = resolve_source_destination(plan.ordered_selections, spec)
        action["scenarioId"] = scenario_id
        action["syntheticSlotSequence"] = list(decisions)
        action["syntheticFrequenciesHz"] = [
            (7.2, 9.0, 12.0)[index] for index in decisions
        ]
        execution = _execute_action(plan, action, spec, attempt_dir)
        trace = {
            "scenarioId": scenario_id,
            "selection": {
                "orderedTargetIds": [item.target_id for item in plan.ordered_selections],
                "orderedLogicalBlockIds": [item.candidate.logical_block_id for item in plan.ordered_selections],
                "queueFrequencyHz": [item.nominal_frequency_hz for item in plan.ordered_selections],
                "triggerRequests": [{
                    "pageId": trigger_info[key]["request"]["pageId"],
                    "pageEpoch": trigger_info[key]["request"]["pageEpoch"],
                    "slots": trigger_info[key]["request"]["slots"],
                    "decodedClassIndex": trigger_info[key]["decoder"].get("classIndex"),
                } for key in ("sourceTrigger", "destinationTrigger")],
                "submittedBatchCount": len(plan.batches),
                "submitState": queue.state,
                "syntheticBackendCalls": backend.calls,
                "runtimeEvents": events,
            },
            "actionResolution": action,
            "execution": execution,
        }
        _write_json(attempt_dir / (scenario_id + ".json"), trace)
        scenario_reports.append({
            "scenarioId": scenario_id,
            "sourceTargetId": source_id,
            "destinationTargetId": destination_id,
            "status": execution["status"],
            "executionEvidence": "attempt-{:02d}/{}.json".format(
                int(attempt_dir.name.split("-")[1]), scenario_id
            ),
            "finalObjectPoseWorldMeters": execution["finalObjectPoseWorldMeters"],
            "finalLinearSpeedMetersPerSecond": execution["finalLinearSpeedMetersPerSecond"],
        })
    acceptance = {
        "schemaVersion": 1,
        "task": "M20 Task 2 - synthetic paged selection, Context, and MuJoCo full chain",
        "status": "PASS" if all(item["status"] == "PASS" for item in scenario_reports) else "FAIL",
        "sourceHead": "ea497b26587a877605874d8109d809ae17c50d61",
        "canonicalSpecSha256": spec_sha256,
        "syntheticEegOnly": True,
        "nd8Opened": False,
        "questBuildRun": False,
        "candidateOrderingAndSlotMapping": "PASS",
        "previousNextUndoSubmitAndStaleFailClosed": selection["status"],
        "contextAffordanceAndStrongEegOverride": context["status"],
        "pickPlaceScenarios": scenario_reports,
        "focusedRegressionCommands": [
            ".venv\\Scripts\\python.exe -m pytest integration\\test_m20_assistive_desk_e2e.py",
            ".venv\\Scripts\\python.exe -m pytest integration\\test_m16_paged_queue.py integration\\test_m16_paged_queue_orchestration.py integration\\test_m19_paged_live_eeg_demo.py integration\\test_m9_batch_dispatch.py integration\\test_m9_mujoco_execution.py",
        ],
        "evidenceDirectory": str(attempt_dir.relative_to(ROOT)).replace("\\", "/"),
    }
    _write_json(output_dir / "acceptance.json", acceptance)
    return acceptance


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-scenarios", action="store_true", required=True)
    parser.add_argument("--synthetic-eeg", action="store_true", required=True)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    try:
        report = run_all_scenarios(args.spec, output_dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        print("M20_TASK2_STATUS={}".format(report["status"]))
        return 0 if report["status"] == "PASS" else 1
    except Exception as error:
        failure = {
            "status": "FAIL",
            "failureType": type(error).__name__,
            "failure": " ".join(str(error).split()),
        }
        print(json.dumps(failure, indent=2, sort_keys=True), file=sys.stderr)
        print("M20_TASK2_STATUS=FAIL", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
