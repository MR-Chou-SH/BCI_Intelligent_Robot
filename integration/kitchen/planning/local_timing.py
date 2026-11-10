"""Sparse semantic timing repairs for already planned action trajectories."""
import numpy as np
import json


def collision_pair(failed):
    error=failed.get('error','')
    if 'RELAXED_AUDIT' not in error or 'normal_force_n' not in error:return None
    try:
        item=json.loads(error[error.index('{'):error.rindex('}')+1])
        return tuple(sorted((item['geom1'],item['geom2'])))
    except (ValueError,KeyError):return None


def modes(context):
    return [e for e in context['changes'] if e['method']=='mode']


def window(data,start,end,factor,reason):
    a=max(data['start_tick'],int(start)//20*20)
    b=min(data['end_tick'],int(end)//20*20)
    return {'start_tick':a,'end_tick':b,'factor':factor,'reason':reason}


def base_caps(context):
    targets={e['args'].get('contact_contract',{}).get('target') for e in modes(context)}
    return {'microwave_start_geom':2.} if 'microwave_start_geom' in targets else {}


def repair_recipes(data,context,failed):
    """Bounded local alternatives; all coordinates remain on the source path."""
    changes=modes(context);recipes=[];caps=base_caps(context)
    wash_failure=any(name in failed.get('error','') for name in ('FRUIT_NOT_CLEANED','WASH_COMPLETION'))
    def add(name,windows=(),extra_caps=None):
        recipe={'name':name,'factor':4.,'caps':{**caps,**(extra_caps or {})},'windows':list(windows)}
        if wash_failure:recipe['wash_gate']=True
        recipes.append(recipe)
    if wash_failure:
        add('wash_completion_gate')
        add('faucet_contact_cap2',extra_caps={'faucet_handle_geom':2.})
    # A washed fruit is grasped again. Include a short free approach lead-in:
    # contact can begin before the declared acquire phase and affect the pinch.
    for index,event in enumerate(changes):
        c=event['args'].get('contact_contract',{});target=c.get('target')
        ids=data['metadata']['object_ids']
        if c.get('phase')!='acquire' or target not in ids:continue
        row=min(len(data['states/tick'])-1,int(np.searchsorted(data['states/tick'],event['tick'])))
        if not data['states/task_flags'][row,ids.index(target),0]:continue
        tick=failed.get('failure_source_tick',event['tick'])
        skill_start=max((e['tick'] for e in context['events'] if e['stage']=='skill_started' and e['tick']<=event['tick']),default=data['start_tick'])
        skill_end=min((e['tick'] for e in context['events'] if e['stage']=='skill_qualified' and e['tick']>=event['tick']),default=data['end_tick'])
        if tick is not None and not skill_start<=tick<=skill_end:continue
        carry=next((e for e in changes[index+1:] if e['args'].get('contact_contract',{}).get('target')==target
                    and e['args'].get('contact_contract',{}).get('phase')=='carry'),None)
        if carry is None:continue
        end=carry['tick']+500
        for lead,cap in ((2,3),(5,3),(2,2)):
            add(f'washed_regrasp_lead{lead}_cap{cap}',[window(data,event['tick']-lead*500,end,cap,'washed regrasp and initial lift')])
        # If an earlier placement changed the later grasp state, restore only
        # this semantic wash skill's prefix timing, keeping subsequent work fast.
        add('washed_prefix_cap3',[window(data,skill_start,end,3,'upstream wash/deposit and regrasp')])
        break
    losses=failed.get('grasp_losses',[])
    if losses:
        loss=losses[0]
        for cap in (3,2):
            add(f'grasp_loss_cap{cap}',[window(data,loss['carry_start_tick']-1000,loss['source_tick']+1000,cap,'measured grasp loss neighborhood')])
    error=failed.get('error','')
    if 'HEAT' in error and caps:add('button_cap1',extra_caps={'microwave_start_geom':1.})
    if 'door' in error.lower() or 'DOOR' in error:add('door_contact_cap2',extra_caps={'microwave_door_handle':2.})
    if 'TILT' in error and failed.get('tray_peak_source_tick') is not None:
        tick=failed['tray_peak_source_tick'];add('tray_turn_cap2',[window(data,tick-1000,tick+1000,2,'measured tray tilt peak')])
    if (not recipes or collision_pair(failed)) and failed.get('failure_source_tick') is not None:
        tick=failed['failure_source_tick'];add('failure_neighborhood_cap2',[window(data,tick-1000,tick+1000,2,'measured failure neighborhood')])
    return recipes
