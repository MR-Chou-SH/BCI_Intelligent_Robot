"""Lossless HDF5 episodes with explicit provenance and SWMR recovery."""
from pathlib import Path
import copy
import datetime
import hashlib
import json
import os
import queue
import shutil
import threading
import uuid
import numpy as np
import h5py
from integration.kitchen.contracts import INPUT_SOURCES,action_names,state_names,actual_ctrl_names
from integration.kitchen.task_annotation import (TASK_ANNOTATION_SCHEMA_VERSION,
    DEVICE_TASK_EVENTS,TaskAnnotationTracker,catalog_identity,replay_task_annotations)

DEFAULT_DATA_ROOT=Path(r"D:\VLA_Kitchen_Data")
RESERVE_BYTES=10*1024**3
CAMERAS=("high","left_wrist","right_wrist")
PHYSICS_TRACE_EVENT="MUJOCO_PHYSICS_TRACE_FRAME"
PHYSICS_TRACE_SCHEMA=1
PHYSICS_TRACE_HZ=500
PHYSICS_TRACE_SUBSTEPS=20
PHYSICS_TRACE_MAX_CONTACTS_PER_SUBSTEP=64
PHYSICS_TRACE_MAX_BYTES_PER_FRAME=2*1024*1024
NONEXPORTABLE_EVENT_KINDS=frozenset({
    "CONTROLLER_STOP","TRAY_COMMON_STOP","RECORDING_FAILURE","INPUT_TIMEOUT",
    "INPUT_REJECTED","CONTROL_LOOP_OVERRUN","CLUTCH_INVALIDATED_AFTER_STOP",
})


def _canonical_json_sha256(value):
    """Stable file-internal binding for immutable-at-record-time metadata."""
    try:
        encoded=json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,
            separators=(",",":")).encode("utf-8")
    except (TypeError,ValueError) as error:
        raise ValueError("Episode provenance is not finite canonical JSON") from error
    return hashlib.sha256(encoded).hexdigest()


def _validate_task_annotation_binding(directory,manifest,rows=None):
    """Bind operator task annotations to the V1.1 catalogue and HDF5 provenance."""
    version=manifest.get("task_binding_schema_version")
    if version is None:return None  # Legacy Episode: readable, but not V1.1 task-bound.
    if version!=TASK_ANNOTATION_SCHEMA_VERSION:
        raise ValueError("Unsupported Episode task-binding schema")
    task_id=manifest.get("task_id")
    task_id=task_id if task_id else None
    identity=catalog_identity() if task_id else None
    if task_id and (manifest.get("task_catalog_version")!=identity["version"] or
                    manifest.get("task_catalog_sha256")!=identity["sha256"]):
        raise ValueError("Episode task binding differs from the frozen V1.1 catalogue")
    if not task_id and (manifest.get("task_catalog_version") is not None or
                        manifest.get("task_catalog_sha256") is not None):
        raise ValueError("Unbound Episode must not claim a V1.1 task catalogue")
    try:
        with h5py.File(Path(directory)/"episode.h5","r",swmr=True) as stream:
            hdf5_version=int(stream.attrs.get("task_binding_schema_version",0))
            if hdf5_version!=TASK_ANNOTATION_SCHEMA_VERSION:
                raise ValueError("Episode HDF5 lacks the task-binding provenance schema")
            hdf5_task_id=stream.attrs.get("task_id","")
            hdf5_catalog_sha=stream.attrs.get("task_catalog_sha256","")
            hdf5_summary_sha=stream.attrs.get("task_annotation_summary_sha256","")
            committed=int(stream["committed_frames"][()])
            hdf5_ticks=np.asarray(stream["sim_tick"][:committed],dtype=np.int64)
    except (OSError,KeyError) as error:
        raise ValueError("Episode task annotation HDF5 binding is unavailable") from error
    def decoded(value):
        return value.decode("utf-8",errors="replace") if isinstance(value,bytes) else value
    if decoded(hdf5_task_id)!=(task_id or ""):
        raise ValueError("Episode task_id differs from its HDF5 provenance binding")
    if decoded(hdf5_catalog_sha)!=(identity["sha256"] if identity else ""):
        raise ValueError("Episode task catalogue hash differs from its HDF5 provenance binding")
    if rows is None:
        rows=[]
        with (Path(directory)/"events.jsonl").open("r",encoding="utf-8") as source:
            for line in source:
                rows.append(json.loads(line))
    summary=replay_task_annotations(task_id,manifest.get("input_source"),rows)
    declared=manifest.get("task_annotation")
    if declared!=summary or manifest.get("task_annotation_status")!=summary["status"]:
        raise ValueError("Episode task annotation summary differs from its event ledger")
    annotated_rows=summary.get("intervals",[])+summary.get("open_intervals",[])
    if annotated_rows:
        if not committed or hdf5_ticks.shape!=(committed,):
            raise ValueError("Task annotations require committed MuJoCo observation frames")
        first_tick=int(hdf5_ticks[0]);last_tick=int(hdf5_ticks[-1])+PHYSICS_TRACE_SUBSTEPS
        for interval in summary["intervals"]:
            if interval["start_sim_tick"]<first_tick or interval["end_sim_tick"]>last_tick:
                raise ValueError("Task annotation interval lies outside the recorded MuJoCo Episode ticks")
        for interval in summary["open_intervals"]:
            if interval["start_sim_tick"]<first_tick or interval["start_sim_tick"]>last_tick:
                raise ValueError("Open task annotation starts outside the recorded MuJoCo Episode ticks")
    digest=_canonical_json_sha256(summary)
    if decoded(hdf5_summary_sha)!=digest:
        raise ValueError("Episode task annotation summary differs from its HDF5 provenance binding")
    return summary


