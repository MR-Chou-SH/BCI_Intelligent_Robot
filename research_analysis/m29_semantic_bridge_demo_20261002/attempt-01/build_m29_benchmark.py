"""Build and freeze the M29 semantic language bridge benchmark exactly once."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUT = Path(__file__).resolve().parent
BENCHMARK = OUT / "semantic_bridge_benchmark.json"
LOCK = OUT / "semantic_bridge_benchmark_lock.json"
M20_SCENE = OUT / "current_scene_snapshot.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _snapshot(scene_id: str, template_id: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "sceneId": scene_id,
        "templateId": template_id,
        "randomSeed": None,
        "createdUtc": "2026-10-02T00:00:00Z",
        "randomizationMethod": "m29_synthetic_semantic_fixture_v1",
        "fallbackUsed": False,
        "candidateOrderFarToNearLeftToRight": [item["semanticId"] for item in objects],
        "objects": objects,
    }


def _object(object_id: str, object_type: str, tags: list[str], *, selectable: bool = True) -> dict[str, Any]:
    return {
        "semanticId": object_id,
        "targetId": object_id,
        "logicalBlockId": "",
        "objectType": object_type,
        "role": "semantic_fixture",
        "sourceKind": "m29_synthetic_fixture",
        "selectable": selectable,
        "fixedPose": not ("movable" in tags),
        "affordanceTags": list(tags),
        "positionMeters": None,
        "dimensionsMeters": None,
    }


def _input(scene: dict[str, Any], nouns: list[str], *, mode: str = "strict", intent: str | None = None, states: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"scene": scene, "nouns": nouns, "mode": mode, "intent_context": intent, "current_states": states or {}}


def _case(case_id: str, split: str, family: str, model_input: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    return {"case_id": case_id, "scene_split": split, "scene_family": family, "model_input": model_input, "evaluation_only": evaluation}


def build_cases() -> list[dict[str, Any]]:
    current = json.loads(M20_SCENE.read_text(encoding="utf-8"))
    future_book = json.loads((OUT / "future_book_pen_scene_snapshot.json").read_text(encoding="utf-8")) if (OUT / "future_book_pen_scene_snapshot.json").is_file() else None
    if future_book is None:
        raise FileNotFoundError("future_book_pen_scene_snapshot.json must be created before benchmark freeze")
    cups = _snapshot("m29-future-cup-tray", "future_kitchenware", [
        _object("cup_source_01", "cup", ["movable", "drinkware"]),
        _object("serving_tray_01", "serving_tray", ["fixed_surface", "cup_destination"]),
        _object("cupboard_01", "cupboard", ["container", "openable", "closable"]),
        _object("sink_01", "sink", ["fixed_surface", "water_source", "sink"]),
    ])
    toys = _snapshot("m29-future-toy-room", "future_toy_room", [
        _object("plush_toy_01", "plush_toy", ["movable", "toy"]),
        _object("toy_bin_01", "toy_bin", ["container", "openable"]),
        _object("display_shelf_01", "display_shelf", ["fixed_surface", "toy_destination"]),
    ])
    tabletop = _snapshot("m29-future-control-panel", "future_control_panel", [
        _object("button_01", "button", ["pressable", "fixed_surface"]),
        _object("controller_01", "controller", ["pressable", "movable"]),
        _object("desk_01", "desk_surface", ["fixed_surface"]),
    ])
    watch_tray = _snapshot("m29-free-watch-tray", "m29_free_noun_fixture", [
        _object("fixture_anchor_surface", "fixture_anchor", ["fixed_surface"]),
    ])
    cases: list[dict[str, Any]] = []

    def add(case_id, split, family, scene, nouns, expected_status, *, mode="strict", intent=None, states=None, actions=None, targets=None, policy=None, note=""):
        evaluation = {
            "expected_status": expected_status,
            "expected_action_sequence": actions,
            "expected_target_ids": targets or [],
            "acceptance_policy": policy or "exact_plan_status_and_action_sequence",
            "case_note": note,
        }
        cases.append(_case(case_id, split, family, _input(scene, nouns, mode=mode, intent=intent, states=states), evaluation))

    # Current M20 assistive-desk tasks, bilingual labels, aliases and validation traps.
    add("m20_phone_charger_zh", "current_m20", "assistive_desk", current, ["手机", "无线充电座"], "executable", actions=["PICK:assist_phone", "PLACE_ON:assist_phone:assist_wireless_charger"], targets=["assist_wireless_charger"])
    add("m20_phone_charger_en", "current_m20", "assistive_desk", current, ["smartphone", "wireless charger"], "executable", actions=["PICK:assist_phone", "PLACE_ON:assist_phone:assist_wireless_charger"], targets=["assist_wireless_charger"])
    add("m20_phone_charger_alias", "current_m20", "assistive_desk", current, ["cell phone", "charging pad"], "executable", actions=["PICK:assist_phone", "PLACE_ON:assist_phone:assist_wireless_charger"], targets=["assist_wireless_charger"])
    add("m20_reverse_charger_phone", "current_m20", "assistive_desk", current, ["wireless charging pad", "phone"], "executable", intent="Place the phone on the wireless charging pad.", actions=["PICK:assist_phone", "PLACE_ON:assist_phone:assist_wireless_charger"], targets=["assist_wireless_charger"])
    add("m20_medicine_closed_box_zh", "current_m20", "assistive_desk", current, ["小药盒", "收纳盒"], "executable", intent="Put the medicine box into the storage box.", states={"assist_storage_box": {"lid": "closed"}}, actions=["OPEN:assist_storage_box", "PICK:assist_medicine_box", "PLACE_IN:assist_medicine_box:assist_storage_box", "CLOSE:assist_storage_box"], targets=["assist_storage_box"])
    add("m20_medicine_closed_box_en", "current_m20", "assistive_desk", current, ["small medicine box", "storage container"], "executable", intent="Put the medicine box into the storage container.", states={"assist_storage_box": {"lid": "closed"}}, actions=["OPEN:assist_storage_box", "PICK:assist_medicine_box", "PLACE_IN:assist_medicine_box:assist_storage_box", "CLOSE:assist_storage_box"], targets=["assist_storage_box"])
    add("m20_phone_inside_storage", "current_m20", "assistive_desk", current, ["手机", "储物盒"], "executable", intent="Place the phone inside the storage box.", states={"assist_storage_box": {"lid": "open"}}, actions=["PICK:assist_phone", "PLACE_IN:assist_phone:assist_storage_box"], targets=["assist_storage_box"])
    add("m20_medicine_user_zone", "current_m20", "assistive_desk", current, ["小药盒", "用户区域"], "executable", intent="Put the medicine box on the user-side placement zone.", actions=["PICK:assist_medicine_box", "PLACE_ON:assist_medicine_box:assist_user_zone"], targets=["assist_user_zone"])
    add("m20_button_press_zh", "current_m20", "assistive_desk", current, ["按钮", "开关"], "executable", intent="Press the selected button switch.", actions=["PRESS:assist_button_switch"], targets=["assist_button_switch"])
    add("m20_button_press_en", "current_m20", "assistive_desk", current, ["button", "switch"], "executable", intent="Press the button switch.", actions=["PRESS:assist_button_switch"], targets=["assist_button_switch"])
    add("m20_ambiguous_relations", "current_m20", "assistive_desk", current, ["phone", "wireless charger", "storage box"], "ambiguous", states={"assist_storage_box": {"lid": "closed"}}, policy="ambiguous_with_multiple_safe_alternatives")
    add("m20_invalid_press_medicine", "current_m20", "assistive_desk", current, ["medicine box"], "invalid", intent="Press the selected medicine box.", policy="invalid_no_actions")
    add("m20_invalid_place_in_charger", "current_m20", "assistive_desk", current, ["phone", "wireless charger"], "invalid", intent="Put the phone inside the wireless charger.", policy="invalid_no_actions")
    add("m20_missing_pixel_dock", "current_m20", "assistive_desk", current, ["phone", "wireless charger"], "invalid", intent="Put the phone on the Pixel Dock.", policy="invalid_no_actions")
    add("m20_unknown_strict_object", "current_m20", "assistive_desk", current, ["unlisted quantum widget", "phone"], "invalid", policy="unknown_strict_object_rejected_without_dispatch")
    add("m20_medicine_user_zone_en", "current_m20", "assistive_desk", current, ["pill box", "user zone"], "executable", intent="Hand the pill box to the user on the user-side zone.", actions=["PICK:assist_medicine_box", "PLACE_ON:assist_medicine_box:assist_user_zone"], targets=["assist_user_zone"])

    # Held-out family 1: books, shelf, storage box, pen and drawer.
    add("book_to_bookshelf_en", "held_out", "future_bookshelf", future_book, ["book", "bookshelf"], "executable", intent="Place the book on the bookshelf.", actions=["PICK:future_book_01", "PLACE_ON:future_book_01:future_book_shelf"], targets=["future_book_shelf"])
    add("book_to_bookshelf_zh", "held_out", "future_bookshelf", future_book, ["书", "书架"], "executable", intent="Place the book on the bookshelf.", actions=["PICK:future_book_01", "PLACE_ON:future_book_01:future_book_shelf"], targets=["future_book_shelf"])
    add("reverse_bookshelf_book", "held_out", "future_bookshelf", future_book, ["bookshelf", "novel"], "executable", intent="Return the book to the bookshelf.", actions=["PICK:future_book_01", "PLACE_ON:future_book_01:future_book_shelf"], targets=["future_book_shelf"])
    add("pen_to_closed_drawer", "held_out", "future_desk_tools", future_book, ["pen", "desk drawer"], "executable", intent="Store the pen in the desk drawer.", states={"future_pen_drawer": {"lid": "closed"}}, actions=["OPEN:future_pen_drawer", "PICK:future_pen_01", "PLACE_IN:future_pen_01:future_pen_drawer", "CLOSE:future_pen_drawer"], targets=["future_pen_drawer"])
    add("pen_to_drawer_alias", "held_out", "future_desk_tools", future_book, ["marker", "drawer"], "executable", intent="Put the pen inside the drawer.", states={"future_pen_drawer": {"lid": "open"}}, actions=["PICK:future_pen_01", "PLACE_IN:future_pen_01:future_pen_drawer"], targets=["future_pen_drawer"])
    add("pen_on_desk", "held_out", "future_desk_tools", future_book, ["pen", "desk surface"], "executable", intent="Leave the pen on the desk surface.", actions=["PICK:future_pen_01", "PLACE_ON:future_pen_01:desk_surface_01"], targets=["desk_surface_01"])
    add("pen_ambiguous_organize", "held_out", "future_desk_tools", future_book, ["pen", "desk drawer", "desk surface"], "ambiguous", states={"future_pen_drawer": {"lid": "open"}}, policy="ambiguous_with_multiple_safe_alternatives")
    add("book_into_closed_box", "held_out", "future_book_storage", future_book, ["book", "book storage box"], "executable", intent="Put the book inside its storage box.", states={"future_book_box": {"lid": "closed"}}, actions=["OPEN:future_book_box", "PICK:future_book_01", "PLACE_IN:future_book_01:future_book_box", "CLOSE:future_book_box"], targets=["future_book_box"])
    add("missing_wall_hook", "held_out", "future_bookshelf", future_book, ["book", "bookshelf"], "invalid", intent="Hang the book on the Wall Hook.", policy="missing_named_destination_rejected")

    # Held-out family 2: kitchenware and dynamic containers.
    add("cup_on_serving_tray", "held_out", "future_kitchenware", cups, ["cup", "serving tray"], "executable", intent="Place the cup on the serving tray.", actions=["PICK:cup_source_01", "PLACE_ON:cup_source_01:serving_tray_01"], targets=["serving_tray_01"])
    add("reverse_tray_cup", "held_out", "future_kitchenware", cups, ["serving tray", "cup"], "executable", intent="Move the cup onto the serving tray.", actions=["PICK:cup_source_01", "PLACE_ON:cup_source_01:serving_tray_01"], targets=["serving_tray_01"])
    add("cup_in_open_cupboard", "held_out", "future_kitchenware", cups, ["cup", "cupboard"], "executable", intent="Put the cup inside the cupboard.", states={"cupboard_01": {"lid": "open"}}, actions=["PICK:cup_source_01", "PLACE_IN:cup_source_01:cupboard_01"], targets=["cupboard_01"])
    add("cup_in_closed_cupboard", "held_out", "future_kitchenware", cups, ["cup", "cupboard"], "executable", intent="Put the cup away in the cupboard.", states={"cupboard_01": {"lid": "closed"}}, actions=["OPEN:cupboard_01", "PICK:cup_source_01", "PLACE_IN:cup_source_01:cupboard_01", "CLOSE:cupboard_01"], targets=["cupboard_01"])
    add("cup_to_sink_surface", "held_out", "future_kitchenware", cups, ["cup", "sink"], "executable", intent="Place the cup on the sink counter.", actions=["PICK:cup_source_01", "PLACE_ON:cup_source_01:sink_01"], targets=["sink_01"])
    add("cup_ambiguous_put_away", "held_out", "future_kitchenware", cups, ["cup", "cupboard", "serving tray"], "ambiguous", states={"cupboard_01": {"lid": "open"}}, policy="ambiguous_with_multiple_safe_alternatives")
    add("invalid_cup_charge", "held_out", "future_kitchenware", cups, ["cup", "sink"], "invalid", intent="Charge the cup on the sink.", policy="unsupported_relation_rejected")

    # Held-out family 3: toy storage, support surfaces, button/device affordances.
    add("toy_in_bin", "held_out", "future_toy_room", toys, ["plush toy", "toy bin"], "executable", intent="Put the plush toy inside the toy bin.", actions=["PICK:plush_toy_01", "PLACE_IN:plush_toy_01:toy_bin_01"], targets=["toy_bin_01"])
    add("toy_on_shelf", "held_out", "future_toy_room", toys, ["plush toy", "display shelf"], "executable", intent="Display the toy on the shelf.", actions=["PICK:plush_toy_01", "PLACE_ON:plush_toy_01:display_shelf_01"], targets=["display_shelf_01"])
    add("toy_ambiguous_tidy", "held_out", "future_toy_room", toys, ["plush toy", "toy bin", "display shelf"], "ambiguous", policy="ambiguous_with_multiple_safe_alternatives")
    add("button_container_conflict", "held_out", "future_control_panel", tabletop, ["button", "controller"], "invalid", intent="Store the button inside the controller.", policy="unsupported_container_relation_rejected")
    add("button_press_future_surface", "held_out", "future_control_panel", tabletop, ["button", "desk surface"], "executable", intent="Press the desk button.", actions=["PRESS:button_01"], targets=["button_01"])

    # Exploratory free-noun cases. These are judged for schema, visible unverified
    # trust labels, bounded inference and dispatch prohibition, not semantic truth.
    free_cases = [
        ("free_watch_tray", ["wristwatch", "wooden tray"]),
        ("free_mug_mat", ["ceramic mug", "silicone mat"]),
        ("free_flashlight_desk", ["flashlight", "bedside table"]),
        ("free_controller_console", ["game controller", "charging dock"]),
        ("free_bicycle_helmet_hook", ["bicycle helmet", "wall hook"]),
        ("free_unknown_pair", ["invented luminoid", "portable organizer"]),
    ]
    for case_id, nouns in free_cases:
        add(case_id, "exploratory_free_noun", "free_noun", watch_tray, nouns, "any_safe_status", mode="free", policy="schema_valid_model_inferred_unverified_dispatch_disabled")

    if len(cases) != 43:
        raise AssertionError("M29 benchmark must contain exactly 43 cases")
    return cases


def freeze() -> dict[str, Any]:
    if BENCHMARK.exists() or LOCK.exists():
        raise FileExistsError("M29 benchmark is already frozen; refusing to overwrite")
    cases = build_cases()
    document = {
        "schema_version": "semantic-bridge-benchmark-v1",
        "benchmark_id": "m29-standalone-semantic-language-bridge-v1",
        "frozen_at_utc": _utc_now(),
        "case_count": len(cases),
        "model_input_contract": "Only case.model_input may reach SemanticLanguageBridge; evaluation_only is held by the scorer.",
        "free_mode_contract": "Free-noun expected results measure safe schema and unverified labeling only, not semantic correctness.",
        "cases": cases,
    }
    data = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    BENCHMARK.write_text(data, encoding="utf-8")
    digest = hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()
    lock = {
        "benchmark_id": document["benchmark_id"],
        "path": str(BENCHMARK.relative_to(OUT.parents[1])).replace("\\", "/"),
        "sha256": digest,
        "case_count": len(cases),
        "frozen_at_utc": document["frozen_at_utc"],
        "evaluated_before_freeze": False,
    }
    LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return lock


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False, indent=2))
