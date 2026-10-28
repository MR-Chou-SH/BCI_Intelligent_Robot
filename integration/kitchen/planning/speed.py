"""Planner timing profiles; physics, controller limits and task gates are unchanged."""
PROFILES=('standard','faster')


def polyline_clock(points, costs, frames=None, *, with_clock=False):
    """One clock across IK knots; interpolate only on the original joint edges.

    Costs are segment durations in control frames, combining joint, linear and
    angular rates. Repeated knots have no dwell. Only the route ends ease out.
    """
    import math
    import numpy as np
    points=np.asarray(points,dtype=float);costs=np.asarray(costs,dtype=float)
    original_knots=np.r_[0.,np.cumsum(costs)]
    keep=costs>1e-10
    if not np.any(keep):return (points[-1:],np.zeros(1),original_knots) if with_clock else points[-1:]
    points=np.vstack([points[0],points[1:][keep]])
    knots=np.r_[0.,np.cumsum(costs[keep])]
    alpha=.12;vmax=1/(1-alpha)
    count=max(8,math.ceil(knots[-1]*vmax)) if frames is None else frames
    t=np.arange(1,count+1)/count
    def ramp(x):return vmax*(x/2-alpha/(2*np.pi)*np.sin(np.pi*x/alpha))
    progress=np.where(t<alpha,ramp(t),np.where(t>1-alpha,1-ramp(1-t),vmax*(t-alpha/2)))
    s=np.clip(progress,0,1)*knots[-1]
    index=np.clip(np.searchsorted(knots,s,side='right')-1,0,len(knots)-2)
    u=(s-knots[index])/(knots[index+1]-knots[index])
    values=points[index]+u[:,None]*(points[index+1]-points[index]);values[-1]=points[-1]
    return (values,s,original_knots) if with_clock else values


def speed_factor(profile,contract=None):
    if profile not in PROFILES:raise ValueError('Unknown motion profile: '+str(profile))
    if profile=='standard':return 1.
    phase=(contract or {}).get('phase')
    if phase in ('acquire','release','device'):return 1.
    return 1.25 if phase=='carry' else 1.4
