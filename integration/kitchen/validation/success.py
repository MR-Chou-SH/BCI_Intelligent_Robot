"""Task success predicates shared by online execution and offline replay."""
import math


def validate_skill(quality,skill,case):
    dev=quality.platform.controller.devices
    fruit,meal=case.get('fruit'),case.get('meal')
    if skill in ('L1','L2'):
        quality.tray_object(fruit)
        if skill=='L2' and fruit not in dev.cleaned:raise ValueError('FRUIT_NOT_CLEANED')
    elif skill=='R1':
        if not dev.meal_ready(meal+'_container'):raise ValueError('MEAL_NOT_IN_OVEN')
        quality.require('door:closed',.5)
    elif skill=='R2':
        quality.tray_object(meal+'_container');quality.require('door:closed',.5)
    elif skill=='R3':quality.require('door:open',.5)
    elif skill=='R4':
        quality.require('door:open',.5)
        if meal+'_container' not in dev.heated:raise ValueError('MEAL_NOT_HEATED')
    elif skill=='R5':quality.tray_object(meal+'_container')
    elif skill=='B1':
        quality.require('tray:delivered',2.)
        if quality.tray_tilt_peak>math.radians(10):raise ValueError('TRAY_TILT_LIMIT')
    else:raise ValueError('UNKNOWN_SKILL')


def validate_final(quality,case):
    task=case['task'];dev=quality.platform.controller.devices
    if case.get('fruit'):quality.tray_object(case['fruit'])
    if case.get('meal') and (task in ('R2','R5') or task.startswith('C')):
        quality.tray_object(case['meal']+'_container')
    if task in ('C04','C07','C08') and case['meal']+'_container' not in dev.heated:
        raise ValueError('COMPOSITE_HEATED_MEAL_MARKER_LOST')
    if quality.failures:raise ValueError('CONTROLLER_STOPPED')


def validate_segments(case,segments):
    """Reject missing/foreign skill labels instead of trusting a saved PASS."""
    from integration.kitchen.tasks.catalog import ATOMIC,COMPOSITE
    names=[s['skill'] for s in segments];task=case['task']
    if task in ATOMIC:expected=[task]
    else:
        expected=list(COMPOSITE[task][1])
        if 'heat_meal_state_dependent' in expected:
            prefix=expected[:-1]
            if names[:len(prefix)]!=prefix:raise ValueError('COMPOSITE_PREFIX_MISMATCH')
            heat=names[len(prefix):]
            # A legal state-dependent heating chain can skip satisfied stages.
            allowed=[['R1','R4','R2'],['R3','R1','R4','R2'],['R4','R2'],['R3','R2'],['R2']]
            if heat not in allowed:raise ValueError('INCOMPLETE_HEATING_CHAIN')
            return
    if names!=expected:raise ValueError('TASK_SKILL_CHAIN_MISMATCH')


def validate_initial(controller,case):
    """Check atomic-task prerequisites from the restored simulator, not labels."""
    dev=controller.devices;task=case['task'];angle=dev.angle('microwave_hinge')
    meal=case.get('meal');ready=bool(meal and dev.meal_ready(meal+'_container'))
    if task in ('R1','R2') and angle<math.radians(70):raise ValueError('INITIAL_DOOR_NOT_OPEN')
    if task=='R1' and ready:raise ValueError('INITIAL_MEAL_ALREADY_IN_OVEN')
    if task=='R2' and not ready:raise ValueError('INITIAL_MEAL_NOT_IN_OVEN')
    if task in ('R3','R4') and angle>math.radians(5):raise ValueError('INITIAL_DOOR_NOT_CLOSED')
    if task=='R3':
        if case['initial_occupancy']=='empty':
            if dev.meal_ready('burger_container') or dev.meal_ready('hotdog_container'):
                raise ValueError('INITIAL_OVEN_NOT_EMPTY')
        elif not ready:raise ValueError('INITIAL_OVEN_OCCUPANCY_MISMATCH')
    if task=='R4' and not ready:raise ValueError('INITIAL_MEAL_NOT_IN_OVEN')


def validate_timeline(case,segments,start,end):
    from integration.kitchen.tasks.catalog import cases
    if case not in cases():raise ValueError('UNKNOWN_TASK_DEFINITION')
    validate_segments(case,segments)
    previous=start
    for segment in segments:
        a,b=segment.get('start_tick'),segment.get('end_tick')
        if (type(a) is not int or type(b) is not int or not previous<=a<b<=end
            or a%20 or b%20):raise ValueError('INVALID_SKILL_TIMELINE')
        previous=b
