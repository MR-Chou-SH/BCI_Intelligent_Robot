"""Expand the user-frozen V1.1 task chains into dependency nodes, not claims."""
import json
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[2]
PATH=ROOT/"docs/kitchen/task_catalog_v1_1.json"

def load_catalog():
    data=json.loads(PATH.read_text(encoding="utf-8"))
    if len(data["atomic"])!=16 or len(data["composite"])!=12:raise ValueError("V1.1 count mismatch")
    return data

def expand(task_id):
    catalog=load_catalog()
    composites={x["id"]:x for x in catalog["composite"]}
    atomics={x["id"] for x in catalog["atomic"]}
    events=set(catalog["events"])
    nodes=[];counts={}
    def walk(item,dependencies,stack=()):
        if item in stack:raise ValueError("Recursive task definition")
        match=re.fullmatch(r"parallel\((C\d+),(C\d+)\)",item)
        if match:
            return walk(match[1],dependencies,stack)+walk(match[2],dependencies,stack)
        if item in composites:
            tail=list(dependencies)
            for part in composites[item]["chain"]:tail=walk(part,tail,stack+(item,))
            return tail
        if item not in atomics|events:raise ValueError("Unknown task/event "+item)
        counts[item]=counts.get(item,0)+1
        identity=item+":"+str(counts[item])
        role="old_meal" if task_id=="C12" and ((item=="R06" and counts[item]==1) or item=="R09") else "new_meal" if item.startswith("R") else "fruit" if item.startswith("L") else None
        nodes.append({"id":identity,"task":item,"kind":"event" if item in events else "atomic",
            "arm":"both" if item=="B01" else "left" if item.startswith("L") else "right" if item.startswith("R") else None,
            "requires":list(dependencies),"object_role":role,"status":"NOT_VERIFIED"})
        return [identity]
    walk(task_id,[])
    return nodes
