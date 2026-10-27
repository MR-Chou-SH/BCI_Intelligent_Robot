import unittest
import numpy as np
from integration.kitchen.planning.curved_motion import bezier_positions,joint_spline,placement_tcp_goal


class CurvedMotionTests(unittest.TestCase):
    def test_placement_requeries_the_payload_after_its_pose_changes(self):
        import mujoco
        from robot_arm.kitchen.model import build_scene
        from robot_arm.kitchen.control import tcp_pose
        from robot_arm.kitchen.registry import object_bounds,geometry_bounds
        scene=build_scene(layout_id='kitchen_v3_norice_candidate_013',seed=0)
        body=scene.objects['banana'];joint=int(scene.model.body_jntadr[body]);adr=int(scene.model.jnt_qposadr[joint])
        original=placement_tcp_goal(scene,'left','banana','tray_bottom',[0.,-.4])
        scene.data.qpos[adr+2]+=.05
        scene.data.qpos[adr+3:adr+7]=[np.cos(.15),np.sin(.15),0.,0.]
        mujoco.mj_forward(scene.model,scene.data)
        goal=placement_tcp_goal(scene,'left','banana','tray_bottom',[0.,-.4])
        tcp,_=tcp_pose(scene.data,scene.arms['left']);root=scene.data.xpos[body]
        lo,_=object_bounds(scene,'banana')
        _,top=geometry_bounds(scene.model,scene.data,scene.model.geom('tray_bottom').id)
        delta=goal-tcp
        np.testing.assert_allclose((root+delta)[:2],[0.,-.4],atol=1e-12)
        self.assertAlmostEqual(lo[2]+delta[2],top[2]+.001,places=10)
        self.assertGreater(np.linalg.norm(goal-original),.01)

    def test_approach_translates_and_descends_before_vertical_grasp(self):
        start=np.array([0.,0.,1.1]);end=np.array([.4,.2,.85])
        points,join=bezier_positions(start,end,0.,'approach')
        np.testing.assert_allclose(points[0],start)
        np.testing.assert_allclose(points[-1],end)
        np.testing.assert_allclose(points[join:,:2],np.tile(end[:2],(len(points)-join,1)))
        self.assertTrue(np.any((points[:,0]>.05)&(points[:,0]<.35)&(points[:,2]<1.05)))
        self.assertGreater(np.max(np.abs(np.diff(points[:join,2]))),.0001)

    def test_carry_arc_is_not_a_horizontal_then_vertical_polyline(self):
        start=np.array([-.6,.1,.94]);end=np.array([0.,-.4,.88])
        points,_=bezier_positions(start,end,.18,'carry',bend=.7)
        self.assertGreater(points[:,2].max(),max(start[2],end[2])+.05)
        delta=np.diff(points,axis=0)
        self.assertGreater(np.sum((np.abs(delta[:,0])>.0001)&(np.abs(delta[:,2])>.0001)),10)

    def test_joint_interpolation_preserves_bounds_and_does_not_dwell_at_knots(self):
        knots=np.array([[0.,0.],[.08,.03],[.16,.08],[.24,.12]])
        values,parameters,s=joint_spline(knots,.008)
        np.testing.assert_allclose(values[-1],knots[-1])
        self.assertTrue(np.all(values>=knots.min(axis=0)-1e-12))
        self.assertTrue(np.all(values<=knots.max(axis=0)+1e-12))
        step=np.diff(np.vstack([knots[:1],values]),axis=0)
        self.assertLessEqual(np.max(np.abs(step)),.008+1e-6)
        for knot in s[1:-1]:
            i=np.searchsorted(parameters,knot)
            self.assertGreater(np.linalg.norm(step[i]),.002)

    def test_duplicate_reference_samples_do_not_create_artificial_holds(self):
        path=np.array([[0.],[0.],[.1],[.1],[.2],[.2]])
        a,_,_=joint_spline(path,.008)
        b,_,_=joint_spline(np.array([[0.],[.1],[.2]]),.008)
        np.testing.assert_allclose(a,b)


if __name__=='__main__':unittest.main()
