import unittest
from collections import Counter
from unittest.mock import patch
import math
import numpy as np
from integration.kitchen.expert_catalog import catalogue,cases,NAMESPACE
from robot_arm.kitchen.platform import KitchenPlatform
import robot_arm.kitchen.contact_execution as contact_execution


class ExpertContracts(unittest.TestCase):
    def test_button_fragment_retains_stationary_gripper_preparation(self):
        from integration.kitchen.expert import KitchenExpert,LocalPlannerClient,reference_calls_between
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            expert=KitchenExpert(p,LocalPlannerClient(p))
            with patch.object(expert,"reference_window") as window:
                expert.reference_device("heat_open")
            start,end=window.call_args.args
            self.assertEqual(start,35440)
            calls=reference_calls_between(start,end)
            guarded=[r for r in calls if r["method"]=="execute_trajectory" and r["args"].get("guarded_gripper")]
            self.assertEqual(sum(r["start_tick"]<39220 for r in guarded),4)
        finally:p.close()

    def test_reference_pick_window_clips_batch_and_excludes_later_release(self):
        from integration.kitchen.expert import reference_calls_between
        calls=reference_calls_between(10980,15660)
        self.assertEqual(calls[-1]["method"],"step")
        self.assertEqual(calls[-1]["args"]["n_substeps"],40)
        self.assertEqual(sum(r["args"]["n_substeps"] for r in calls if r["method"]=="step"),4680)
        self.assertFalse(any(r["method"]=="end_contact" for r in calls))
        self.assertEqual(len([r for r in calls if r["method"]=="execute_trajectory"][-1]["args"]["waypoints"]),2)
        load=reference_calls_between(10980,23260)
        self.assertEqual(sum(r["method"]=="end_contact" for r in load),1)
        self.assertTrue(all(r["start_tick"]<=23260 and r["end_tick"]<=23260 for r in load))

    def test_fruit_speed_allowances_require_matching_phase_and_contact(self):
        limits=contact_execution.measured_speed_limits
        self.assertEqual(limits("pear",{"target":"pear","phase":"release"},{"pads_separated":True}),(.40,4.))
        self.assertEqual(limits("pear",{"target":"pear","phase":"release"},{"pads_separated":False}),(.30,4.))
        self.assertEqual(limits("pear",{"target":"pear","phase":"carry"},{"bilateral_contact":True}),(.30,4.))
        self.assertEqual(limits("pear",{"target":"pear","phase":"carry"},{"bilateral_contact":False,"pads_separated":False}),(.40,4.))
        self.assertEqual(limits("pear",{"target":"pear","phase":"carry"},{"pads_separated":True}),(.30,4.))
        self.assertEqual(limits("banana",{"target":"banana","phase":"carry"},{"bilateral_contact":True}),(.30,6.))
        self.assertEqual(limits("banana",{"target":"banana","phase":"carry"},{"bilateral_contact":False}),(.30,4.))
        self.assertEqual(limits("banana",{"target":"banana","phase":"carry"},{"bilateral_contact":False},supported=True),(.30,6.))
        self.assertEqual(limits("burger",{"target":"burger_container","phase":"carry"},{"bilateral_contact":True}),(.30,4.))
        self.assertEqual(limits("microwave_door_handle",{"target":"banana","phase":"carry"},{"bilateral_contact":True}),(.30,4.))

    def test_exact_user_table_instances_and_namespace(self):
        rows=cases()
        self.assertEqual(len(rows),64)
        self.assertEqual(len({r["case_id"] for r in rows}),64)
        counts=Counter(row["task"] for row in rows)
        self.assertEqual({k:counts[k] for k in ("L1","L2","R1","R2","R3","R4","R5","B1")},
                         {"L1":4,"L2":4,"R1":2,"R2":2,"R3":3,"R4":2,"R5":2,"B1":1})
        self.assertEqual([counts["C%02d"%i] for i in range(1,9)],[4,4,2,2,8,8,8,8])
        self.assertTrue(all(r["namespace"]==NAMESPACE for r in rows))
        self.assertEqual(catalogue()["atomic_count"],20)
        self.assertEqual(catalogue()["composite_count"],44)

    def test_four_pad_gate_is_and_not_or_and_restricts_payload(self):
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            c={"arm":"both","target":"wood_tray","phase":"acquire","payload_ids":["wood_tray"],
               "supports":{"wood_tray":["user_table","v3_counter_right"]}}
            p.contact.activate(c)
            result=p.contact._read_grasp("both",p.contact._target_geoms)
            self.assertEqual(set(result["by_arm"]),{"left","right"})
            self.assertEqual(len(result["pad_normal_forces_n"]),4)
            self.assertFalse(result["bilateral_contact"])
            p.contact.clear()
            c["target"]="apple";c["payload_ids"]=["apple"]
            with self.assertRaisesRegex(ValueError,"DUAL_CONTACT_REQUIRES_EMPTY_TRAY_PAYLOAD"):
                p.contact.validate_contract(c)
        finally:p.close()

    def test_only_centered_isotropic_sphere_has_rotational_symmetry(self):
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            matrix=np.array([[[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]]])
            identity=np.eye(3)[None,:,:];origin=np.zeros((1,3))
            # ADR-004 item 1 exempts only a single centred sphere. Apple/tomato
            # now carry a user-directed cylindrical foot, so they are multi-geom
            # and the rotational equivalence no longer applies (the whole gate is
            # disabled in this phase anyway). Translation is still measured.
            for name,expected_zero in (("apple",False),("tomato",False),("pear",False)):
                c={"arm":"left","target":name,"phase":"acquire","payload_ids":[name],"supports":{name:["v3_counter_left","v3_counter_right"]}}
                p.contact.activate(c)
                distance,angle=p.contact._relative_errors(origin,matrix,origin,identity)
                self.assertEqual(distance,0.)
                self.assertAlmostEqual(angle,0. if expected_zero else np.pi/2)
                distance,_=p.contact._relative_errors(np.array([[.02,0,0]]),matrix,origin,identity)
                self.assertGreater(distance,.015)
                p.contact.clear()
        finally:p.close()

    def test_relative_motion_gate_disabled_but_re_enableable(self):
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            # The temporary user-authorized state is explicit, not an accident.
            self.assertFalse(contact_execution.GRASP_RELATIVE_GATE_ENABLED)
            self.assertTrue(p.contact._grasp_within_limits(.001,math.radians(120)))
            self.assertTrue(p.contact._grasp_within_limits(.02,0.))
            self.assertFalse(p.contact._grasp_within_limits(float("nan"),0.))
            with patch.object(contact_execution,"GRASP_RELATIVE_GATE_ENABLED",True):
                self.assertTrue(p.contact._grasp_within_limits(.001,math.radians(29)))
                self.assertFalse(p.contact._grasp_within_limits(.001,math.radians(31)))
                self.assertFalse(p.contact._grasp_within_limits(.02,0.))
        finally:p.close()

    def test_temporary_support_compliance_and_raised_hotdog_bar(self):
        from robot_arm.kitchen.registry import control_identity
        config0=control_identity()["configuration"]
        p=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0)
        try:
            self.assertEqual(contact_execution.SUPPORT_COMPLIANCE_M, .0015)
            self.assertEqual(contact_execution.ORDINARY_MARGIN_M, -.003)
            self.assertEqual(config0["ordinary_clearance_m"], -.003)
            self.assertEqual(config0["ordinary_clearance_gate"],"disabled_temporary_20261024")
            self.assertEqual(contact_execution.LOSS_GRACE_S, .100)
            self.assertEqual(config0["grasp_loss_grace_s"], .100)
            self.assertEqual(contact_execution.RELEASE_TRAVEL_M, .120)
            self.assertGreater(contact_execution.RELEASE_TRAVEL_M, contact_execution.LOCAL_TRAVEL_M)
            config=control_identity()["configuration"]
            self.assertEqual(config["support_compliance_gate"],"raised_temporary_20261024")
            self.assertEqual(config["support_compliance_m"],.0015)
            self.assertEqual(config["grasp_relative_gate"],"disabled_temporary_20261024")
            m=p.scene.model
            for side,sign in (("left",-1.),("right",1.)):
                geom=m.geom("hotdog_grasp_"+side)
                self.assertAlmostEqual(float(m.geom_pos[geom.id][0]),sign*.082,places=6)
                self.assertAlmostEqual(float(m.geom_pos[geom.id][2]),.060,places=6)
        finally:p.close()


if __name__=="__main__":unittest.main()
