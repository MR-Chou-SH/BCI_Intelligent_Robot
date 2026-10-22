"""Explicit operator task annotations; never physical manipulation evidence."""
from __future__ import annotations

import hashlib

from integration.kitchen.task_catalog import PATH as TASK_CATALOG_PATH, expand, load_catalog


TASK_ANNOTATION_SCHEMA_VERSION = 1
FROZEN_TASK_CATALOG_SHA256 = "5ae53a4d6a9ecf1ece2c398c7608c769cfed243cc5318863f140b51020101e88"
DEVICE_TASK_EVENTS = {
    "wash_complete": "清洗完成",
    "heat_complete": "加热完成",
}


def catalog_identity() -> dict:
    raw = TASK_CATALOG_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != FROZEN_TASK_CATALOG_SHA256:
        raise ValueError("V1.1 task catalogue hash differs from the frozen task definition")
    catalog = load_catalog()
    return {"version": catalog["version"], "sha256": digest}


def task_plan(task_id: str | None) -> list[dict]:
    if task_id is None:
        return []
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a nonempty V1.1 composite-task ID")
    catalog_identity()
    return expand(task_id)


def valid_task_ids() -> tuple[str, ...]:
    catalog_identity()
    return tuple(item["id"] for item in load_catalog()["composite"])


def require_complete_task_annotation(manifest: dict) -> dict:
    """Export gate for V1.1 task labels, independent of physical-QC approval."""
    identity = catalog_identity()
    summary = manifest.get("task_annotation")
    task_id = manifest.get("task_id")
    if task_id not in valid_task_ids():
        raise ValueError("LeRobot export requires an explicit frozen V1.1 task_id")
    if manifest.get("task_binding_schema_version") != TASK_ANNOTATION_SCHEMA_VERSION:
        raise ValueError("LeRobot export requires a bound V1.1 task-annotation schema")
    if (manifest.get("task_catalog_version") != identity["version"] or
            manifest.get("task_catalog_sha256") != identity["sha256"]):
        raise ValueError("LeRobot task binding does not match the frozen V1.1 catalogue")
    if (manifest.get("task_annotation_status") != "COMPLETE_ANNOTATION" or
            not isinstance(summary, dict) or summary.get("status") != "COMPLETE_ANNOTATION"):
        raise ValueError("LeRobot export requires closed annotation intervals for the full V1.1 task graph")
    nodes = summary.get("nodes")
    intervals = summary.get("intervals")
    expected_nodes = [
        {"node_id": node["id"], "task": node["task"], "kind": node["kind"],
         "arm": node["arm"], "object_role": node["object_role"],
         "requires": node["requires"]}
        for node in task_plan(task_id)
    ]
    expected_ids = {node["node_id"] for node in expected_nodes}
    expected_order = [node["node_id"] for node in expected_nodes]
    completed = summary.get("completed_node_ids")
    empty_fields = ("missing_node_ids", "open_node_ids", "open_intervals",
                    "unmatched_device_events")
    if (type(summary.get("schema_version")) is not int or
            summary.get("schema_version") != TASK_ANNOTATION_SCHEMA_VERSION or
            summary.get("interval_semantics") != "half_open_sim_tick" or
            not isinstance(nodes, list) or nodes != expected_nodes or
            not isinstance(intervals, list) or
            any(not isinstance(row, dict) or not isinstance(row.get("node_id"), str)
                for row in intervals) or
            {row["node_id"] for row in intervals} != expected_ids or
            len(intervals) != len(expected_nodes) or
            not isinstance(completed, list) or completed != expected_order or
            any(summary.get(field) != [] for field in empty_fields)):
        raise ValueError("LeRobot task annotation does not cover each V1.1 occurrence exactly once")
    by_id = {node["node_id"]: node for node in expected_nodes}
    interval_by_id = {row["node_id"]: row for row in intervals}
    for row in intervals:
        node=by_id[row["node_id"]]
        if row.get("task")!=node.get("task") or row.get("arm")!=node.get("arm") or row.get("object_role")!=node.get("object_role"):
            raise ValueError("LeRobot task interval metadata differs from the frozen task occurrence")
        if node.get("kind")=="atomic" and row.get("annotation_source")!="operator_annotation":
            raise ValueError("LeRobot atomic-task intervals require operator annotation provenance")
        if node.get("kind")=="event" and row.get("annotation_source")!="simulated_device_event":
            raise ValueError("LeRobot device-event intervals require simulated-device provenance")
        if (type(row.get("start_sim_tick")) is not int or type(row.get("end_sim_tick")) is not int or
                row["start_sim_tick"]>row["end_sim_tick"] or
                (node.get("kind")=="atomic" and row["start_sim_tick"]>=row["end_sim_tick"]) or
                (node.get("kind")=="event" and row["start_sim_tick"]!=row["end_sim_tick"])):
            raise ValueError("LeRobot task interval has invalid simulation tick bounds")
    for node in expected_nodes:
        row = interval_by_id[node["node_id"]]
        for dependency_id in node["requires"]:
            if interval_by_id[dependency_id]["end_sim_tick"] > row["start_sim_tick"]:
                raise ValueError("LeRobot task occurrence starts before its dependency annotation ends")
    atomic_rows = [row for row in intervals if by_id[row["node_id"]]["kind"] == "atomic"]
    for index, row in enumerate(atomic_rows):
        node = by_id[row["node_id"]]
        for other in atomic_rows[index + 1:]:
            other_node = by_id[other["node_id"]]
            conflicts = (node["arm"] == "both" or other_node["arm"] == "both" or
                         node["arm"] == other_node["arm"])
            overlaps = (row["start_sim_tick"] < other["end_sim_tick"] and
                        other["start_sim_tick"] < row["end_sim_tick"])
            if conflicts and overlaps:
                raise ValueError("LeRobot task annotations contain conflicting same-arm overlap")
    return summary


