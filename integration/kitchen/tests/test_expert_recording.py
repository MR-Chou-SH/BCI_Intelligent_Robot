import gzip
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import mujoco

from robot_arm.kitchen.platform import KitchenPlatform
from integration.kitchen.expert import LocalPlannerClient
from integration.kitchen.collection import audit_record


class ExpertRecordingTests(unittest.TestCase):
    def test_food_support_bounce_is_bounded_and_does_not_invent_contacts(self):
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            p.advance(500)
            engine=p.contact._clone();scene=engine.scene;m=scene.model
            body=scene.objects["hotdog"];address=int(m.jnt_qposadr[int(m.body_jntadr[body])])
            scene.data.qpos[address+2]+=.002
            mujoco.mj_forward(m,scene.data)
            contract={"phase":"carry","target":"hotdog_container",
                      "payload_ids":["hotdog_container","hotdog"]}
            measured={"hotdog":{"contacts":[]},"hotdog_container":{"contacts":[]}}
            self.assertFalse(engine._payload_support_violations(contract,measured,.04))
            self.assertFalse(engine._payload_support_violations(contract,measured,0.))
            self.assertFalse(engine._payload_support_violations(contract,measured,.04))
            self.assertTrue(engine._payload_support_violations(contract,measured,.04))
            engine._payload_support_loss_s.clear()
            scene.data.qpos[address]+=.1;mujoco.mj_forward(m,scene.data)
            self.assertTrue(engine._payload_support_violations(contract,measured,.002))
            self.assertEqual(measured["hotdog"]["contacts"],[])
            self.assertFalse(p.contact._payload_support_loss_s)
        finally:p.close()

    def test_negative_ordinary_tolerance_does_not_fake_deep_support_contact(self):
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            p.advance(500)
            contract={"arm":"left","target":"banana","phase":"acquire","payload_ids":["banana"],
                      "supports":{"banana":["v3_counter_front"]}}
            p.contact.activate(contract)
            native=mujoco.mj_geomDistance
            with patch("robot_arm.kitchen.contact_execution.ORDINARY_MARGIN_M",-.003), \
                 patch("mujoco.mj_geomDistance",wraps=native) as distance:
                violations=p.contact._contact_checks()
                self.assertTrue(distance.call_args_list)
                self.assertTrue(all(call.args[4]>0 for call in distance.call_args_list))
                self.assertFalse(violations,violations)
                scratch=p.contact._clone()
                model=scratch.scene.model
                joint=int(model.body_jntadr[scratch.scene.objects["banana"]])
                address=int(model.jnt_qposadr[joint])
                scratch.scene.data.qpos[address+2]-=.004
                mujoco.mj_forward(model,scratch.scene.data)
                deep=scratch._contact_checks()
                self.assertTrue(any(v["code"]=="CLEARANCE_VIOLATION" and
                    "banana_0" in v.get("pair",[]) for v in deep),deep)
        finally:p.close()

    def test_distributed_native_support_uses_resultant_and_rejects_weak_load(self):
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            p.advance(500)
            supports=["v3_counter_front"]
            evidence=p.controller.devices.support_evidence("banana",supports)
            self.assertGreater(evidence["matched_contact_count"],1)
            self.assertLess(evidence["maximum_upward_normal_force_n"],.25)
            self.assertGreater(evidence["total_qualifying_upward_support_force_n"],1.)
            self.assertTrue(evidence["supported"])
            contract={"arm":"left","target":"banana","phase":"acquire","payload_ids":["banana"],
                      "supports":{"banana":supports}}
            p.contact.activate(contract)
            accepted=0
            for i,c in enumerate(p.scene.data.contact):
                a,b=int(c.geom1),int(c.geom2)
                if p.contact._support_contact_role(a,b,c,0.) is not None:
                    force=__import__('numpy').zeros(6)
                    mujoco.mj_contactForce(p.scene.model,p.scene.data,i,force)
                    self.assertTrue(p.contact._support_contact(a,b,c,float(force[0])))
                    accepted+=1
            self.assertGreater(accepted,1)
            def weak_force(model,data,index,out):
                out[:]=0.;out[0]=.02
            with patch("mujoco.mj_contactForce",side_effect=weak_force):
                weak=p.controller.devices.support_evidence("banana",supports)
                self.assertFalse(weak["supported"])
                self.assertLess(weak["total_qualifying_upward_support_force_n"],.25)
            self.assertFalse(p.controller.devices.supports("banana",["microwave_bottom"]))
        finally:p.close()

    def test_gzip_raw_trace_roundtrip_still_has_all_twenty_substeps(self):
        with tempfile.TemporaryDirectory() as temp:
            p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0,
                              data_root=Path(temp)/"data",evidence_compression="gzip",record_physics=True)
            try:
                c=LocalPlannerClient(p,Path(temp)/"rpc.jsonl.gz")
                q=p.controller.reference.tolist();q[0]+=.002
                reply=c.call("execute_trajectory",{"waypoints":[q]},mutating=True)
                self.assertTrue(reply["ok"])
                path=reply["result"]["evidence_path"]
                self.assertTrue(path.endswith(".jsonl.gz"))
                self.assertTrue(c.call("step",{"n_substeps":20},mutating=True)["ok"])
                audit=audit_record(path,p.episode["episode_id"])
                self.assertEqual(audit["samples"],20)
                with gzip.open(path,"rt") as stream:self.assertEqual(sum(1 for _ in stream),22)
                from integration.kitchen.platform_research_handoff_example import read_record
                output=Path(temp)/"audit";output.mkdir()
                state=c.call("observe")["result"]
                copied=read_record(Path(path),output,state,state["execution"])
                self.assertTrue(copied["copied_path"].endswith(".jsonl.gz"))
                self.assertEqual(copied["samples"],20)
            finally:p.close()

    def test_dynamic_tray_fixture_requires_support_rest_and_retained_target(self):
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            p.advance(100)
            contract={"arm":"left","target":"apple","phase":"acquire","payload_ids":["apple"],
                      "supports":{"apple":["tray_bottom"]},"target_support_transition":False}
            p.contact.activate(contract)
            apple=p.scene.model.geom("apple_collision").id
            tray=p.scene.model.geom("tray_bottom").id
            self.assertEqual(p.contact._static_target_support_role(apple,tray),(apple,tray))
            self.assertFalse(p.contact._transition_ready())
            clone=p.contact._clone()
            body=clone.scene.objects["wood_tray"]
            j=int(clone.scene.model.body_jntadr[body]);dof=int(clone.scene.model.jnt_dofadr[j])
            clone.scene.data.qvel[dof]=.1
            mujoco.mj_forward(clone.scene.model,clone.scene.data)
            self.assertIsNone(clone._static_target_support_role(apple,tray))
            clone=p.contact._clone()
            adr=int(clone.scene.model.jnt_qposadr[j]);clone.scene.data.qpos[adr+2]+=.1
            mujoco.mj_forward(clone.scene.model,clone.scene.data)
            self.assertIsNone(clone._static_target_support_role(apple,tray))
        finally:p.close()


if __name__=="__main__":unittest.main()
