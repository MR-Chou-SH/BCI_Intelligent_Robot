"""Canonical English commands shared by atomic and composite VLA labels."""
from .catalog import chain,HEAT

SCHEMA='kitchen_language_annotations_v1'
OBJECT_NAMES={'apple':'apple','pear':'pear','banana':'banana','tomato':'tomato',
              'burger':'hamburger','hotdog':'hot dog'}
TEMPLATES={
 'L1':'Place the {fruit} directly on the serving tray.',
 'L2':'Wash the {fruit} and place it on the serving tray.',
 'R1':'Put the {food} in the microwave and close the door.',
 'R2':'Take the {food} out of the microwave, place it on the serving tray, and close the door.',
 'R3':'Open the microwave door.',
 'R4':'Heat the {food} in the microwave, wait until it is ready, and open the door.',
 'R5':'Place the {food} directly on the serving tray.',
 'B1':'Deliver the serving tray to the user with both arms.',
 HEAT:'Heat the {food} and place it on the serving tray.',
}

def instruction(skill,case):
    template=TEMPLATES[skill]
    values={}
    for slot,key in [('fruit','fruit'),('food','meal')]:
        if '{'+slot+'}' in template:
            target=case.get(key)
            if target not in OBJECT_NAMES:raise ValueError('MISSING_LANGUAGE_TARGET: '+slot)
            values[slot]=OBJECT_NAMES[target]
    return template.format(**values)

def coarse_instruction(case):
    commands=[instruction(s,case) for s in chain(case)]
    result=[commands[0]]
    for i,command in enumerate(commands[1:],1):
        prefix='Finally, ' if i==len(commands)-1 and len(commands)>2 else 'Then, '
        result.append(prefix+command[0].lower()+command[1:])
    return ' '.join(result)