class TaskAnnotationTracker:
    """Validate task graph annotations using half-open simulation-tick intervals."""

    def __init__(self, task_id: str | None, input_source: str):
        self.task_id = task_id
        self.input_source = input_source
        self.plan = task_plan(task_id)
        self.nodes = {node["id"]: node for node in self.plan}
        self.completed: dict[str, dict] = {}
        self.open: dict[str, dict] = {}
        self.unmatched_device_events: list[dict] = []

    @staticmethod
    def _tick(sim_tick):
        if type(sim_tick) is not int or sim_tick < 0:
            raise ValueError("Task annotation requires a nonnegative integer MuJoCo sim_tick")
        return sim_tick

    def _node(self, node_id):
        if not self.task_id:
            raise ValueError("Task annotation requires an Episode task_id")
        if not isinstance(node_id, str) or node_id not in self.nodes:
            raise ValueError("Task annotation node_id is not in the frozen V1.1 task graph")
        return self.nodes[node_id]

    def _dependencies_ready(self, node):
        return all(required in self.completed for required in node["requires"])

    def validate_start(self, node_id, sim_tick):
        tick = self._tick(sim_tick)
        node = self._node(node_id)
        if node["kind"] != "atomic":
            raise ValueError("Only physical atomic nodes can use operator start/end intervals")
        if node_id in self.completed or node_id in self.open:
            raise ValueError("Task occurrence is already completed or active")
        if not self._dependencies_ready(node):
            raise ValueError("Task occurrence dependencies are not yet annotated complete")
        arm = node["arm"]
        for active_id in self.open:
            active_arm = self.nodes[active_id]["arm"]
            if arm == "both" or active_arm == "both" or arm == active_arm:
                raise ValueError("Task intervals may overlap only across independent arms")
        return tick, node

    def record_start(self, node_id, sim_tick, annotation_source):
        tick, node = self.validate_start(node_id, sim_tick)
        self.open[node_id] = {
            "node_id": node_id,
            "task": node["task"],
            "arm": node["arm"],
            "object_role": node["object_role"],
            "start_sim_tick": tick,
            "annotation_source": annotation_source,
        }

    def validate_end(self, node_id, sim_tick):
        tick = self._tick(sim_tick)
        if node_id not in self.open:
            raise ValueError("Task occurrence has no matching start annotation")
        if tick <= self.open[node_id]["start_sim_tick"]:
            raise ValueError("Task interval must be nonempty and use [start_sim_tick,end_sim_tick)")
        return tick

    def record_end(self, node_id, sim_tick):
        tick = self.validate_end(node_id, sim_tick)
        interval = dict(self.open.pop(node_id))
        interval["end_sim_tick"] = tick
        self.completed[node_id] = interval

    def observe_device_event(self, kind, sim_tick, details=None):
        """Bind real simulated-device state events without fabricating robot actions."""
        if not self.task_id or kind not in DEVICE_TASK_EVENTS:
            return
        tick = self._tick(sim_tick)
        if not isinstance(details, dict) or details.get("simulated_device_event") is not True:
            self.unmatched_device_events.append({"event": kind, "sim_tick": tick,
                "reason": "missing_simulated_device_provenance"})
            return
        task_name = DEVICE_TASK_EVENTS[kind]
        candidates = [node for node in self.plan
                      if node["kind"] == "event" and node["task"] == task_name
                      and node["id"] not in self.completed and self._dependencies_ready(node)]
        if not candidates:
            self.unmatched_device_events.append({"event": kind, "sim_tick": tick})
            return
        node = candidates[0]
        self.completed[node["id"]] = {
            "node_id": node["id"], "task": node["task"], "arm": None,
            "object_role": node["object_role"], "start_sim_tick": tick,
            "end_sim_tick": tick, "annotation_source": "simulated_device_event",
        }

    def apply_event(self, kind, sim_tick, details):
        if not isinstance(details, dict):
            raise ValueError("Episode task-event details must be an object")
        if kind == "TASK_SUBTASK_STARTED":
            source = details.get("annotation_source")
            expected = ("operator_annotation" if self.input_source in
                        ("pc_human_teleop", "quest_human_teleop") else "programmatic_test")
            if source != expected:
                raise ValueError("Task interval annotation source does not match Episode input provenance")
            self.record_start(details.get("node_id"), sim_tick, source)
        elif kind == "TASK_SUBTASK_ENDED":
            source = details.get("annotation_source")
            expected = ("operator_annotation" if self.input_source in
                        ("pc_human_teleop", "quest_human_teleop") else "programmatic_test")
            if source != expected:
                raise ValueError("Task interval annotation source does not match Episode input provenance")
            self.validate_end(details.get("node_id"), sim_tick)
            self.record_end(details.get("node_id"), sim_tick)
        else:
            self.observe_device_event(kind, sim_tick, details)

    def available_node_ids(self):
        return [node["id"] for node in self.plan
                if node["kind"] == "atomic" and node["id"] not in self.completed
                and (node["id"] in self.open or self._dependencies_ready(node))]

    def summary(self):
        if not self.task_id:
            return {"schema_version": TASK_ANNOTATION_SCHEMA_VERSION,
                    "status": "NOT_PROVIDED", "interval_semantics": "half_open_sim_tick",
                    "nodes": [], "completed_node_ids": [], "open_node_ids": [],
                    "missing_node_ids": [], "intervals": [], "open_intervals": [],
                    "unmatched_device_events": []}
        plan_rows = [{"node_id": node["id"], "task": node["task"],
                      "kind": node["kind"], "arm": node["arm"],
                      "object_role": node["object_role"], "requires": node["requires"]}
                     for node in self.plan]
        missing = [node["id"] for node in self.plan
                   if node["id"] not in self.completed and node["id"] not in self.open]
        status = ("COMPLETE_ANNOTATION" if not missing and not self.open
                  and not self.unmatched_device_events else "INCOMPLETE_ANNOTATION")
        return {"schema_version": TASK_ANNOTATION_SCHEMA_VERSION,
                "status": status, "interval_semantics": "half_open_sim_tick",
                "nodes": plan_rows,
                "completed_node_ids": [node["id"] for node in self.plan if node["id"] in self.completed],
                "open_node_ids": [node["id"] for node in self.plan if node["id"] in self.open],
                "missing_node_ids": missing,
                "intervals": [self.completed[node["id"]] for node in self.plan if node["id"] in self.completed],
                "open_intervals": [self.open[node["id"]] for node in self.plan if node["id"] in self.open],
                "unmatched_device_events": list(self.unmatched_device_events),
                "evidence_boundary": "operator or simulated-device annotation only; not contact, grasp, task success, or physical QC"}


def replay_task_annotations(task_id: str | None, input_source: str, rows) -> dict:
    """Rebuild annotation state from the append-only event ledger."""
    tracker = TaskAnnotationTracker(task_id, input_source)
    if not task_id:
        if any(row.get("event") in ("TASK_SUBTASK_STARTED", "TASK_SUBTASK_ENDED") for row in rows):
            raise ValueError("Unbound Episode contains task interval annotations")
        return tracker.summary()
    for row in rows:
        tracker.apply_event(row.get("event"), row.get("sim_tick"), row.get("details", {}))
    return tracker.summary()