def _validate_episode_provenance_binding(directory,manifest):
    metadata=manifest.get("metadata")
    contract=metadata.get("physics_trace_contract") if isinstance(metadata,dict) else None
    contract_sha256=_canonical_json_sha256(contract) if isinstance(contract,dict) else ""
    try:
        with h5py.File(Path(directory)/"episode.h5","r",swmr=True) as stream:
            episode_id=stream.attrs.get("episode_id")
            input_source=stream.attrs.get("input_source")
            hdf5_contract_sha256=stream.attrs.get("physics_trace_contract_sha256")
    except (OSError,KeyError) as error:
        raise ValueError("Episode HDF5 provenance binding is unavailable") from error
    if isinstance(episode_id,bytes):episode_id=episode_id.decode("utf-8",errors="replace")
    if isinstance(input_source,bytes):input_source=input_source.decode("utf-8",errors="replace")
    if isinstance(hdf5_contract_sha256,bytes):
        hdf5_contract_sha256=hdf5_contract_sha256.decode("ascii",errors="replace")
    if (episode_id!=manifest.get("episode_id") or input_source!=manifest.get("input_source") or
            hdf5_contract_sha256!=contract_sha256):
        raise ValueError("Episode source or physical-trace contract differs from its HDF5 provenance binding")
    return contract_sha256

def _finite_vector(value,length):
    try:
        vector=np.asarray(value,dtype=float)
    except (TypeError,ValueError):
        return False
    return vector.shape==(length,) and bool(np.isfinite(vector).all())


