"""FR3 conservative rectangular joint limits intersect the actual model.

Source: https://support.franka.de/docs/control_parameters.html
section 'Limits for Franka Research 3', suggested rectangular limits.
Retrieved 2026-10-19. This is a simulator control domain, not hardware certification.
"""
import numpy as np
LOWER=np.array([-2.3093,-1.5133,-2.4937,-2.7478,-2.4800,.8521,-2.6895])
UPPER=np.array([2.3093,1.5133,2.4937,-.4461,2.4800,4.2094,2.6895])

def joint_control_limits(model,arm):
    lo=np.maximum(model.jnt_range[list(arm.joints),0]+.005,LOWER)
    hi=np.minimum(model.jnt_range[list(arm.joints),1]-.005,UPPER)
    if np.any(lo>=hi):raise ValueError("Empty FR3 safe control domain")
    return lo,hi
