"""Frozen Expert V2 definitions for interpreting historical recordings only."""
from itertools import product
import hashlib
import json

NAMESPACE = "kitchen_expert_v2"
FRUITS = ("apple", "pear", "banana", "tomato")
MEALS = ("burger", "hotdog")
ATOMIC = {
    "L1": ("抓水果直接放餐盘", ("fruit_to_tray",)),
    "L2": ("抓水果→清洗→放餐盘", ("wash_fruit_to_tray",)),
    "R1": ("抓主食→放微波炉→关门", ("load_then_close",)),
    "R2": ("从微波炉取主食→放餐盘→关门", ("unload_to_tray_then_close",)),
    "R3": ("微波炉关闭→直接开门", ("open_door",)),
    "R4": ("启动加热→等待→开门", ("heat_then_open",)),
    "R5": ("抓主食直接放餐盘", ("meal_to_tray",)),
    "B1": ("双臂协作递送空托盘", ("deliver_tray",)),
}
COMPOSITE = {
    "C01": ("指定水果直接装盘", ("L1",)),
    "C02": ("清洗指定水果并装盘", ("L2",)),
    "C03": ("指定主食直接装盘", ("R5",)),
    "C04": ("加热指定主食并装盘", ("heat_meal_state_dependent",)),
    "C05": ("水果直接装盘＋主食直接装盘", ("L1", "R5")),
    "C06": ("清洗水果＋主食直接装盘", ("L2", "R5")),
    "C07": ("水果直接装盘＋加热主食", ("L1", "heat_meal_state_dependent")),
    "C08": ("清洗水果＋加热主食", ("L2", "heat_meal_state_dependent")),
}


def cases(suite="all"):
    if suite not in ("atomic", "composite", "all"):
        raise ValueError("Unknown suite")
    result = []
    def add(task, fruit=None, meal=None, occupancy=None):
        description, skills = (ATOMIC if task in ATOMIC else COMPOSITE)[task]
        row = {"namespace": NAMESPACE, "task": task, "fruit": fruit, "meal": meal,
               "initial_occupancy": occupancy, "instruction": description,
               "skills": list(skills), "execution_order": "sequential",
               "task_success": "NOT_VERIFIED"}
        row["case_id"] = ":".join(str(x) for x in (NAMESPACE, task, fruit, meal, occupancy) if x is not None)
        result.append(row)
    if suite in ("atomic", "all"):
        for task in ATOMIC:
            if task.startswith("L"):
                for fruit in FRUITS: add(task, fruit=fruit)
            elif task == "R3":
                for occupancy in ("empty", *MEALS): add(task, meal=occupancy if occupancy != "empty" else None, occupancy=occupancy)
            elif task == "B1": add(task)
            else:
                for meal in MEALS: add(task, meal=meal)
    if suite in ("composite", "all"):
        for task in COMPOSITE:
            if task in ("C01", "C02"):
                for fruit in FRUITS: add(task, fruit=fruit)
            elif task in ("C03", "C04"):
                for meal in MEALS: add(task, meal=meal)
            else:
                for fruit, meal in product(FRUITS, MEALS): add(task, fruit=fruit, meal=meal)
    return result


def catalogue():
    data = {"namespace": NAMESPACE, "atomic_count": len(cases("atomic")),
            "composite_count": len(cases("composite")), "cases": cases(),
            "definition": "user tables 2026-10-24; + runs sequentially, no implicit B1 delivery",
            "legacy_catalogue_modified": False, "images_included": False}
    data["sha256"] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                               separators=(",", ":")).encode()).hexdigest()
    return data
