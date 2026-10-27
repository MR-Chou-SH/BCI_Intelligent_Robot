"""Planner-side task plans and append-only collection sidecars (no policy).

Use call/start_segment/end_segment while YOUR planner supplies actions. These
annotations never establish physical task success or LeRobot export approval.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import gzip
from pathlib import Path

from .task_annotation import TaskAnnotationTracker, catalog_identity, DEVICE_TASK_EVENTS
from .task_catalog import expand, load_catalog

# Named acquisition cases from the user's diagram, not new V1.1 task IDs.
# Preconditions are requirements to verify/prepare, never direct qpos writes.
CASES = {
    "fruit_to_tray": (["L01", "L06"], {"fruit": "on_left_counter", "left_gripper": "empty"}),
    "wash_fruit_to_tray": (["C04"], {"fruit": "on_left_counter", "faucet": "off"}),
    "load_open_microwave": (["R02", "R03", "R04"], {"door": "open", "microwave": "empty", "meal": "on_right_counter"}),
    "unload_open_microwave": (["R06", "R07", "R04"], {"door": "open", "meal": "in_microwave"}),
    "open_closed_microwave": (["R01"], {"door": "closed", "heating": "inactive"}),
    "heat_then_open": (["R05", "加热完成", "R01"], {"door": "closed", "meal": "in_microwave", "heating": "inactive"}),
    "meal_to_tray": (["R02", "R07"], {"meal": "on_right_counter", "right_gripper": "empty"}),
    "deliver_tray": (["B01"], {"tray": "supported_at_kitchen", "both_grippers": "empty"}),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def build_plan(task):
    identity = catalog_identity()
    if task in CASES:
        chain, preconditions = CASES[task]
        nodes, tail, counts = [], [], {}
        # Cases are sequential. Frozen composite plans retain parallel joins.
        for part in chain:
            local = expand(part)
            mapping = {}
            for node in local:
                item = node["task"]
                counts[item] = counts.get(item, 0) + 1
                mapping[node["id"]] = f"{item}:{counts[item]}"
            depended = {d for node in local for d in node["requires"]}
            for node in local:
                row = copy.deepcopy(node)
                row["id"] = mapping[node["id"]]
                row["requires"] = [mapping[d] for d in node["requires"]] if node["requires"] else list(tail)
                nodes.append(row)
            tail = [mapping[node["id"]] for node in local if node["id"] not in depended]
    else:
        catalogue = load_catalog()
        if task not in {x["id"] for x in catalogue["atomic"] + catalogue["composite"]}:
            raise ValueError("Unknown atomic/composite task or acquisition case")
        nodes, preconditions = expand(task), {"task_specific_initial_state": "REQUIRES_VERIFICATION"}
    plan = {"schema": "kitchen_collection_plan_v1", "task": task,
            "catalog": identity, "nodes": nodes, "required_initial_state": preconditions,
            "initial_state_verified": False, "planner_status": "NOT_IMPLEMENTED_BY_THIS_PLAN",
            "task_success": "NOT_VERIFIED"}
    plan["plan_sha256"] = hashlib.sha256(canonical(plan)).hexdigest()
    return plan


class CollectionTracker(TaskAnnotationTracker):
    """Same dependency/arm/tick checks with an independent acquisition plan."""

    def __init__(self, plan):
        self.task_id = plan["task"]
        self.input_source = "programmatic_test"
        self.plan = copy.deepcopy(plan["nodes"])
        self.nodes = {node["id"]: node for node in self.plan}
        self.completed, self.open, self.unmatched_device_events = {}, {}, []


def audit_record(path, episode_id):
    """Read sealed local physics chains without modifying their source."""
    previous, first, last, count, metadata, end = "0" * 64, None, None, 0, None, None
    file_hash = hashlib.sha256()
    opener=gzip.open if Path(path).suffix==".gz" else open
    with opener(path,"rb") as stream:
        for line in stream:
            file_hash.update(line)
            if not line.endswith(b"\n"):
                raise ValueError("Truncated physics record")
            row = json.loads(line)
            claimed = row.pop("sha256")
            if end is not None or row["previous_sha256"] != previous or hashlib.sha256(
                    json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() != claimed:
                raise ValueError("Physics chain mismatch or row after end")
            previous, data = claimed, row["record"]
            if data["type"] == "metadata":
                if metadata is not None or count:
                    raise ValueError("Duplicate or late physics metadata")
                metadata = data
                if data["manifest"]["episode"]["episode_id"] != episode_id:
                    raise ValueError("Physics record belongs to another episode")
            elif data["type"] == "physics":
                if metadata is None or (last is not None and data["sim_tick"] != last + 1):
                    raise ValueError("Physics metadata missing or tick gap")
                if data.get("controller_stopped"):
                    raise ValueError("Controller stopped during recorded dynamics")
                first = data["sim_tick"] if first is None else first
                last, count = data["sim_tick"], count + 1
            elif data["type"] == "end":
                end = data
            else:
                raise ValueError("Unknown physics record type")
    if (metadata is None or end is None or not count or end["sampling_failed"] or
            end["physics_samples"] != count or end["last_recorded_tick"] != last):
        raise ValueError("Missing or inconsistent sealed physics evidence")
    with Path(path).open("rb") as stream: disk_sha=hashlib.file_digest(stream,"sha256").hexdigest()
    return {"path": str(path), "sha256": disk_sha,"uncompressed_sha256":file_hash.hexdigest(), "samples": count,
            "start_tick": first - 1, "end_tick": last, "final_chain_sha256": previous}


class TaskCollection:
    """Wrap a single-writer PlannerClient for ONE already prepared episode.

    Keep all planner mutations on this wrapper. End segments before finish;
    finish holds the robot and audits every encountered execution evidence file.
    Initial-state preparation precedes this object and must be logged separately.
    """

    def __init__(self, client, output, task, *, diagnostics=False):
        self.client = client
        self.diagnostics=diagnostics
        self.plan = build_plan(task)
        self.tracker = CollectionTracker(self.plan)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output/"journal.jsonl").touch()
        self.episode_id = None
        self.paths = set()
        self.device_events = set()
        self.closed = False
        self.state = self.call("observe")
        self.episode_id = self.state["episode"]["episode_id"]
        self.binding = {key: self.state[key] for key in (
            "runtime_session", "epoch", "scene_hash", "control_hash", "episode_id", "initialization_hash")}
        self.state_recording = self.call("start_state_recording", mutating=True)
        try:
            self._save("initial.json", {"plan": self.plan,"binding":self.binding,
                                       "state_recording": self.state_recording})
        except Exception:
            self.call("stop_state_recording", mutating=True)
            raise

    def _save(self, name, value):
        with (self.output / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)

    def _journal(self, value):
        if not self.diagnostics and value["type"]=="rpc":
            if value["reply"].get("ok"):return
            value={"type":"rpc_error","method":value["method"],"error":value["reply"].get("error")}
        with (self.output / "journal.jsonl").open("ab") as stream:
            stream.write(canonical(value) + b"\n")

    def call(self, method, args=None, *, mutating=False):
        if self.closed or method == "reset":
            raise ValueError("Collection is closed or reset requires a separate collection")
        try:
            reply = self.client.call(method, args, mutating=mutating)
        except Exception as error:
            self._journal({"type": "rpc_error", "method": method, "args": args, "error": repr(error)})
            raise
        self._journal({"type": "rpc", "method": method, "args": args, "mutating": mutating, "reply": reply})
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error"))
        token = reply.get("token", {})
        if self.episode_id is not None and token.get("episode_id") != self.episode_id:
            raise RuntimeError("Episode changed during collection")
        result = reply["result"]
        if isinstance(result, dict):
            execution = result if "evidence_path" in result else result.get("execution")
            if execution and execution.get("evidence_path"):
                self.paths.add(execution["evidence_path"])
        return result

    def _segment(self, node_id, event):
        state = self.call("observe")
        details = {"node_id": node_id, "annotation_source": "programmatic_test"}
        candidate = copy.deepcopy(self.tracker)
        candidate.apply_event(event, state["sim_tick"], details)
        self._journal({"type": "annotation", "event": event, "sim_tick": state["sim_tick"], "details": details})
        self.tracker = candidate

    def start_segment(self, node_id):
        self._segment(node_id, "TASK_SUBTASK_STARTED")

    def end_segment(self, node_id):
        self._segment(node_id, "TASK_SUBTASK_ENDED")

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if not self.closed:
            try:
                self.finish("planner_exception: " + repr(error) if error else "planner_finished")
            except Exception as finish_error:
                self._save("collection_failure.json", {"error": repr(finish_error),
                    "planner_error": repr(error), "task_success": "NOT_VERIFIED"})
                if error is None:
                    raise
        return False

    def device_event(self, event):
        # Only accept events actually returned by this episode's simulator.
        feedback = self.call("get_task_feedback")
        if (event not in feedback["events"] or event.get("event") not in DEVICE_TASK_EVENTS or
                not self.state["sim_tick"] <= event.get("sim_tick", -1) <= feedback["evidence_tick"]):
            raise ValueError("Device event is not in current simulator feedback")
        candidate = copy.deepcopy(self.tracker)
        kind, tick = event["event"], event["sim_tick"]
        event_key = (kind, tick, event.get("object"))
        if event_key in self.device_events:
            raise ValueError("Device event already recorded")
        for node in candidate.plan:
            if node["task"] == DEVICE_TASK_EVENTS[kind] and node["id"] not in candidate.completed:
                if any(d not in candidate.completed or candidate.completed[d]["end_sim_tick"] > tick
                       for d in node["requires"]):
                    raise ValueError("Device event precedes its task dependencies")
                break
        candidate.apply_event(kind, tick, event)
        self._journal({"type": "annotation", "event": kind, "sim_tick": tick, "details": event})
        self.tracker = candidate
        self.device_events.add(event_key)

    def finish(self, reason="planner_finished"):
        self.call("hold", mutating=True)
        trajectory = self.call("stop_state_recording", mutating=True)
        feedback = self.call("get_task_feedback")
        records, errors = [], []
        from .data.replay import inspect_trajectory
        try:state_audit=inspect_trajectory(trajectory["path"])
        except (OSError,ValueError,KeyError) as error:
            state_audit=None;errors.append({"error":str(error)})
        for path in sorted(self.paths):
            try:
                records.append(audit_record(path, self.episode_id))
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append({"path": path, "error": str(error)})
        if not records and state_audit and state_audit["steps"]:
            records.append({"path":trajectory["path"],"kind":"state_actions",
                            "samples":state_audit["steps"],"start_tick":state_audit["start_tick"],
                            "end_tick":state_audit["end_tick"]})
        summary = self.tracker.summary()
        for interval in summary["intervals"]:
            cursor = interval["start_sim_tick"]
            for record in sorted(records, key=lambda r: r["start_tick"]):
                if record["start_tick"] <= cursor:
                    cursor = max(cursor, record["end_tick"])
            if cursor < interval["end_sim_tick"]:
                errors.append({"node_id": interval["node_id"], "error": "Annotation not covered by physics records"})
        result = {"schema": "kitchen_collection_sidecar_v1", "binding": self.binding,
                  "plan": self.plan, "annotation": summary, "records": records,
                  "record_errors": errors, "reason": reason, "feedback": feedback,
                  "record_integrity": "PASS" if records and not errors else "INCOMPLETE",
                  "task_success": "NOT_VERIFIED", "physical_quality": "NOT_VERIFIED",
                  "training_export_approved": False, "rgb_available": False}
        result["state_trajectory"]={**trajectory,"audit":state_audit}
        result["journal_sha256"] = hashlib.sha256((self.output / "journal.jsonl").read_bytes()).hexdigest()
        result["initial_sha256"] = hashlib.sha256((self.output / "initial.json").read_bytes()).hexdigest()
        self._save("collection.json", result)
        self.closed = True
        return result


def main():
    parser = argparse.ArgumentParser(description="Inspect acquisition plans; does not execute robot actions")
    parser.add_argument("task", help="V1.1 atomic/composite ID or case name; use list to enumerate")
    args = parser.parse_args()
    if args.task == "list":
        catalogue = load_catalog()
        result = {"atomic": [x["id"] for x in catalogue["atomic"]],
                  "composite": [x["id"] for x in catalogue["composite"]], "diagram_cases": list(CASES)}
    else:
        result = build_plan(args.task)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
