"""Policy actions: 14 nominal joint targets (rad), two opening ratios (0..1).

Opening is a control setpoint, not the measured mesh gap: 0 requests full
closure, 1 full opening. Real gap in metres is recorded separately in state.
"""
import numpy as np


def encode(target):
    target=np.asarray(target,dtype='f8')
    return target[[0,1,2,3,4,5,6,8,9,10,11,12,13,14]].copy(),1.-target[[7,15]]/.05


def decode(joint_pos,gripper_opening):
    result=np.empty(16,dtype='f8')
    result[[0,1,2,3,4,5,6,8,9,10,11,12,13,14]]=joint_pos
    result[[7,15]]=.05*(1.-np.asarray(gripper_opening))
    return result