def _validate_physics_trace_row(row,frame_index,observation_tick,contract,
        expected_state=None,expected_action=None,expected_ctrl=None,require_running_controller=False):
    details=row["details"]
    if (details.get("schema_version")!=PHYSICS_TRACE_SCHEMA or
            details.get("frame_index")!=frame_index or
            details.get("observation_sim_tick")!=observation_tick or
            row.get("sim_tick")!=observation_tick or
            details.get("action_start_sim_tick")!=observation_tick+1 or
            details.get("action_end_sim_tick")!=observation_tick+PHYSICS_TRACE_SUBSTEPS or
            details.get("physics_hz")!=PHYSICS_TRACE_HZ or
            details.get("sample_count")!=PHYSICS_TRACE_SUBSTEPS):
        raise ValueError("Physical trace frame/tick contract is invalid")
    try:observation_time=float(details.get("observation_sim_time_s"))
    except (TypeError,ValueError):raise ValueError("Physical trace observation time is invalid")
    if not np.isfinite(observation_time):raise ValueError("Physical trace observation time is nonfinite")
    if not np.isclose(observation_time,observation_tick/PHYSICS_TRACE_HZ,rtol=0.,atol=1e-8):
        raise ValueError("Physical trace observation time differs from the HDF5 simulation tick")
    if not _finite_vector(details.get("pre_action_state16"),16) or not _finite_vector(details.get("accepted_action16"),16):
        raise ValueError("Physical trace is missing the pre-action state or accepted action")
    if expected_state is not None and not np.allclose(details["pre_action_state16"],expected_state,rtol=0.,atol=1e-12):
        raise ValueError("Physical trace pre-action state does not match its HDF5 observation row")
    if expected_action is not None and not np.allclose(details["accepted_action16"],expected_action,rtol=0.,atol=1e-12):
        raise ValueError("Physical trace accepted action does not match its HDF5 action row")
    samples=details.get("samples")
    if not isinstance(samples,list) or len(samples)!=PHYSICS_TRACE_SUBSTEPS:
        raise ValueError("Physical trace must contain exactly 20 post-step samples")
    object_names=contract.get("object_names")
    device_joint_names=contract.get("device_joint_names")
    wash_timer_names=contract.get("wash_timer_object_names")
    geom_names=contract.get("geom_names_by_id")
    body_names=contract.get("body_names_by_id")
    if (not isinstance(object_names,list) or not object_names or
            not all(isinstance(name,str) and name for name in object_names) or
            len(object_names)!=len(set(object_names))):
        raise ValueError("Physical trace object identity contract is invalid")
    if (not isinstance(device_joint_names,list) or
            not all(isinstance(name,str) and name for name in device_joint_names) or
            len(device_joint_names)!=len(set(device_joint_names))):
        raise ValueError("Physical trace device-joint contract is invalid")
    if (not isinstance(wash_timer_names,list) or
            not all(isinstance(name,str) and name in object_names for name in wash_timer_names) or
            len(wash_timer_names)!=len(set(wash_timer_names))):
        raise ValueError("Physical trace simulated-device timer contract is invalid")
    if (not isinstance(geom_names,list) or not isinstance(body_names,list) or
            not all(isinstance(name,str) for name in geom_names+body_names)):
        raise ValueError("Physical trace contact identity maps are invalid")
    equality_specs=contract.get("equality_constraints")
    if not isinstance(equality_specs,list):
        raise ValueError("Physical trace equality-constraint contract is invalid")
    if any(not isinstance(item,dict) or type(item.get("id")) is not int for item in equality_specs):
        raise ValueError("Physical trace equality-constraint identities are invalid")
    equality_ids={item.get("id") for item in equality_specs if isinstance(item,dict)}
    if len(equality_ids)!=len(equality_specs):raise ValueError("Physical trace equality ids are duplicated")
    sample_times=[]
    for offset,sample in enumerate(samples,1):
        if not isinstance(sample,dict) or sample.get("sim_tick")!=observation_tick+offset:
            raise ValueError("Physical trace substep ticks are missing or noncontiguous")
        try:sample_time=float(sample.get("sim_time_s"))
        except (TypeError,ValueError):raise ValueError("Physical trace substep time is invalid")
        if not np.isfinite(sample_time):raise ValueError("Physical trace substep time is nonfinite")
        if not np.isclose(sample_time,(observation_tick+offset)/PHYSICS_TRACE_HZ,rtol=0.,atol=1e-8):
            raise ValueError("Physical trace substep time differs from its simulation tick")
        sample_times.append(sample_time)
        objects=sample.get("object_pose_velocity")
        if not isinstance(objects,list) or len(objects)!=len(object_names):
            raise ValueError("Physical trace object pose set differs from the frozen scene contract")
        if any(not _finite_vector(state,13) for state in objects):
            raise ValueError("Physical trace contains invalid object pose/velocity")
        tcp=sample.get("tcp_pose")
        if not isinstance(tcp,list) or len(tcp)!=2 or any(not _finite_vector(pose,7) for pose in tcp):
            raise ValueError("Physical trace must contain both finite TCP poses")
        fingers=sample.get("finger_qpos")
        if not isinstance(fingers,list) or len(fingers)!=2 or any(
                not _finite_vector(value,2) for value in fingers):
            raise ValueError("Physical trace must contain four finite individual finger positions")
        devices=sample.get("device_joint_state")
        if not isinstance(devices,list) or len(devices)!=len(device_joint_names) or any(
                not _finite_vector(value,2) for value in devices):
            raise ValueError("Physical trace device-joint state set differs from its contract")
        device_state=sample.get("device_state")
        if (not isinstance(device_state,dict) or type(device_state.get("enabled")) is not bool or
                device_state.get("simulated_device_state") is not True or
                not _finite_vector(device_state.get("wash_timer_s"),len(wash_timer_names)) or
                any(value<0 for value in device_state.get("wash_timer_s",[]))):
            raise ValueError("Physical trace simulated-device state is incomplete")
        cleaned=device_state.get("cleaned_object_names")
        heated=device_state.get("heated_object_names")
        heating=device_state.get("heating_object_name")
        try:heat_seconds=float(device_state.get("heat_timer_s"));button_seconds=float(device_state.get("button_hold_s"))
        except (TypeError,ValueError):raise ValueError("Physical trace simulated-device timers are invalid")
        if (not isinstance(cleaned,list) or any(name not in object_names for name in cleaned) or
                not isinstance(heated,list) or any(name not in object_names for name in heated) or
                (heating is not None and heating not in object_names) or
                not np.isfinite([heat_seconds,button_seconds]).all() or heat_seconds<0 or button_seconds<0 or
                type(device_state.get("button_latched")) is not bool):
            raise ValueError("Physical trace simulated-device state values are invalid")
        active=sample.get("active_equality_ids")
        if not isinstance(active,list) or any(type(value) is not int or value not in equality_ids for value in active):
            raise ValueError("Physical trace has an unknown active equality constraint")
        if active:
            raise ValueError("Active equality is unsupported until runtime assist residual/load QC is implemented")
        allowed=sample.get("allowed_device_contact_geom_ids")
        if not isinstance(allowed,list) or any(
                type(value) is not int or value<0 or value>=len(geom_names) for value in allowed):
            raise ValueError("Physical trace device-contact allowlist is invalid")
        contacts=sample.get("contacts")
        if (not isinstance(contacts,list) or len(contacts)>PHYSICS_TRACE_MAX_CONTACTS_PER_SUBSTEP or
                type(sample.get("contact_count")) is not int or sample["contact_count"]!=len(contacts)):
            raise ValueError("Physical trace contact table is incomplete, truncated, or over capacity")
        for contact in contacts:
            if (not isinstance(contact,list) or len(contact)!=23 or
                    any(type(contact[index]) is not int for index in range(4)) or
                    contact[0]<0 or contact[0]>=len(geom_names) or
                    contact[2]<0 or contact[2]>=len(geom_names) or
                    contact[1]<0 or contact[1]>=len(body_names) or
                    contact[3]<0 or contact[3]>=len(body_names) or
                    not _finite_vector(contact[4:],19)):
                raise ValueError("Physical trace contact row is invalid")
        if type(sample.get("controller_stopped")) is not bool:
            raise ValueError("Physical trace controller state is invalid")
        if require_running_controller and sample["controller_stopped"]:
            raise ValueError("Episode physics trace observed a safety-stopped controller")
        if not _finite_vector(sample.get("actual_ctrl16"),16):
            raise ValueError("Physical trace actual-control sample is invalid")
    expected_dt=1.0/PHYSICS_TRACE_HZ
    if not np.isclose(sample_times[0]-observation_time,expected_dt,rtol=0.,atol=1e-8):
        raise ValueError("Physical trace first sample does not follow the observation by one physics step")
    if not np.allclose(np.diff(sample_times),expected_dt,rtol=0.,atol=1e-8):
        raise ValueError("Physical trace timestamps do not preserve the 500 Hz physics clock")
    if expected_ctrl is not None:
        sampled_ctrl=np.asarray([sample["actual_ctrl16"] for sample in samples],dtype=np.float64)
        if sampled_ctrl.shape!=(PHYSICS_TRACE_SUBSTEPS,16) or not np.allclose(
                sampled_ctrl,expected_ctrl,rtol=0.,atol=1e-12):
            raise ValueError("Physical trace applied controls do not match HDF5 substep controls")



