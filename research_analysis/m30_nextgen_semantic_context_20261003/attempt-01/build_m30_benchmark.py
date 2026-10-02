"""Build and freeze the independently authored M30 sequential benchmark."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from integration.semantic_context_relations import relation_compatibility


OUT = Path(__file__).resolve().parent
BENCHMARK = OUT / "semantic_context_sequence_benchmark_v2.json"
LOCK = OUT / "semantic_context_sequence_benchmark_lock.json"


def obj(code: str, zh: str, en: str, tags: tuple[str, ...] = (), *, object_type: str | None = None,
        state: dict[str, Any] | None = None, aliases: tuple[str, ...] = (), color: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": code,
        "object_type": object_type or en,
        "name": en,
        "name_zh": zh,
        "aliases": list(aliases),
        "affordance_tags": list(tags),
        "selectable": True,
    }
    if state is not None:
        row["current_state"] = dict(state)
    if color is not None:
        row["color"] = color
        row["observed_facts"] = {"color_source": "explicit_scene_text"}
    return row


def make_template(
    slug: str,
    family: str,
    split: str,
    scene_type: str,
    task_type: str,
    objects: list[dict[str, Any]],
    sequence: list[str],
    relations: list[str],
    *,
    ambiguous: dict[int, list[str]] | None = None,
    completed_round: int | None = None,
    invalid_round: int | None = None,
    task_state_overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prefix = slug + "__"
    cloned = []
    old_to_new = {}
    for item in objects:
        new_id = prefix + item["id"]
        old_to_new[item["id"]] = new_id
        copy = dict(item)
        copy["id"] = new_id
        cloned.append(copy)
    if len(cloned) != 8:
        raise ValueError(f"{slug} must have exactly eight scene objects")
    path = [old_to_new[item] for item in sequence]
    if not 2 <= len(path) <= 5 or len(relations) != len(path) - 1:
        raise ValueError(f"{slug} needs a two-to-five-object path and one relation label per decision")
    points = []
    for round_index in range(1, len(path)):
        history = path[:round_index]
        expected = path[round_index]
        acceptable = [expected]
        expected_status = "informative"
        acceptable_relations: dict[str, list[str]] = {expected: [relations[round_index - 1]]}
        if ambiguous and round_index in ambiguous:
            for alt in ambiguous[round_index]:
                alternative = old_to_new[alt]
                if alternative not in acceptable:
                    acceptable.append(alternative)
                    acceptable_relations[alternative] = ["RELATED_TO", "OTHER"]
            expected_status = "ambiguous"
        if completed_round == round_index:
            acceptable = []
            acceptable_relations = {}
            expected_status = "context_off"
        if invalid_round == round_index:
            acceptable = []
            acceptable_relations = {}
            expected_status = "invalid"
        if expected_status == "ambiguous" and len(acceptable) < 2:
            raise ValueError(f"{slug} round {round_index} is marked ambiguous without two distinct targets")
        task_state = {"phase": "in_progress", "task_type": task_type}
        if completed_round == round_index:
            task_state["phase"] = "complete"
        if task_state_overrides and round_index in task_state_overrides:
            task_state.update(task_state_overrides[round_index])
        remaining = [item["id"] for item in cloned if item["id"] not in set(history)]
        scene = {
            "schema_version": "m27-semantic-scene-v1",
            "scene_id": "scene_" + slug,
            "scene_type": scene_type,
            "candidate_order": [item["id"] for item in cloned],
            "objects": cloned,
        }
        points.append({
            "round_index": round_index,
            "model_input": {
                "scene": scene,
                "selection_history": history,
                "current_task_state": task_state,
                "candidate_next_object_ids": remaining,
            },
            "evaluation_only": {
                "acceptable_next_targets": acceptable,
                "acceptable_relations_by_target": acceptable_relations,
                "expected_status": expected_status,
                "label_source": "independently_authored_semantic_task_template",
            },
        })
    return {
        "episode_id": slug,
        "scene_family": family,
        "split": split,
        "trajectory_object_ids_evaluation_only": path,
        "decision_points": points,
    }


TEMPLATES: list[dict[str, Any]] = []


def add(*args, **kwargs):
    TEMPLATES.append(make_template(*args, **kwargs))


# TRAIN families: desktop/office and storage/organization (14 episodes).
add("office_phone_charge", "desktop_office", "train", "home_office", "charge_a_phone", [
    obj("phone", "手机", "phone", ("chargeable", "movable")), obj("charger", "无线充电座", "charger", ("charging_target",)),
    obj("cable", "充电线", "cable", ("power_cable", "movable")), obj("outlet", "插座", "power_outlet", ("power_source",)),
    obj("stand", "手机支架", "phone_stand", ("support_surface",)), obj("case", "手机壳", "phone_case", ("protective_case",)),
    obj("notebook", "笔记本", "notebook", ("support_surface",)), obj("drawer", "抽屉", "drawer", ("container", "openable")),
], ["phone", "charger", "cable", "outlet", "stand"], ["CHARGE_WITH", "USE_WITH", "USE_WITH", "PLACE_ON"])

add("office_book_shelve", "desktop_office", "train", "study", "organize_a_reading_desk", [
    obj("book", "书", "book", ("movable",)), obj("shelf", "书架", "bookshelf", ("support_surface", "storage_surface")),
    obj("folder", "文件夹", "folder", ("container",)), obj("tray", "文件托盘", "tray", ("support_surface",)),
    obj("drawer", "书桌抽屉", "drawer", ("container", "openable")), obj("lamp", "台灯", "lamp", ("support_surface",)),
    obj("pen", "笔", "pen", ("movable",)), obj("bin", "废纸篓", "waste_bin", ("container",)),
], ["book", "shelf", "folder", "tray", "drawer"], ["STORE_ON", "STORE_IN", "PLACE_ON", "STORE_IN"], ambiguous={4: ["lamp"]})

add("office_pen_station", "desktop_office", "train", "home_office", "organize_writing_supplies", [
    obj("pen", "蓝色签字笔", "pen", ("movable",), color="blue"), obj("holder", "笔筒", "pen_holder", ("container",)),
    obj("notebook", "便签本", "notebook", ("support_surface",)), obj("tray", "桌面托盘", "tray", ("support_surface",)),
    obj("drawer", "桌面抽屉", "drawer", ("container", "openable")), obj("phone", "手机", "phone", ("chargeable",)),
    obj("mug", "马克杯", "mug", ("container",)), obj("bin", "垃圾桶", "waste_bin", ("container",)),
], ["pen", "holder", "notebook", "tray", "drawer"], ["STORE_IN", "PLACE_ON", "STORE_ON", "STORE_IN"])

add("office_laptop_dock", "desktop_office", "train", "computer_desk", "prepare_a_laptop_desk", [
    obj("laptop", "笔记本电脑", "laptop", ("chargeable", "movable")), obj("dock", "扩展坞", "computer_dock", ("charging_target", "support_surface")),
    obj("adapter", "电源适配器", "power_adapter", ("power_source",)), obj("outlet", "墙上插座", "power_outlet", ("power_source",)),
    obj("mat", "桌垫", "desk_mat", ("support_surface",)), obj("mouse", "鼠标", "computer_mouse", ("movable",)),
    obj("drawer", "抽屉", "drawer", ("container", "openable")), obj("notebook", "记事本", "notebook", ("support_surface",)),
], ["laptop", "dock", "adapter", "outlet", "mat"], ["PLACE_ON", "USE_WITH", "USE_WITH", "PLACE_ON"])

add("office_audio_storage", "desktop_office", "train", "home_office", "put_away_headphones", [
    obj("headphones", "耳机", "headphones", ("movable",)), obj("case", "耳机盒", "case", ("container",)),
    obj("drawer", "书桌抽屉", "drawer", ("container", "openable")), obj("shelf", "书架", "bookshelf", ("support_surface",)),
    obj("phone", "手机", "phone", ("chargeable",)), obj("charger", "充电座", "charger", ("charging_target",)),
    obj("book", "书", "book", ("movable",)), obj("tray", "托盘", "tray", ("support_surface",)),
], ["headphones", "case", "drawer", "shelf", "tray"], ["PLACE_IN", "STORE_IN", "STORE_ON", "PLACE_ON"], ambiguous={1: ["drawer"]})

add("office_documents", "desktop_office", "train", "study", "file_documents", [
    obj("document", "文件", "document", ("movable",)), obj("folder", "文件夹", "folder", ("container",)),
    obj("drawer", "文件抽屉", "drawer", ("container", "openable")), obj("cabinet", "文件柜", "cabinet", ("container", "openable")),
    obj("tray", "桌面托盘", "tray", ("support_surface",)), obj("pen", "钢笔", "pen", ("movable",)),
    obj("notebook", "本子", "notebook", ("support_surface",)), obj("bin", "回收纸篓", "waste_bin", ("container",)),
], ["document", "folder", "drawer", "cabinet", "tray"], ["STORE_IN", "STORE_IN", "STORE_IN", "PLACE_ON"], ambiguous={3: ["tray"]})

add("office_remote", "desktop_office", "train", "living_room_desk", "put_away_a_remote", [
    obj("remote", "电视遥控器", "remote_control", ("movable",)), obj("stand", "电视柜", "tv_stand", ("support_surface",)),
    obj("drawer", "电视柜抽屉", "drawer", ("container", "openable")), obj("table", "边桌", "side_table", ("support_surface",)),
    obj("battery", "备用电池", "battery", ("power_source",)), obj("box", "遥控器收纳盒", "storage_box", ("container",)),
    obj("basket", "收纳篮", "basket", ("container",)), obj("mug", "水杯", "mug", ("container",)),
], ["remote", "stand", "drawer", "table", "basket"], ["PLACE_ON", "STORE_IN", "PLACE_ON", "STORE_IN"], ambiguous={4: ["box"]})

add("storage_medicine", "storage_organization", "train", "home_storage", "store_medicine_safely", [
    obj("medicine", "小药盒", "medicine_box", ("movable",)), obj("box", "收纳盒", "storage_box", ("container", "openable")),
    obj("cabinet", "储物柜", "cabinet", ("container", "openable")), obj("user_zone", "用户区域", "user_zone", ("user_zone", "support_surface")),
    obj("shelf", "置物架", "shelf", ("support_surface",)), obj("cup", "水杯", "cup", ("container",)),
    obj("book", "说明书", "booklet", ("movable",)), obj("bin", "废纸篓", "waste_bin", ("container",)),
], ["medicine", "box", "cabinet", "user_zone", "shelf"], ["STORE_IN", "STORE_IN", "HAND_OVER", "STORE_ON"])

add("storage_toys", "storage_organization", "train", "playroom", "organize_toys", [
    obj("toy", "玩具", "toy", ("movable",)), obj("bin", "玩具收纳箱", "storage_bin", ("container",)),
    obj("shelf", "玩具架", "shelf", ("support_surface",)), obj("cabinet", "柜子", "cabinet", ("container", "openable")),
    obj("tray", "游戏托盘", "tray", ("support_surface",)), obj("book", "绘本", "book", ("movable",)),
    obj("basket", "玩具篮", "basket", ("container",)), obj("table", "游戏桌", "table", ("support_surface",)),
], ["toy", "bin", "shelf", "cabinet", "tray"], ["STORE_IN", "STORE_ON", "STORE_IN", "PLACE_ON"], ambiguous={1: ["basket"]})

add("storage_cables", "storage_organization", "train", "home_office", "organize_cables", [
    obj("cable", "数据线", "cable", ("movable",)), obj("organizer", "理线盒", "cable_organizer", ("container",)),
    obj("drawer", "桌面抽屉", "drawer", ("container", "openable")), obj("desk", "书桌", "desk", ("support_surface",)),
    obj("tray", "工具托盘", "tray", ("support_surface",)), obj("charger", "充电头", "charger", ("charging_target",)),
    obj("phone", "手机", "phone", ("chargeable",)), obj("box", "备用盒", "box", ("container",)),
], ["cable", "organizer", "drawer", "desk", "tray"], ["STORE_IN", "STORE_IN", "PLACE_ON", "STORE_ON"])

add("storage_plates", "storage_organization", "train", "dining_area", "put_away_dishes", [
    obj("plate", "白色盘子", "plate", ("dish", "movable"), color="white"), obj("rack", "沥水架", "dish_rack", ("support_surface",)),
    obj("cabinet", "餐具柜", "cabinet", ("container", "openable")), obj("counter", "料理台", "counter", ("support_surface",)),
    obj("sink", "水槽", "sink", ("sink", "water_source")), obj("cup", "玻璃杯", "cup", ("container",)),
    obj("towel", "厨房毛巾", "towel", ("movable",)), obj("bin", "厨余桶", "waste_bin", ("container",)),
], ["plate", "rack", "cabinet", "counter", "sink"], ["STORE_ON", "STORE_IN", "PLACE_ON", "RINSE_WITH"], ambiguous={2: ["counter"]})

add("storage_clothes", "storage_organization", "train", "bedroom", "organize_clothing", [
    obj("sweater", "毛衣", "sweater", ("movable", "washable")), obj("closet", "衣柜", "closet", ("container", "openable")),
    obj("hanger", "衣架", "hanger", ("support_surface",)), obj("dresser", "五斗柜", "dresser", ("container",)),
    obj("basket", "洗衣篮", "laundry_basket", ("container",)), obj("shirt", "衬衫", "shirt", ("washable",)),
    obj("shelf", "衣柜隔板", "shelf", ("support_surface",)), obj("bin", "收纳箱", "storage_bin", ("container",)),
], ["sweater", "closet", "hanger", "dresser", "basket"], ["STORE_IN", "STORE_ON", "STORE_IN", "STORE_IN"], ambiguous={1: ["hanger"]})

add("storage_tools", "storage_organization", "train", "garage", "put_away_hand_tools", [
    obj("wrench", "扳手", "wrench", ("tool", "fastening_tool")), obj("box", "工具箱", "toolbox", ("container",)),
    obj("shelf", "车库货架", "shelf", ("support_surface",)), obj("tray", "零件托盘", "parts_tray", ("support_surface",)),
    obj("bench", "工作台", "workbench", ("support_surface",)), obj("screw", "螺丝", "screw", ("fastenable",)),
    obj("drawer", "工具抽屉", "drawer", ("container", "openable")), obj("bin", "螺丝收纳盒", "parts_bin", ("container",)),
], ["wrench", "box", "shelf", "tray", "bench"], ["STORE_IN", "STORE_ON", "PLACE_ON", "PLACE_ON"])

add("storage_receipts", "storage_organization", "train", "home_office", "file_receipts", [
    obj("receipt", "收据", "paper", ("movable",)), obj("folder", "票据夹", "folder", ("container",)),
    obj("cabinet", "文件柜", "cabinet", ("container", "openable")), obj("desk", "书桌", "desk", ("support_surface",)),
    obj("tray", "文件托盘", "tray", ("support_surface",)), obj("pen", "笔", "pen", ("movable",)),
    obj("document_holder", "文件立架", "document_holder", ("support_surface",)), obj("bin", "纸类回收箱", "recycling_bin", ("container",)),
], ["receipt", "folder", "cabinet", "desk", "tray"], ["STORE_IN", "STORE_IN", "PLACE_ON", "STORE_ON"], ambiguous={4: ["document_holder"]})

# DEV families: household cleaning and tools/simple manipulation (6 episodes).
add("clean_laundry", "household_cleaning", "dev", "laundry_room", "wash_clothes", [
    obj("shirt", "白色衬衫", "shirt", ("washable", "movable"), color="white"), obj("basket", "洗衣篮", "laundry_basket", ("container",)),
    obj("washer", "洗衣机", "washing_machine", ("washing_machine", "washing_equipment")), obj("detergent", "洗衣液", "detergent", ("detergent",)),
    obj("rack", "晾衣架", "drying_rack", ("support_surface", "drying_surface")), obj("closet", "储物柜", "cabinet", ("container",)),
    obj("jeans", "牛仔裤", "jeans", ("washable",)), obj("bin", "垃圾桶", "waste_bin", ("container",)),
], ["shirt", "basket", "washer", "detergent", "rack"], ["STORE_IN", "WASH_WITH", "WASH_WITH", "STORE_ON"], ambiguous={1: ["washer"]})

add("clean_dishes", "household_cleaning", "dev", "kitchen", "wash_a_cup", [
    obj("cup", "陶瓷杯", "cup", ("dish", "rinsable", "washable")), obj("sink", "水槽", "sink", ("sink", "water_source")),
    obj("soap", "洗洁精", "detergent", ("detergent",)), obj("rack", "沥水架", "dish_rack", ("support_surface",)),
    obj("cabinet", "橱柜", "cabinet", ("container", "openable")), obj("towel", "抹布", "towel", ("rinsable",)),
    obj("plate", "盘子", "plate", ("dish",)), obj("bin", "垃圾桶", "waste_bin", ("container",)),
], ["cup", "sink", "soap", "rack", "cabinet"], ["RINSE_WITH", "WASH_WITH", "STORE_ON", "STORE_IN"])

add("clean_spill", "household_cleaning", "dev", "utility_room", "clean_a_floor_spill", [
    obj("spill", "地面污渍", "spill", ("cleanable",)), obj("mop", "拖把", "mop", ("tool",)),
    obj("bucket", "水桶", "bucket", ("container",)), obj("sink", "水槽", "sink", ("sink", "water_source")),
    obj("bin", "污物桶", "waste_bin", ("container",)), obj("soap", "清洁剂", "cleaner", ("cleaning_agent",)),
    obj("cloth", "抹布", "cloth", ("cleanable",)), obj("cabinet", "清洁柜", "cabinet", ("container",)),
], ["spill", "mop", "bucket", "sink", "bin"], ["USE_WITH", "PLACE_IN", "FILL_FROM", "DISCARD_IN"], ambiguous={2: ["soap"]})

add("tool_screw", "tools_simple_manipulation", "dev", "workbench", "fasten_a_fastener", [
    obj("screw", "紧固件", "fastener", ("fastenable", "fastener")), obj("driver", "螺丝刀", "screwdriver", ("tool", "fastening_tool")),
    obj("tray", "零件托盘", "parts_tray", ("support_surface",)), obj("box", "工具箱", "toolbox", ("container",)),
    obj("bench", "工作台", "workbench", ("support_surface",)), obj("bolt", "螺栓", "bolt", ("fastenable",)),
    obj("wrench", "扳手", "wrench", ("tool", "fastening_tool")), obj("bin", "零件盒", "parts_bin", ("container",)),
], ["screw", "driver", "tray", "box", "bench"], ["FASTEN_WITH", "STORE_ON", "STORE_IN", "PLACE_ON"], ambiguous={1: ["wrench"]})

add("tool_nail", "tools_simple_manipulation", "dev", "workshop", "drive_a_nail", [
    obj("nail", "钉子", "nail", ("fastenable",)), obj("hammer", "锤子", "hammer", ("tool",)),
    obj("board", "木板", "board", ("support_surface",)), obj("bench", "工作台", "workbench", ("support_surface",)),
    obj("box", "工具盒", "toolbox", ("container",)), obj("screw", "螺丝", "screw", ("fastenable",)),
    obj("tray", "零件托盘", "parts_tray", ("support_surface",)), obj("gloves", "手套", "gloves", ("movable",)),
], ["nail", "hammer", "board", "bench", "box"], ["USE_WITH", "PLACE_ON", "PLACE_ON", "STORE_IN"])

add("tool_bolt", "tools_simple_manipulation", "dev", "garage", "tighten_a_bolt", [
    obj("bolt", "螺栓", "bolt", ("fastenable",)), obj("wrench", "扳手", "wrench", ("tool", "fastening_tool")),
    obj("tray", "零件托盘", "parts_tray", ("support_surface",)), obj("box", "工具箱", "toolbox", ("container",)),
    obj("shelf", "货架", "shelf", ("support_surface",)), obj("screw", "螺丝", "screw", ("fastenable",)),
    obj("driver", "螺丝刀", "screwdriver", ("tool", "fastening_tool")), obj("bench", "工作台", "workbench", ("support_surface",)),
], ["bolt", "wrench", "tray", "box", "shelf"], ["FASTEN_WITH", "STORE_ON", "STORE_IN", "STORE_ON"])

# HELD-OUT families: kitchen, assistive handover, state-dependent and adversarial.
add("kitchen_apple", "kitchen_food_preparation", "held_out", "kitchen", "prepare_an_apple", [
    obj("apple", "红色苹果", "apple", ("food", "cuttable", "washable"), color="red"), obj("knife", "水果刀", "knife", ("tool", "cutting_tool")),
    obj("board", "砧板", "cutting_board", ("support_surface",)), obj("plate", "白色盘子", "plate", ("support_surface",), color="white"),
    obj("sink", "水槽", "sink", ("sink", "water_source")), obj("orange", "橙子", "orange", ("food", "juicable")),
    obj("juicer", "榨汁机", "juicer", ("juicer", "juicing_appliance")), obj("bin", "厨余桶", "waste_bin", ("container",)),
], ["apple", "knife", "board", "plate", "sink"], ["CUT_WITH", "PLACE_ON", "PLACE_ON", "RINSE_WITH"], ambiguous={1: ["board"]})

add("kitchen_juice", "kitchen_food_preparation", "held_out", "kitchen", "make_orange_juice", [
    obj("orange", "橙子", "orange", ("food", "juicable")), obj("juicer", "榨汁机", "juicer", ("juicer", "juicing_appliance")),
    obj("cup", "玻璃杯", "cup", ("container", "dish")), obj("sink", "水槽", "sink", ("sink", "water_source")),
    obj("fridge", "冰箱", "refrigerator", ("container", "openable")), obj("knife", "小刀", "knife", ("tool", "cutting_tool")),
    obj("board", "砧板", "cutting_board", ("support_surface",)), obj("bin", "垃圾桶", "waste_bin", ("container",)),
], ["orange", "juicer", "cup", "sink", "fridge"], ["JUICE_WITH", "POUR_INTO", "RINSE_WITH", "STORE_IN"])

add("kitchen_vegetable", "kitchen_food_preparation", "held_out", "kitchen", "prepare_a_vegetable", [
    obj("vegetable", "青菜", "vegetable", ("food", "washable", "cuttable")), obj("board", "砧板", "cutting_board", ("support_surface",)),
    obj("knife", "菜刀", "knife", ("tool", "cutting_tool")), obj("pan", "炒锅", "pan", ("container", "cookware")),
    obj("stove", "炉灶", "stove", ("heating_appliance",)), obj("sink", "水槽", "sink", ("sink", "water_source")),
    obj("plate", "餐盘", "plate", ("support_surface",)), obj("fridge", "冰箱", "refrigerator", ("container",)),
], ["vegetable", "board", "knife", "pan", "stove"], ["PLACE_ON", "CUT_WITH", "PLACE_IN", "USE_WITH"], ambiguous={1: ["sink"]})

add("assist_medicine", "assistive_handover", "held_out", "bedside", "prepare_medicine_handover_with_water", [
    obj("medicine", "药盒", "medicine_box", ("movable",)), obj("user_zone", "用户区域", "user_zone", ("user_zone", "support_surface")),
    obj("water", "水瓶", "water_bottle", ("container",)), obj("cup", "水杯", "cup", ("container",)),
    obj("tray", "床头托盘", "tray", ("support_surface",)), obj("cabinet", "床头柜", "cabinet", ("container", "openable")),
    obj("book", "书", "book", ("movable",)), obj("bin", "垃圾桶", "waste_bin", ("container",)),
], ["medicine", "user_zone", "water", "cup", "tray"], ["HAND_OVER", "USE_WITH", "POUR_INTO", "PLACE_ON"], ambiguous={1: ["water"]})

add("assist_glasses", "assistive_handover", "held_out", "bedroom", "prepare_glasses_for_user", [
    obj("glasses", "眼镜", "glasses", ("movable",)), obj("case", "眼镜盒", "case", ("container",)),
    obj("user_zone", "用户区域", "user_zone", ("user_zone", "support_surface")), obj("drawer", "抽屉", "drawer", ("container", "openable")),
    obj("stand", "眼镜架", "stand", ("support_surface",)), obj("cloth", "镜布", "cloth", ("movable",)),
    obj("book", "书", "book", ("movable",)), obj("bin", "收纳篮", "basket", ("container",)),
], ["glasses", "case", "user_zone", "drawer", "stand"], ["PLACE_IN", "HAND_OVER", "STORE_IN", "PLACE_ON"], ambiguous={1: ["user_zone"]})

add("assist_phone", "assistive_handover", "held_out", "living_room", "prepare_a_phone_for_handover", [
    obj("phone", "手机", "phone", ("chargeable", "movable")), obj("charger", "充电座", "charger", ("charging_target",)),
    obj("user_zone", "用户区域", "user_zone", ("user_zone", "support_surface")), obj("stand", "手机支架", "stand", ("support_surface",)),
    obj("cable", "充电线", "cable", ("power_cable",)), obj("case", "手机壳", "case", ("container",)),
    obj("table", "边桌", "table", ("support_surface",)), obj("drawer", "抽屉", "drawer", ("container", "openable")),
], ["phone", "charger", "user_zone", "stand", "table"], ["CHARGE_WITH", "HAND_OVER", "PLACE_ON", "PLACE_ON"])

add("state_charger", "state_dependent", "held_out", "home_office", "charge_a_phone_using_an_available_charger", [
    obj("phone", "手机", "phone", ("chargeable",)), obj("occupied_charger", "已占用的充电座", "charger", ("charging_target",), state={"occupancy": "occupied"}),
    obj("free_charger", "空闲充电座", "charger", ("charging_target",), state={"occupancy": "free"}), obj("cable", "充电线", "cable", ("power_cable",)),
    obj("outlet", "插座", "power_outlet", ("power_source",)), obj("stand", "手机支架", "stand", ("support_surface",)),
    obj("case", "手机壳", "case", ("container",)), obj("book", "书", "book", ("movable",)),
], ["phone", "free_charger", "cable", "outlet", "stand"], ["CHARGE_WITH", "USE_WITH", "USE_WITH", "PLACE_ON"], ambiguous={2: ["outlet"]})

add("state_bins", "state_dependent", "held_out", "playroom", "put_a_toy_in_available_storage", [
    obj("toy", "玩具", "toy", ("movable",)), obj("full_bin", "已满且受阻收纳箱", "storage_bin", ("container",), state={"full": True, "status": "blocked", "lid": "closed"}),
    obj("empty_bin", "空收纳箱", "storage_bin", ("container",), state={"availability": "available"}), obj("shelf", "玩具架", "shelf", ("support_surface",)),
    obj("label", "标签", "label", ("movable",)), obj("cabinet", "柜子", "cabinet", ("container", "openable"), state={"lid": "open"}),
    obj("basket", "不可用玩具篮", "basket", ("container",), state={"availability": "unavailable"}), obj("table", "桌子", "table", ("support_surface",)),
], ["toy", "empty_bin", "shelf", "label", "cabinet"], ["STORE_IN", "STORE_ON", "USE_WITH", "STORE_IN"], ambiguous={1: ["cabinet"]})

add("state_dishes", "state_dependent", "held_out", "kitchen", "store_a_dish_in_available_space", [
    obj("plate", "盘子", "plate", ("dish", "movable")), obj("full_cabinet", "已满且关闭的橱柜", "cabinet", ("container", "openable"), state={"availability": "full", "lid": "closed"}),
    obj("free_rack", "空沥水架", "dish_rack", ("support_surface",)), obj("cabinet", "空橱柜", "cabinet", ("container", "openable")),
    obj("counter", "台面", "counter", ("support_surface",)), obj("sink", "水槽", "sink", ("sink", "water_source")),
    obj("cup", "杯子", "cup", ("container",)), obj("bin", "回收桶", "recycling_bin", ("container",)),
], ["plate", "free_rack", "cabinet", "sink", "counter"], ["STORE_ON", "STORE_IN", "RINSE_WITH", "PLACE_ON"], ambiguous={2: ["counter"]})

add("state_tool_absent", "state_dependent", "held_out", "workbench", "fasten_a_screw_with_a_screwdriver", [
    obj("screw", "螺丝", "screw", ("fastenable",)), obj("parts_tray", "零件托盘", "parts_tray", ("support_surface",)),
    obj("toolbox", "工具箱", "toolbox", ("container",)), obj("bench", "工作台", "workbench", ("support_surface",)),
    obj("bolt", "螺栓", "bolt", ("fastenable",)), obj("gloves", "手套", "gloves", ("movable",)),
    obj("label", "标签", "label", ("movable",)), obj("bin", "零件盒", "parts_bin", ("container",)),
], ["screw", "parts_tray"], ["NONE"], invalid_round=1,
   task_state_overrides={1: {"required_tool_type": "screwdriver", "required_tool_available": False}})

add("adv_multiple_surfaces", "ambiguous_adversarial", "held_out", "study", "place_a_book_on_a_surface", [
    obj("book", "书", "book", ("movable",)), obj("desk", "书桌", "desk", ("support_surface",)),
    obj("shelf", "书架", "bookshelf", ("support_surface", "storage_surface")), obj("tray", "托盘", "tray", ("support_surface",)),
    obj("drawer", "抽屉", "drawer", ("container", "openable")), obj("lamp", "台灯", "lamp", ("support_surface",)),
    obj("box", "收纳盒", "storage_box", ("container",)), obj("chair", "椅子", "chair", ("support_surface",)),
], ["book", "desk", "tray", "drawer", "shelf"], ["PLACE_ON", "PLACE_ON", "STORE_IN", "STORE_ON"], ambiguous={1: ["shelf", "tray", "lamp", "chair"], 2: ["shelf", "lamp"]})

add("adv_completed", "ambiguous_adversarial", "held_out", "home_office", "check_a_completed_charging_task", [
    obj("phone", "手机", "phone", ("chargeable",)), obj("charger", "充电座", "charger", ("charging_target",)),
    obj("cable", "充电线", "cable", ("power_cable",)), obj("outlet", "插座", "power_outlet", ("power_source",)),
    obj("stand", "手机支架", "stand", ("support_surface",)), obj("case", "手机壳", "case", ("container",)),
    obj("book", "书", "book", ("movable",)), obj("drawer", "抽屉", "drawer", ("container", "openable")),
], ["phone", "charger", "cable", "outlet", "stand"], ["CHARGE_WITH", "USE_WITH", "USE_WITH", "PLACE_ON"], completed_round=4)

add("adv_no_charger", "ambiguous_adversarial", "held_out", "mixed_storage", "charge_a_phone_with_no_charger", [
    obj("phone", "手机", "phone", ("chargeable",)), obj("book", "书", "book", ("movable",)),
    obj("plate", "盘子", "plate", ("support_surface",)), obj("fruit", "苹果", "apple", ("food",)),
    obj("knife", "水果刀", "knife", ("tool", "cutting_tool")), obj("cabinet", "橱柜", "cabinet", ("container", "openable")),
    obj("bin", "垃圾桶", "waste_bin", ("container",)), obj("desk", "书桌", "desk", ("support_surface",)),
], ["phone", "book"], ["NONE"], invalid_round=1,
   task_state_overrides={1: {"required_relation": "CHARGE_WITH", "required_target_affordance": "charging_target"}})


def build() -> dict[str, Any]:
    if len(TEMPLATES) != 33:
        raise AssertionError(f"expected 33 episodes, got {len(TEMPLATES)}")
    decisions = sum(len(episode["decision_points"]) for episode in TEMPLATES)
    family_counts: dict[str, int] = {}
    split_counts: dict[str, int] = {}
    split_decision_counts: dict[str, int] = {}
    for episode in TEMPLATES:
        family_counts[episode["scene_family"]] = family_counts.get(episode["scene_family"], 0) + 1
        split_counts[episode["split"]] = split_counts.get(episode["split"], 0) + 1
        split_decision_counts[episode["split"]] = split_decision_counts.get(episode["split"], 0) + len(episode["decision_points"])
        if not 1 <= len(episode["decision_points"]) <= 4:
            raise AssertionError("each episode must have one to four sequential decision points")
        for point in episode["decision_points"]:
            model_input = point["model_input"]
            if "evaluation_only" in model_input or "acceptable_next_targets" in model_input:
                raise AssertionError("evaluation labels leaked into model_input")
            if len(model_input["scene"]["objects"]) != 8:
                raise AssertionError("all frozen scenes must contain eight objects")
            expected_remaining = [obj["id"] for obj in model_input["scene"]["objects"]
                                  if obj["id"] not in set(model_input["selection_history"])]
            if model_input["candidate_next_object_ids"] != expected_remaining:
                raise AssertionError("candidate list does not equal all unselected objects")
            labels = point["evaluation_only"]
            if not set(labels["acceptable_next_targets"]).issubset(set(expected_remaining)):
                raise AssertionError("an acceptable target is not a remaining candidate")
            by_id = {item["id"]: item for item in model_input["scene"]["objects"]}
            history_sources = [by_id[item_id] for item_id in model_input["selection_history"]]
            for target_id, relation_types in labels["acceptable_relations_by_target"].items():
                if target_id not in labels["acceptable_next_targets"]:
                    raise AssertionError("relation labels must align with acceptable targets")
                for relation_type in relation_types:
                    if not any(relation_compatibility(relation_type, source, by_id[target_id])[0]
                               for source in history_sources):
                        raise AssertionError(
                            f"incompatible gold relation {relation_type} for {episode['episode_id']} "
                            f"round {point['round_index']} target {target_id}"
                        )
    if family_counts != {
        "desktop_office": 7, "storage_organization": 7, "household_cleaning": 3,
        "tools_simple_manipulation": 3, "kitchen_food_preparation": 3,
        "assistive_handover": 3, "state_dependent": 4, "ambiguous_adversarial": 3,
    }:
        raise AssertionError(f"unexpected family allocation: {family_counts}")
    if decisions != 126:
        raise AssertionError(f"expected 126 decisions after removing the invalid adversarial trajectory, got {decisions}")
    if split_counts != {"train": 14, "dev": 6, "held_out": 13}:
        raise AssertionError(f"unexpected split allocation: {split_counts}")
    if split_decision_counts != {"train": 56, "dev": 24, "held_out": 46}:
        raise AssertionError(f"unexpected decision allocation: {split_decision_counts}")
    state_rows = [item.get("current_state", {}) for episode in TEMPLATES
                  for item in episode["decision_points"][0]["model_input"]["scene"]["objects"]]
    task_states = [point["model_input"]["current_task_state"] for episode in TEMPLATES
                   for point in episode["decision_points"]]
    state_text = json.dumps(state_rows, ensure_ascii=False).casefold()
    if not all(token in state_text for token in ('"lid": "open"', '"lid": "closed"',
                                                  '"availability": "unavailable"', '"status": "blocked"',
                                                  '"occupancy": "occupied"', '"full": true')):
        raise AssertionError("frozen benchmark is missing a required explicit dynamic state")
    if not any(state.get("required_tool_available") is False for state in task_states):
        raise AssertionError("frozen benchmark is missing the tool-absent task state")
    if not any(state.get("required_relation") == "CHARGE_WITH"
               and state.get("required_target_affordance") == "charging_target" for state in task_states):
        raise AssertionError("frozen benchmark is missing the absent-charger adversarial state")
    if not any(state.get("phase") == "complete" for state in task_states):
        raise AssertionError("frozen benchmark is missing an already-completed task")
    absent_tool = next(episode for episode in TEMPLATES if episode["episode_id"] == "state_tool_absent")
    absent_scene = absent_tool["decision_points"][0]["model_input"]["scene"]["objects"]
    if any("screwdriver" in str(item.get("object_type", "")).casefold()
           or "fastening_tool" in {tag.casefold() for tag in item.get("affordance_tags", [])}
           for item in absent_scene):
        raise AssertionError("the declared absent tool appears in the scene")
    return {
        "schema_version": "m30-semantic-context-sequence-benchmark-v2",
        "benchmark_id": "m30-nextgen-sequential-context-v2",
        "frozen_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "episode_count": len(TEMPLATES),
        "decision_point_count": decisions,
        "model_input_contract": "Only decision_point.model_input is supplied to SemanticContextEngine.predict_next_target; evaluation_only is scorer-only.",
        "ground_truth_source": "human-authored relation/affordance templates defined in build_m30_benchmark.py before model calls",
        "split_policy": "family-held-out; train=desktop_office+storage_organization; dev=household_cleaning+tools_simple_manipulation; held_out=kitchen_food_preparation+assistive_handover+state_dependent+ambiguous_adversarial",
        "family_episode_counts": family_counts,
        "split_episode_counts": split_counts,
        "split_decision_point_counts": split_decision_counts,
        "episodes": TEMPLATES,
    }


def main() -> int:
    if BENCHMARK.exists() or LOCK.exists():
        raise FileExistsError("refusing to overwrite an existing M30 benchmark or lock")
    data = build()
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    BENCHMARK.write_text(serialized, encoding="utf-8")
    digest = hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()
    lock = {
        "benchmark_id": data["benchmark_id"],
        "path": BENCHMARK.name,
        "sha256": digest,
        "episode_count": data["episode_count"],
        "decision_point_count": data["decision_point_count"],
        "family_episode_counts": data["family_episode_counts"],
        "split_episode_counts": data["split_episode_counts"],
        "frozen_at_utc": data["frozen_at_utc"],
        "expected_answers_generated_independently": True,
        "evaluation_started_before_freeze": False,
    }
    LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(lock, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
