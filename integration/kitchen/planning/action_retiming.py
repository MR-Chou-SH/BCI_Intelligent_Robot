"""Experimental action-only retiming; preserve grips, waits and event boundaries."""
import math
import numpy as np

ARM_COLUMNS=np.array([0,1,2,3,4,5,6,8,9,10,11,12,13,14])
MAX_FACTOR=5.


def retime(commands, initial_reference, cuts, modes, factor, prepare, *, continuous=False,carry_pause_clear=None):
    """Return bounded commands and an exact old/new frame-boundary map.

    Space remains on the original joint polyline. Gripper-changing frames,
    exact target holds and device phases keep their original duration. Only
    other moving runs are resampled. Existing controller limits can insert
    shorter progress steps; they are never raised or bypassed.
    """
    commands=np.asarray(commands,dtype=float)
    initial_reference=np.asarray(initial_reference,dtype=float)
    if (commands.ndim!=2 or commands.shape[1]!=16 or not len(commands)
        or initial_reference.shape!=(16,) or not np.isfinite(commands).all()
        or not np.isfinite(initial_reference).all() or not 1<=factor<=MAX_FACTOR):
        raise ValueError('Invalid action retiming input')
    n=len(commands);cuts=set(cuts)|{0,n}
    if any(type(c) is not int or not 0<=c<=n for c in cuts):raise ValueError('Invalid frame boundary')
    if not modes or modes[0][0]!=0 or any(a[0]>=b[0] for a,b in zip(modes,modes[1:])):
        raise ValueError('Missing or unordered action modes')
    cuts.update(int(t) for t,_ in modes)
    before=np.vstack([initial_reference,commands[:-1]])
    moving=np.max(np.abs(commands[:,ARM_COLUMNS]-before[:,ARM_COLUMNS]),axis=1)>1e-10
    gripping=np.max(np.abs(commands[:,[7,15]]-before[:,[7,15]]),axis=1)>1e-12
    kinds=np.where(gripping,2,np.where(moving,1,0))
    bridged_carry_frames=0
    if continuous:
        # Only bridge short interior pauses with unchanged grippers, within
        # the same explicit phase. Long device/settling waits remain intact.
        boundaries=sorted(cuts)
        for left,right in zip(boundaries,boundaries[1:]):
            mode=next(value for tick,value in reversed(modes) if tick<=left)
            i=left
            while i<right:
                if kinds[i]!=0:i+=1;continue
                j=i+1
                while j<right and kinds[j]==0:j+=1
                if i>left and j<right and kinds[i-1]==kinds[j]==1:
                    if j-i<=10:kinds[i:j]=1
                    elif (j-i<=50 and (mode.get('contact_contract') or {}).get('phase')=='carry'
                          and carry_pause_clear is not None and carry_pause_clear(i,j,mode)):
                        kinds[i:j]=1;bridged_carry_frames+=j-i
                i=j
    cuts.update((np.flatnonzero(kinds[1:]!=kinds[:-1])+1).tolist())
    cuts=sorted(cuts);extended=np.vstack([initial_reference,commands])
    output=[];mapping={0:0};mode_index=0;previous=initial_reference.copy()
    protected_frames=0;limited_frames=0
    def at(position):
        if position>=n:return extended[-1].copy()
        index=min(int(math.floor(position)),n-1);u=position-index
        # Delta form preserves a stationary joint exactly at a hard limit;
        # weighted sums can round one ULP outside an otherwise legal bound.
        return extended[index]+(extended[index+1]-extended[index])*u
    for a,b in zip(cuts,cuts[1:]):
        while mode_index+1<len(modes) and modes[mode_index+1][0]<=a:mode_index+=1
        mode=modes[mode_index][1]
        contract=mode.get('contact_contract') or {}
        protected=(kinds[a]!=1 or mode.get('guarded_gripper',False)
                   or contract.get('phase')=='device' and not
                   (continuous and contract.get('target')=='faucet_handle_geom'))
        if protected:protected_frames+=b-a
        # Advance the source clock as far as the limiter permits in ONE output
        # frame. Finishing every clamped sample with an extra tiny remainder
        # frame would introduce speed dips and can make a "faster" route longer.
        # Arc-length clock removes internal slow-downs/settling at old IK
        # knots, including constant-path faucet rotation. No geometry change.
        arc=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(extended[a:b+1,ARM_COLUMNS],axis=0),axis=1))]
        use_arc=continuous and not protected and arc[-1]>1e-10
        def forward(position):
            desired=min(arc[-1],np.interp(position,np.arange(a,b+1),arc)+arc[-1]*factor/(b-a))
            return float(np.interp(desired,arc,np.arange(a,b+1)))
        position=float(a);budget=5*(b-a)+10
        while position<b-1e-10:
            budget-=1
            if budget<0:raise ValueError('Retiming limiter budget exhausted')
            target=forward(position) if use_arc else min(float(b),position+(1. if protected else factor))
            if b-target<1e-10:target=float(b)
            goal=at(target)
            accepted=np.asarray(prepare(goal,previous),dtype=float)
            if accepted.shape!=(16,) or not np.isfinite(accepted).all():raise ValueError('Invalid rate-limited action')
            if np.max(np.abs(accepted-goal))>1e-9:
                if protected:raise ValueError('Protected action unexpectedly requires rate limiting')
                limited_frames+=1;lo=position;hi=target
                for _ in range(24):
                    mid=(lo+hi)/2;probe=at(mid)
                    if np.max(np.abs(prepare(probe,previous)-probe))<=1e-10:lo=mid
                    else:hi=mid
                target=lo;goal=at(target)
                if target-position<1e-8:raise ValueError('Retiming limiter stalled')
            output.append(goal.copy());previous=goal.copy();position=target
        mapping[b]=len(output)
    return np.asarray(output),mapping,{'source_frames':n,'candidate_frames':len(output),
        'protected_source_frames':protected_frames,'limiter_constrained_frames':limited_frames,
        'bridged_clear_carry_pause_frames':bridged_carry_frames}
