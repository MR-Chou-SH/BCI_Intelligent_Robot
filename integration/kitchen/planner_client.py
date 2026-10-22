"""External-planner example: query, submit bounded targets, step and read evidence."""
from __future__ import annotations

import argparse
import json
import socket
import uuid


class PlannerClient:
    def __init__(self,host="127.0.0.1",port=11031,timeout=30.):
        self.socket=socket.create_connection((host,port),timeout=timeout)
        self.file=self.socket.makefile("rb")
        self.token=None
        self.writer_id=uuid.uuid4().hex

    def call(self,method,args=None,*,mutating=False,request_id=None,token=None):
        if mutating and self.token is None:self.call("observe")
        message={"method":method,"args":args or {}}
        if mutating:message.update(request_id=request_id or uuid.uuid4().hex,token=token or self.token,writer_id=self.writer_id)
        self.socket.sendall((json.dumps(message,allow_nan=False)+"\n").encode())
        line=self.file.readline(16*1024*1024)
        if not line or not line.endswith(b"\n"):raise ConnectionError("Missing or oversized complete reply")
        result=json.loads(line)
        if "token" in result:self.token=result["token"]
        return result

    def close(self):
        self.file.close();self.socket.close()

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def example(host,port):
    results={}
    with PlannerClient(host,port) as client:
        manifest=client.call("get_scene_manifest")
        if not manifest["ok"]:raise RuntimeError(manifest)
        results["scene_version"]=manifest["result"]["scene_version"]
        results["object_ids"]=manifest["result"]["object_ids"]
        reset=client.call("reset",{"seed":0},mutating=True)
        if not reset["ok"]:raise RuntimeError(reset)
        targets=reset["result"]["accepted_target16"]
        targets[0]+=.002
        checked=client.call("check_motion",{"end_joint_targets":targets})
        if not checked["ok"] or not checked["result"]["pass"]:raise RuntimeError(checked)
        results["submit"]=client.call("submit_joint_targets",{"joint_targets":targets},mutating=True)
        results["step"]=client.call("step",{"n_substeps":20},mutating=True)
        results["execution"]=client.call("get_execution_status")
        results["feedback"]=client.call("get_task_feedback",{"task_instance_id":"example-only","object_ids":["apple"]})
        results["removed_rice"]=client.call("query_objects",{"ids":["rice"]})
        results["hold"]=client.call("hold",mutating=True)
        results["evidence_boundary"]="API smoke; not a grasp, microwave or tray physical PASS"
    return results


def contact_example(client):
    """Home-only contract demonstration, explicitly not a physical device task."""
    def require(reply):
        if not reply.get("ok"):raise RuntimeError(reply)
        return reply["result"]
    state=require(client.call("reset",{"seed":0},mutating=True))
    q=state["reference16"]
    contract={"arm":"right","target":"microwave_door_handle","phase":"acquire","payload_ids":[],"supports":{}}
    preview=require(client.call("check_contact_motion",{"waypoints":[q],"contact_contract":contract}))
    if not preview["pass"] or client.token["sim_tick"]!=0:raise RuntimeError(preview)
    acquired=require(client.call("execute_trajectory",{"waypoints":[q,q],"contact_contract":contract},mutating=True))
    completed=require(client.call("step",{"n_substeps":40},mutating=True))
    if completed["execution"]["status"]!="COMPLETED" or not completed["contact_execution"]["active"]:raise RuntimeError(completed)
    monitored=require(client.call("step",{"n_substeps":20},mutating=True))
    if monitored["contact_execution"]["measured_post_step_samples"]!=60:raise RuntimeError(monitored)
    contract["phase"]="release"
    released=require(client.call("execute_trajectory",{"waypoints":[q,q],"contact_contract":contract},mutating=True))
    require(client.call("step",{"n_substeps":40},mutating=True))
    ended=require(client.call("end_contact",mutating=True))
    final=require(client.call("observe"))
    if final["contact_execution"]["active"]:raise RuntimeError(final)
    return {"status":"PC_CONTACT_LIFECYCLE_ENTRY_PASS","preview":preview,"acquire":acquired,"release":released,
        "end":ended,"final_tick":client.token["sim_tick"],"physical_device_or_grasp_success":"NOT_VERIFIED_HOME_ONLY"}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=11031)
    args=parser.parse_args()
    print(json.dumps(example(args.host,args.port),indent=2,allow_nan=False))


if __name__=="__main__":main()
