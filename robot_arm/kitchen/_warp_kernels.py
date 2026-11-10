"""Optional GPU control kernels; imported only by the Warp adapter."""
import warp as wp


@wp.kernel
def write_targets(previous:wp.array2d[float], accepted:wp.array2d[float],
                  bias:wp.array2d[float], actuators:wp.array[int], dofs:wp.array[int],
                  gains:wp.array[float], lower:wp.array[float], upper:wp.array[float],
                  alpha:float, ctrl:wp.array2d[float]):
    world,slot=wp.tid()
    value=previous[world,slot]+(accepted[world,slot]-previous[world,slot])*alpha
    if slot%8!=7:
        value+=bias[world,dofs[slot]]/gains[slot]
    ctrl[world,actuators[slot]]=wp.clamp(value,lower[slot],upper[slot])