def validate_episode_event_ledger(directory,*,expected_sha256=None,require_hdf5_binding=True,
        require_physics_trace=False):
    """Fail closed unless a complete, normal review-stop event ledger is present.

    The optional physics trace check binds raw MuJoCo substeps to the HDF5
    observation/action rows. It does not certify task success or quality.
    """
    directory=Path(directory)
    path=directory/"events.jsonl"
    try:
        digest=hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda:source.read(1024*1024),b""):digest.update(block)
        digest=digest.hexdigest()
        if not isinstance(expected_sha256,str) or len(expected_sha256)!=64:
            raise ValueError("Episode manifest is missing the closed event-ledger SHA-256")
        if digest!=expected_sha256:
            raise ValueError("Episode event ledger SHA-256 does not match its closed manifest")
        manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
        manifest=None if manifest.get("task_binding_schema_version") is None and not require_physics_trace else manifest
        contract=None;committed=None;hdf5_ticks=None;hdf5_digest=None
        hdf5_episode_id=None;hdf5_input_source=None;hdf5_contract_sha256=None
        if require_hdf5_binding or require_physics_trace:
            with h5py.File(directory/"episode.h5","r",swmr=True) as stream:
                hdf5_digest=stream.attrs.get("event_log_sha256")
                if require_physics_trace:
                    hdf5_episode_id=stream.attrs.get("episode_id")
                    hdf5_input_source=stream.attrs.get("input_source")
                    hdf5_contract_sha256=stream.attrs.get("physics_trace_contract_sha256")
                    required_trace_data={"sim_tick","committed_frames","state","action","actual_ctrl"}
                    if not required_trace_data.issubset(stream.keys()):
                        raise ValueError("Episode HDF5 is missing data required to bind physical trace rows")
                    committed=int(stream["committed_frames"][()])
                    hdf5_ticks=np.asarray(stream["sim_tick"][:committed],dtype=np.int64)
                    hdf5_states=np.asarray(stream["state"][:committed],dtype=np.float64)
                    hdf5_actions=np.asarray(stream["action"][:committed],dtype=np.float64)
                    hdf5_controls=np.asarray(stream["actual_ctrl"][:committed],dtype=np.float64)
                    if (hdf5_ticks.shape!=(committed,) or hdf5_states.shape!=(committed,16) or
                            hdf5_actions.shape!=(committed,16) or
                            hdf5_controls.shape!=(committed,PHYSICS_TRACE_SUBSTEPS,16)):
                        raise ValueError("Episode HDF5 observation/action/control rows have invalid shapes")
        if isinstance(hdf5_digest,bytes):hdf5_digest=hdf5_digest.decode("ascii",errors="replace")
        if require_hdf5_binding and hdf5_digest!=digest:
            raise ValueError("Episode HDF5 event-ledger digest does not match the event file")
        if require_physics_trace:
            manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
            metadata=manifest.get("metadata")
            contract=metadata.get("physics_trace_contract") if isinstance(metadata,dict) else None
            if (manifest.get("physics_trace_schema_version")!=PHYSICS_TRACE_SCHEMA or
                    not isinstance(contract,dict) or contract.get("schema_version")!=PHYSICS_TRACE_SCHEMA or
                    contract.get("sampling_hz")!=PHYSICS_TRACE_HZ or
                    contract.get("substeps_per_observation")!=PHYSICS_TRACE_SUBSTEPS or
                    contract.get("sample_semantics")!="post_mj_step"):
                raise ValueError("Episode is missing the declared MuJoCo physical-trace contract")
            episode_id=manifest.get("episode_id")
            input_source=manifest.get("input_source")
            if input_source not in INPUT_SOURCES:
                raise ValueError("Episode input source is invalid for physical-trace review")
            if isinstance(hdf5_episode_id,bytes):hdf5_episode_id=hdf5_episode_id.decode("utf-8",errors="replace")
            if isinstance(hdf5_input_source,bytes):hdf5_input_source=hdf5_input_source.decode("utf-8",errors="replace")
            if isinstance(hdf5_contract_sha256,bytes):hdf5_contract_sha256=hdf5_contract_sha256.decode("ascii",errors="replace")
            expected_contract_sha256=_canonical_json_sha256(contract)
            if (hdf5_episode_id!=episode_id or hdf5_input_source!=input_source or
                    hdf5_contract_sha256!=expected_contract_sha256):
                raise ValueError("Episode source or physical-trace contract differs from its HDF5 provenance binding")
    except ValueError:
        raise
    except (OSError,KeyError,json.JSONDecodeError) as error:
        raise ValueError("Episode event ledger is required and HDF5 binding must be available") from error
    event_count=0;trace_count=0;terminal=None;last_row=None;faults=[]
    pc_input_samples=[];quest_recording_started=[]
    try:
        with path.open("r",encoding="utf-8") as source:
            for expected,line in enumerate(source,1):
                try:row=json.loads(line)
                except (json.JSONDecodeError,TypeError) as error:
                    raise ValueError("Episode event ledger contains malformed JSON") from error
                if (not isinstance(row,dict) or type(row.get("sequence")) is not int or
                        row["sequence"]!=expected or not isinstance(row.get("event"),str) or
                        not row["event"].strip() or not isinstance(row.get("details"),dict)):
                    raise ValueError("Episode event ledger has invalid schema or sequence")
                event_count=expected;last_row=row
                if row["event"]=="EPISODE_ENDED":
                    if terminal is not None:raise ValueError("Episode event ledger has multiple terminal events")
                    terminal=row
                if row["event"] in NONEXPORTABLE_EVENT_KINDS:faults.append(row["event"])
                if require_physics_trace and row["event"]=="PC_INPUT_SAMPLE":
                    details=row["details"]
                    if details.get("input_source")!=manifest.get("input_source"):
                        raise ValueError("PC input provenance event does not match the Episode input source")
                    if type(details.get("generated_held_keys")) is not bool:
                        raise ValueError("PC input provenance event is missing generated-input status")
                    if (manifest.get("input_source") in ("pc_human_teleop","quest_human_teleop") and
                            details["generated_held_keys"]):
                        raise ValueError("Human-source Episode contains generated PC input")
                    pc_input_samples.append(int(row["sim_tick"]))
                if require_physics_trace and row["event"]=="RECORDING_STARTED":
                    if row["details"].get("source")!=manifest.get("input_source"):
                        raise ValueError("Quest recording provenance event does not match the Episode input source")
                    quest_recording_started.append(row)
                if require_physics_trace and row["event"]==PHYSICS_TRACE_EVENT:
                    if trace_count>=committed:
                        raise ValueError("Episode has an orphan physical trace without a committed HDF5 frame")
                    details=row["details"]
                    if (details.get("episode_id")!=manifest.get("episode_id") or
                            details.get("input_source")!=manifest.get("input_source") or
                            details.get("physics_trace_contract_sha256")!=expected_contract_sha256):
                        raise ValueError("Physical trace provenance differs from its HDF5-bound Episode source/contract")
                    _validate_physics_trace_row(row,trace_count,int(hdf5_ticks[trace_count]),contract,
                        expected_state=hdf5_states[trace_count],expected_action=hdf5_actions[trace_count],
                        expected_ctrl=hdf5_controls[trace_count],require_running_controller=True)
                    trace_count+=1
    except OSError as error:
        raise ValueError("Episode event ledger is unavailable") from error
    if event_count==0:raise ValueError("Episode event ledger is empty")
    if terminal is None or terminal is not last_row:
        raise ValueError("Episode event ledger must have exactly one final EPISODE_ENDED event")
    if faults:
        raise ValueError("Episode contains non-exportable control/recording fault event(s): "+", ".join(faults))
    if terminal["details"].get("outcome")!="incomplete" or terminal["details"].get("reason")!="operator_review_pending":
        raise ValueError("Episode event ledger does not show a normal operator-review stop")
    if manifest is not None and manifest.get("task_binding_schema_version") is not None:
        rows=[]
        with path.open("r",encoding="utf-8") as source:
            rows=[json.loads(line) for line in source]
        _validate_task_annotation_binding(directory,manifest,rows)
    if require_physics_trace:
        if (trace_count!=committed or int(manifest.get("physics_trace_frame_count",-1))!=committed or
                int(manifest.get("frame_count",-1))!=committed):
            raise ValueError("Episode physics trace count does not match committed HDF5 frames")
        if not committed or (committed>1 and np.any(np.diff(hdf5_ticks)!=PHYSICS_TRACE_SUBSTEPS)):
            raise ValueError("Episode HDF5 frame ticks are empty or noncontiguous")
        input_source=manifest.get("input_source")
        if input_source=="pc_human_teleop" and pc_input_samples!=hdf5_ticks.tolist():
            raise ValueError("PC human input provenance must match every recorded observation tick")
        if input_source=="quest_human_teleop" and len(quest_recording_started)!=1:
            raise ValueError("Quest human Episode must contain one matching RECORDING_STARTED provenance event")
    return {"event_count":event_count,"terminal_sequence":event_count,"path":path,"sha256":digest,
        "physics_trace_frames":trace_count}



