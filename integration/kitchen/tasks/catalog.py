"""Deduplicated Expert V3: 20 atomic + 80 composite instances, including delivery."""
from itertools import product
import hashlib
import json
from .legacy_catalog import ATOMIC,FRUITS,MEALS

NAMESPACE='kitchen_expert_v3'
HEAT='heat_meal_state_dependent'
COMPOSITE={
    'C04':('主食加热装盘',(HEAT,)),
    'C05':('水果与主食直接装盘',('L1','R5')),
    'C06':('清洗水果与主食直接装盘',('L2','R5')),
    'C07':('水果直接装盘与加热主食',('L1',HEAT)),
    'C08':('清洗水果与加热主食',('L2',HEAT)),
    'D01':('水果直接装盘并送餐',('L1','B1')),
    'D02':('清洗水果装盘并送餐',('L2','B1')),
    'D03':('从炉内取餐装盘并送餐',('R2','B1')),
    'D04':('主食直接装盘并送餐',('R5','B1')),
    'D05':('主食加热装盘并送餐',(HEAT,'B1')),
    'D06':('水果与主食直接装盘并送餐',('L1','R5','B1')),
    'D07':('清洗水果与主食直接装盘并送餐',('L2','R5','B1')),
    'D08':('水果与加热主食装盘并送餐',('L1',HEAT,'B1')),
    'D09':('清洗水果与加热主食装盘并送餐',('L2',HEAT,'B1')),
}


def definition(case):
    """Historical labels retain their original meaning during replay."""
    if case['namespace']=='kitchen_expert_v2':
        from . import legacy_catalog
        return legacy_catalog.ATOMIC if case['task'] in legacy_catalog.ATOMIC else legacy_catalog.COMPOSITE
    if case['namespace']!=NAMESPACE:raise ValueError('UNKNOWN_TASK_NAMESPACE')
    return ATOMIC if case['task'] in ATOMIC else COMPOSITE


def chain(case):return definition(case)[case['task']][1] if case['task'] not in ATOMIC else (case['task'],)


def cases(suite='all'):
    if suite not in ('atomic','composite','all'):raise ValueError('Unknown suite')
    result=[]
    def add(task,fruit=None,meal=None,occupancy=None):
        description,skills=(ATOMIC if task in ATOMIC else COMPOSITE)[task]
        row={'namespace':NAMESPACE,'task':task,'fruit':fruit,'meal':meal,'initial_occupancy':occupancy,
             'instruction':description,'skills':list(skills),'execution_order':'sequential','task_success':'NOT_VERIFIED'}
        parts=[NAMESPACE,task]
        if fruit:parts.append(fruit)
        if meal:parts.append(meal)
        if occupancy=='empty':parts.append('empty')
        row['case_id']=':'.join(parts);result.append(row)
    if suite in ('atomic','all'):
        for task in ATOMIC:
            if task.startswith('L'):
                for fruit in FRUITS:add(task,fruit=fruit)
            elif task=='R3':
                for occupancy in ('empty',*MEALS):add(task,meal=None if occupancy=='empty' else occupancy,occupancy=occupancy)
            elif task=='B1':add(task)
            else:
                for meal in MEALS:add(task,meal=meal)
    if suite in ('composite','all'):
        for task in COMPOSITE:
            if task in ('D01','D02'):
                for fruit in FRUITS:add(task,fruit=fruit)
            elif task in ('C04','D03','D04','D05'):
                for meal in MEALS:add(task,meal=meal)
            else:
                for fruit,meal in product(FRUITS,MEALS):add(task,fruit=fruit,meal=meal)
    return result


def known_cases(namespace):
    if namespace==NAMESPACE:return cases()
    if namespace=='kitchen_expert_v2':
        from .legacy_catalog import cases as legacy_cases
        return legacy_cases()
    raise ValueError('UNKNOWN_TASK_NAMESPACE')


def catalogue():
    data={'namespace':NAMESPACE,'atomic_count':len(cases('atomic')),'composite_count':len(cases('composite')),
          'cases':cases(),'definition':'deduplicated 2026-10-27; C01-C03 removed; D01-D09 explicitly append B1',
          'legacy_catalogue_modified':False,'images_included':False}
    data['sha256']=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return data