def capacity_preflight(root,*,seconds=120,height=256,width=256,reserve_bytes=RESERVE_BYTES):
    root=Path(root).expanduser().resolve()
    if seconds<=0 or height<=0 or width<=0:raise ValueError("Positive duration/resolution required")
    parent=root
    while not parent.exists():parent=parent.parent
    free=shutil.disk_usage(parent).free
    frame_count=int(seconds*25)
    episode_bytes=int(frame_count*(3*height*width*3+20*16*8+4096))
    trace_bytes=int(frame_count*PHYSICS_TRACE_MAX_BYTES_PER_FRAME)
    raw=episode_bytes+trace_bytes
    result={"data_root":str(root),"free_bytes":free,"estimated_uncompressed_bytes":raw,
        "episode_estimate_bytes":episode_bytes,"physics_trace_bound_bytes":trace_bytes,
        "physics_trace_max_contacts_per_substep":PHYSICS_TRACE_MAX_CONTACTS_PER_SUBSTEP,
        "reserve_bytes":reserve_bytes,"status":"PASS" if free>=raw+reserve_bytes else "INSUFFICIENT_SPACE"}
    if result["status"]!="PASS":raise OSError("Capacity preflight failed: "+json.dumps(result))
    return result

def _atomic_json(path,payload):
    tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8") as stream:
        json.dump(payload,stream,ensure_ascii=False,indent=2)
        stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)

def finalize_operator_review(directory,*,success=None,keep=None):
    """Record human outcome and retention as separate review decisions."""
    if success is None and keep is None:raise ValueError("Provide a success label, a keep decision, or both")
    if success is not None and type(success) is not bool:raise TypeError("success must be bool or None")
    if keep is not None and type(keep) is not bool:raise TypeError("keep must be bool or None")
    path=Path(directory)/"manifest.json"
    manifest=json.loads(path.read_text(encoding="utf-8"))
    if success is not None or keep is True:
        _validate_episode_provenance_binding(Path(directory),manifest)
    human_source=manifest.get("input_source") in ("pc_human_teleop","quest_human_teleop")
    if not human_source and (success is not None or keep is True):
        raise ValueError("Only a human teleoperation episode can receive human labels or be retained")
    reviewable=(manifest.get("status")=="incomplete" and
        manifest.get("termination_reason")=="operator_review_pending" and
        int(manifest.get("frame_count",0))>0)
    previously_reviewed=(manifest.get("operator_validation") in
        ("HUMAN_CONFIRMED_SUCCESS","HUMAN_CONFIRMED_FAILURE") and int(manifest.get("frame_count",0))>0)
    if not (reviewable or previously_reviewed):
        raise ValueError("Operator review requires a nonempty episode closed for review")
    if success is True:
        validate_episode_event_ledger(Path(directory),expected_sha256=manifest.get("event_log_sha256"),
            require_physics_trace=True)
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    if success is not None:
        manifest.update(status="success" if success else "failure",
            operator_success_label=success,
            operator_validation="HUMAN_CONFIRMED_SUCCESS" if success else "HUMAN_CONFIRMED_FAILURE",
            automatic_task_success=None,success_labeled_utc=now)
    if keep is not None:
        manifest.update(keep=keep,keep_decided_utc=now)
    _atomic_json(path,manifest)
    return manifest

class EpisodeWriter:
    """One writer owns a file; commit count exposes complete rows only."""
    def __init__(self,data_root=DEFAULT_DATA_ROOT,*,instruction,input_source,task_id=None,
                 height=256,width=256,expected_seconds=120,metadata=None,reserve_bytes=RESERVE_BYTES):
        if input_source not in INPUT_SOURCES:raise ValueError("Explicit input evidence source required")
        if not instruction.strip():raise ValueError("Full instruction required")
        self.task_tracker=TaskAnnotationTracker(task_id,input_source)
        task_identity=catalog_identity() if task_id else None
        self.capacity=capacity_preflight(data_root,seconds=expected_seconds,height=height,width=width,reserve_bytes=reserve_bytes)
        self.reserve_bytes=reserve_bytes;self.height=height;self.width=width
        self.id=uuid.uuid4().hex
        self.directory=Path(data_root)/input_source/self.id
        self.directory.mkdir(parents=True,exist_ok=False)
        self.partial=self.directory/"episode.partial.h5"
        self.manifest={"episode_id":self.id,"instruction":instruction,"input_source":input_source,
            "task_binding_schema_version":TASK_ANNOTATION_SCHEMA_VERSION,
            "task_id":task_id,"task_catalog_version":task_identity["version"] if task_identity else None,
            "task_catalog_sha256":task_identity["sha256"] if task_identity else None,
            "task_annotation_status":self.task_tracker.summary()["status"],
            "task_annotation":self.task_tracker.summary(),
            "operator_validation":"NOT_VERIFIED" if input_source=="programmatic_test" else "OPERATOR_INPUT_SOURCE",
            "physical_quality_status":"NOT_VERIFIED","physical_quality_evidence":None,
            "physics_trace_schema_version":None,"physics_trace_frame_count":0,
            "schema_version":2,"fps":25,"physics_hz":500,"camera_names":list(CAMERAS),
            "camera_shape":[height,width,3],"action_names":action_names(),"state_names":state_names(),
            "actual_ctrl_names":actual_ctrl_names(),
            "state_gripper_semantics":"mean measured slide coordinate, not physical aperture",
            "action_semantics":"accepted 25Hz absolute target; actual substep ctrl stored separately",
            "status":"recording","keep":True,"created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "metadata":metadata or {},"capacity":self.capacity}
        initial_snapshot=(metadata or {}).get("initial_snapshot")
        if isinstance(initial_snapshot,dict) and isinstance(initial_snapshot.get("model_identity"),dict):
            self.manifest["model_identity"]=copy.deepcopy(initial_snapshot["model_identity"])
        _atomic_json(self.directory/"manifest.json",self.manifest)
        self.file=h5py.File(self.partial,"x",libver="latest")
        self.file.attrs["schema_version"]=2
        contract=(metadata or {}).get("physics_trace_contract") if isinstance(metadata,dict) else None
        self.file.attrs["episode_id"]=self.id
        self.file.attrs["input_source"]=input_source
        self.file.attrs["task_binding_schema_version"]=TASK_ANNOTATION_SCHEMA_VERSION
        self.file.attrs["task_id"]=task_id or ""
        self.file.attrs["task_catalog_sha256"]=task_identity["sha256"] if task_identity else ""
        self.file.attrs["physics_trace_contract_sha256"]=(
            _canonical_json_sha256(contract) if isinstance(contract,dict) else "")
        self.datasets={}
        shapes={"state":(16,),"action":(16,),"actual_ctrl":(20,16),
            "images":(3,height,width,3),"sim_tick":(),"capture_monotonic_ns":(),"capture_end_monotonic_ns":(),
            "camera_poses":(3,7),"joint_velocity":(16,),"raw_tcp_target":(2,7),"raw_tendon_target":(2,),"feedback":(4,)}
        for name,shape in shapes.items():
            dtype=np.uint8 if name=="images" else np.uint64 if name in ("sim_tick","capture_monotonic_ns","capture_end_monotonic_ns") else np.float64
            self.datasets[name]=self.file.create_dataset(name,shape=(0,)+shape,maxshape=(None,)+shape,
                chunks=(1,)+shape,dtype=dtype,compression="lzf",fletcher32=True)
        self.commit=self.file.create_dataset("committed_frames",shape=(),dtype=np.uint64)
        self.commit[()]=0
        self.file.flush();self.file.swmr_mode=True
        self.count=0;self.last_tick=None;self.closed=False;self.physics_trace_count=0
        self.event_file=(self.directory/"events.jsonl").open("x",encoding="utf-8")
        self.event_sequence=0

    def event(self,kind,*,sim_tick=None,**details):
        if self.closed:raise RuntimeError("Closed episode")
        if kind==PHYSICS_TRACE_EVENT:
            contract=self.manifest.get("metadata",{}).get("physics_trace_contract")
            if not isinstance(contract,dict):raise ValueError("Physical trace provenance requires a declared contract")
            bindings={"episode_id":self.manifest["episode_id"],
                "input_source":self.manifest["input_source"],
                "physics_trace_contract_sha256":_canonical_json_sha256(contract)}
            if any(key in details and details[key]!=value for key,value in bindings.items()):
                raise ValueError("Physical trace provenance binding cannot be overridden")
            details.update(bindings)
        next_sequence=self.event_sequence+1
        payload={"sequence":next_sequence,"event":kind,"sim_tick":sim_tick,"details":details}
        try:encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(",",":"))
        except (TypeError,ValueError) as error:
            raise ValueError("Episode event contains a non-JSON or nonfinite value") from error
        candidate=None
        if kind in ("TASK_SUBTASK_STARTED","TASK_SUBTASK_ENDED") or kind in DEVICE_TASK_EVENTS:
            candidate=copy.deepcopy(self.task_tracker)
            candidate.apply_event(kind,sim_tick,details)
        if kind==PHYSICS_TRACE_EVENT:
            contract=self.manifest.get("metadata",{}).get("physics_trace_contract")
            _validate_physics_trace_row(payload,self.physics_trace_count,int(sim_tick),contract or {})
            if self.last_tick is not None and int(sim_tick)!=self.last_tick+PHYSICS_TRACE_SUBSTEPS:
                raise ValueError("Physical trace does not correspond to the next HDF5 observation row")
            trace_bytes=len(encoded.encode("utf-8"))
            if trace_bytes>PHYSICS_TRACE_MAX_BYTES_PER_FRAME:
                raise RuntimeError("MuJoCo physical trace exceeds its declared per-frame storage bound")
        self.event_file.write(encoded+"\n")
        self.event_file.flush()
        self.event_sequence=next_sequence
        if candidate is not None:self.task_tracker=candidate
        if kind==PHYSICS_TRACE_EVENT:self.physics_trace_count+=1

    def append(self,frame):
        if self.closed:raise RuntimeError("Closed episode")
        if shutil.disk_usage(self.directory).free<self.reserve_bytes:raise OSError("Data reserve reached")
        tick=int(frame["sim_tick"])
        if tick<0 or (self.last_tick is not None and tick!=self.last_tick+20):
            raise ValueError("Non-contiguous observation tick; reset needs a new episode")
        values=dict(frame);values.setdefault("feedback",np.zeros(4))
        for key,dataset in self.datasets.items():
            value=np.asarray(values[key],dtype=dataset.dtype)
            if value.shape!=dataset.shape[1:]:raise ValueError("Invalid shape for "+key)
            if value.dtype.kind=="f" and not np.isfinite(value).all():raise ValueError("Nonfinite "+key)
            if key=="images" and np.asarray(values[key]).dtype!=np.uint8:raise ValueError("RGB must be uint8")
        for key,dataset in self.datasets.items():
            dataset.resize(self.count+1,axis=0);dataset[self.count]=values[key];dataset.flush()
        self.commit[()]=self.count+1;self.commit.flush()
        self.count+=1;self.last_tick=tick

    def close(self,*,outcome="incomplete",reason="operator_stop",keep=True):
        if self.closed:return self.directory
        if outcome not in ("success","failure","aborted","incomplete"):raise ValueError("Invalid outcome")
        self.event("EPISODE_ENDED",sim_tick=self.last_tick,outcome=outcome,reason=reason)
        task_summary=self.task_tracker.summary()
        task_summary_sha256=_canonical_json_sha256(task_summary)
        self.manifest["task_annotation_status"]=task_summary["status"]
        self.manifest["task_annotation"]=task_summary
        self.manifest["physics_trace_frame_count"]=self.physics_trace_count
        self.manifest["physics_trace_schema_version"]=(
            PHYSICS_TRACE_SCHEMA if self.count>0 and self.physics_trace_count==self.count else None)
        self.event_file.flush()
        event_log_sha256=hashlib.sha256((self.directory/"events.jsonl").read_bytes()).hexdigest()
        self.file.attrs["event_log_sha256"]=event_log_sha256
        self.file.attrs["task_annotation_summary_sha256"]=task_summary_sha256
        self.file.flush();self.file.close();self.event_file.close();self.closed=True
        target=self.directory/"episode.h5"
        if target.exists():raise FileExistsError(target)
        self.partial.rename(target)
        self.manifest.update(status=outcome,termination_reason=reason,keep=bool(keep),
            event_log_sha256=event_log_sha256,
            frame_count=self.count,ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hdf5_file=target.name)
        _atomic_json(self.directory/"manifest.json",self.manifest)
        return self.directory

def recover_episode(directory):
    """Read without repairing or overwriting the crashed partial."""
    directory=Path(directory);path=directory/"episode.h5"
    if not path.exists():path=directory/"episode.partial.h5"
    try:
        with h5py.File(path,"r",libver="latest",swmr=True) as stream:
            count=int(stream["committed_frames"][()])
            keys=("state","action","actual_ctrl","images","sim_tick","capture_monotonic_ns","feedback")
            if int(stream.attrs["schema_version"])>=2:
                keys+=("capture_end_monotonic_ns","camera_poses","joint_velocity","raw_tcp_target","raw_tendon_target")
            available=min([count]+[int(stream[k].shape[0]) for k in keys])
            ticks=stream["sim_tick"][:available]
            contiguous=bool(len(ticks)<2 or np.all(np.diff(ticks)==20))
            finite=all(np.isfinite(stream[k][:available]).all() for k in keys if stream[k].dtype.kind=="f")
            # Read RGB row by row to verify Fletcher32 without loading the episode into RAM.
            for row in range(available):stream["images"][row]
            if "capture_end_monotonic_ns" in stream:
                finite=bool(finite and np.all(stream["capture_end_monotonic_ns"][:available]>=stream["capture_monotonic_ns"][:available]))
            return {"status":"RECOVERABLE_PREFIX" if contiguous and finite else "INVALID_PREFIX",
                "episode_directory":str(directory),"file":str(path),"committed_frames":count,
                "readable_frames":available,"contiguous":contiguous,"finite":finite,
                "original_status":"incomplete" if path.name.endswith("partial.h5") else "closed",
                "source_mutated":False,"power_loss_guarantee":False}
    except (OSError,KeyError,ValueError) as error:
        return {"status":"UNRECOVERABLE","episode_directory":str(directory),"error":str(error),
            "source_mutated":False,"power_loss_guarantee":False}

class AsyncEpisodeWriter:
    @property
    def directory(self):
        return self.writer.directory

    def __init__(self,*args,**kwargs):
        self.writer=EpisodeWriter(*args,**kwargs)
        self.task_tracker=copy.deepcopy(self.writer.task_tracker)
        self.queue=queue.Queue(maxsize=16);self.error=None;self.closed=False;self.queued_frames=0
        self.worker=threading.Thread(target=self._run,name="KitchenEpisodeWriter",daemon=True)
        self.worker.start()
    def _run(self):
        while True:
            item=self.queue.get()
            try:
                if item is None:return
                if "__barrier__" in item:
                    item["__barrier__"].set()
                elif self.error is None:
                    if "__episode_event__" in item:
                        payload=item["__episode_event__"]
                        self.writer.event(payload["kind"],sim_tick=payload.get("sim_tick"),**payload.get("details",{}))
                    elif "__episode_frame__" in item:
                        payload=item["__episode_frame__"]
                        trace=payload.get("physics_trace")
                        if trace is not None:
                            observation_tick=int(payload["frame"]["sim_tick"])
                            trace.update(schema_version=PHYSICS_TRACE_SCHEMA,
                                frame_index=payload["frame_index"],observation_sim_tick=observation_tick,
                                action_start_sim_tick=observation_tick+1,
                                action_end_sim_tick=observation_tick+PHYSICS_TRACE_SUBSTEPS,
                                physics_hz=PHYSICS_TRACE_HZ,sample_count=PHYSICS_TRACE_SUBSTEPS,
                                pre_action_state16=np.asarray(payload["frame"]["state"],dtype=float).tolist(),
                                accepted_action16=np.asarray(payload["frame"]["action"],dtype=float).tolist())
                            self.writer.event(PHYSICS_TRACE_EVENT,
                                sim_tick=observation_tick,**trace)
                        self.writer.append(payload["frame"])
                    else:self.writer.append(item)
            except BaseException as error:self.error=error
            finally:self.queue.task_done()
    def append(self,frame,*,physics_trace=None):
        if self.closed:raise RuntimeError("Closed asynchronous writer")
        if self.error:raise RuntimeError("Writer failed") from self.error
        frame_payload={k:np.array(v,copy=True) for k,v in frame.items()}
        if physics_trace is None:
            payload=frame_payload
        else:
            if not isinstance(physics_trace,dict):raise TypeError("physics_trace must be an object")
            # The caller transfers ownership of this newly built per-frame
            # snapshot to the bounded worker queue and must not mutate it.
            # JSON validation/encoding stays in the worker, outside the 25 Hz loop.
            payload={"__episode_frame__":{"frame":frame_payload,"physics_trace":physics_trace,
                "frame_index":self.queued_frames}}
        try:self.queue.put_nowait(payload)
        except queue.Full:raise RuntimeError("Writer backlog exceeded; stop episode incomplete")
        self.queued_frames+=1
    def event(self,kind,*,sim_tick=None,**details):
        if self.closed:raise RuntimeError("Closed asynchronous writer")
        if self.error:raise RuntimeError("Writer failed") from self.error
        details_snapshot=copy.deepcopy(details)
        candidate=copy.deepcopy(self.task_tracker)
        candidate.apply_event(kind,sim_tick,details_snapshot)
        try:self.queue.put_nowait({"__episode_event__":{"kind":kind,"sim_tick":sim_tick,"details":details_snapshot}})
        except queue.Full:raise RuntimeError("Writer backlog exceeded while recording event")
        self.task_tracker=candidate
    def task_segment_start(self,node_id,sim_tick):
        source=("operator_annotation" if self.writer.manifest["input_source"] in
                ("pc_human_teleop","quest_human_teleop") else "programmatic_test")
        self.event("TASK_SUBTASK_STARTED",sim_tick=sim_tick,node_id=node_id,
            annotation_source=source)
        return node_id
    def task_segment_end(self,node_id,sim_tick):
        source=("operator_annotation" if self.writer.manifest["input_source"] in
                ("pc_human_teleop","quest_human_teleop") else "programmatic_test")
        self.event("TASK_SUBTASK_ENDED",sim_tick=sim_tick,node_id=node_id,
            annotation_source=source)
        return node_id
    def flush(self,timeout=5.):
        """Wait for all previously queued frames/events and surface worker errors."""
        if self.closed:raise RuntimeError("Closed asynchronous writer")
        if self.error:raise RuntimeError("Writer failed") from self.error
        barrier=threading.Event()
        try:self.queue.put({"__barrier__":barrier},timeout=timeout)
        except queue.Full:raise RuntimeError("Writer backlog did not drain before barrier")
        if not barrier.wait(timeout):raise TimeoutError("Writer barrier timed out")
        if self.error:raise RuntimeError("Writer failed") from self.error
    def _shutdown_failure(self,message):
        failure=TimeoutError(message)
        if self.error is None:self.error=failure
        # A stalled worker may still own HDF5. Do not close it concurrently;
        # persist exclusion immediately and retain the recoverable partial.
        self.writer.manifest.update(status="incomplete",keep=False,
            termination_reason="writer_shutdown_timeout",
            writer_shutdown_state="TIMED_OUT_PARTIAL_RETAINED",shutdown_error=message,
            async_writer_error=repr(self.error),writer_queue_depth_at_close=self.queue.qsize(),
            writer_worker_alive=self.worker.is_alive())
        _atomic_json(self.writer.directory/"manifest.json",self.writer.manifest)
        return failure

    def close(self,**kwargs):
        if self.closed:return self.writer.directory
        try:self.queue.put(None,timeout=5)
        except queue.Full as error:
            raise self._shutdown_failure("Writer shutdown queue did not drain") from error
        self.worker.join(timeout=30)
        if self.worker.is_alive():
            raise self._shutdown_failure("Writer did not close")
        self.closed=True
        if self.error:
            self.writer.manifest.update(async_writer_error=repr(self.error),
                writer_queue_depth_at_close=self.queue.qsize(),writer_worker_alive=self.worker.is_alive())
            kwargs.update(outcome="incomplete",reason="writer_failure")
        self.writer.manifest.pop("writer_shutdown_state",None)
        return self.writer.close(**kwargs)
