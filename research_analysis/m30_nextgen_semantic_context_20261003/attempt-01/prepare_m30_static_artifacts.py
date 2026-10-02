"""Create the frozen M30 experiment spec, scene manifest and relation schema."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from validate_m30_benchmark import validate
from integration.semantic_context_relations import RELATION_TYPES, _SOURCE_TAGS, _TARGET_TAGS


OUT = Path(__file__).resolve().parent
FAMILY_DESCRIPTIONS = {
    "desktop_office": "Office and desktop organization tasks; TRAIN.",
    "storage_organization": "Household storage and organization tasks; TRAIN.",
    "household_cleaning": "Cleaning and washing tasks; DEV.",
    "tools_simple_manipulation": "Basic tool and fastener tasks; DEV.",
    "kitchen_food_preparation": "Food preparation and kitchen relations; HELD_OUT.",
    "assistive_handover": "Object delivery to a user zone; HELD_OUT.",
    "state_dependent": "Selection depends on explicit occupancy, capacity, access or tool state; HELD_OUT.",
    "ambiguous_adversarial": "Multiple plausible surfaces, completed tasks and incompatible contexts; HELD_OUT.",
}
RELATION_DESCRIPTIONS = {
    "CHARGE_WITH": "Charge a compatible source object using a charging target.",
    "STORE_IN": "Store an object inside a compatible container.",
    "STORE_ON": "Store an object on a support or storage surface.",
    "PLACE_IN": "Place an object inside a container.",
    "PLACE_ON": "Place an object on a support surface.",
    "HAND_OVER": "Move an object to an explicit user or handover zone.",
    "CUT_WITH": "Cut a compatible food or cuttable object using a cutting tool.",
    "JUICE_WITH": "Process a compatible fruit using a juicing appliance.",
    "RINSE_WITH": "Rinse a compatible object using a water source.",
    "WASH_WITH": "Wash a compatible item using washing equipment or detergent.",
    "FILL_FROM": "Fill a container from an explicit water source.",
    "POUR_INTO": "Pour contents into a compatible container.",
    "FASTEN_WITH": "Fasten a compatible fastener using an appropriate tool.",
    "PRESS": "Press an explicitly pressable control.",
    "OPEN": "Open an explicitly openable object when its state allows it.",
    "CLOSE": "Close an explicitly openable object that is not already closed.",
    "DISCARD_IN": "Discard an object into a compatible container.",
    "USE_WITH": "Generic contextual use relation; not a physical skill claim.",
    "RELATED_TO": "Objects are contextually related without a more specific relation.",
    "OTHER": "A supported relation outside the named task relation set.",
    "NONE": "No useful relation is asserted.",
}


def _write_once(name: str, value: str) -> None:
    path = OUT / name
    if path.exists():
        raise FileExistsError(f"refusing to overwrite M30 artifact: {name}")
    path.write_text(value, encoding="utf-8")


def main() -> int:
    validation = validate()
    benchmark = json.loads((OUT / "semantic_context_sequence_benchmark_v2.json").read_text(encoding="utf-8"))
    families = {}
    for episode in benchmark["episodes"]:
        family = episode["scene_family"]
        families.setdefault(family, {"description": FAMILY_DESCRIPTIONS[family], "split": episode["split"], "episode_ids": []})
        families[family]["episode_ids"].append(episode["episode_id"])
    _write_once("scene_family_manifest.json", json.dumps({
        "schemaVersion": "m30-scene-family-manifest-v1",
        "benchmarkSha256": validation["sha256"],
        "splitPolicy": "Group holdout by scene family; no family crosses train/dev/held_out.",
        "families": families,
        "episodeCountsBySplit": validation["splitEpisodeCounts"],
    }, ensure_ascii=False, indent=2) + "\n")

    relations = []
    for relation in sorted(RELATION_TYPES):
        relations.append({
            "relationType": relation,
            "description": RELATION_DESCRIPTIONS[relation],
            "requiredSourceAnyOf": sorted(_SOURCE_TAGS.get(relation, frozenset())),
            "requiredTargetAnyOf": sorted(_TARGET_TAGS.get(relation, frozenset())),
        })
    _write_once("relational_affordance_schema.json", json.dumps({
        "schemaVersion": "m30-relational-affordance-v1",
        "relationTypes": relations,
        "stateRules": [
            "Missing state remains unknown and is never treated as false.",
            "Explicit full, occupied, unavailable or blocked state rejects capacity-dependent relations.",
            "Closed non-openable containers reject STORE_IN, PLACE_IN, DISCARD_IN and POUR_INTO.",
            "OPEN requires the openable affordance; CLOSE also rejects an already-closed target.",
            "A relation is not a robot action or an executable manipulation plan.",
        ],
        "candidateMembership": "Only IDs in the supplied remaining selectable candidate list may be ranked.",
    }, ensure_ascii=False, indent=2) + "\n")

    spec = f"""# M30 Experiment Specification

## Question and scope

After each accepted selection, can the M30 semantic engine use the structured scene, explicit object state, full accepted selection history and relational affordances to produce an uncalibrated prior over the next selection? Separately, does measured held-out semantic quality transfer safely to frozen M25 adaptive EEG replay?

This is software-only research. It does not use Quest, ND8, COM11, raw EEG, a physical robot, or real VLA dispatch. M30 generates no VLA instruction. Historical EEG transfer is a simulation because the 88 randomized historical trials do not have prospective semantic-scene labels.

## Frozen benchmark and split

- {validation['episodeCount']} episodes and {validation['decisionPointCount']} next-selection decisions.
- Eight scene families with family-level splits: train 14 episodes / 56 decisions; dev 6 / 24; held-out 13 / 46.
- Train: desktop/office and storage/organization. Dev: household cleaning and tools/simple manipulation. Held-out: kitchen/food, assistive handover, state-dependent and ambiguous/adversarial.
- Human-authored deterministic templates in `build_m30_benchmark.py` defined expected targets, relations, ambiguity, invalidity and task completion before any model evaluation.
- `semantic_context_sequence_benchmark_lock.json` freezes benchmark SHA-256 `{validation['sha256']}`. The model receives only `model_input`; `evaluation_only` is scorer-only. The read-only validator checks prohibited evaluation fields recursively.
- Explicit states include open/closed, free/occupied, full/available, blocked/unavailable, absent required tool, completed task, and equally plausible destinations.

## Semantic Context method

The model ranks every remaining selectable object ID once. It can report `informative`, `ambiguous`, `context_off`, or `invalid`; ambiguous/invalid/completed cases are never forced active. Ordinal 0..1 semantic scores are transformed using the frozen softmax temperature 0.20. The resulting variable-dimensional `q_global` is uncalibrated prior-like evidence, not a probability.

Candidates are paginated in stable scene order, three per page. Each `q_page` contains only candidates on that page and is renormalized. A last page of one or two real candidates is preserved without a fake semantic object. Page projection is an interface adapter and does not mutate global order or selection history.

Coverage gates C65/C85/C100 use only informative, semantically eligible train/dev predictions. A predeclared confidence score combines top semantic score and top-versus-second score margin equally. The fixed gate threshold for each operating point is chosen from train/dev confidence distributions, with C100 including all eligible train/dev cases. Held-out participation is measured as realized and can differ from the target; ambiguous, invalid and completed decisions remain off.

Report overall and eligible participation, active precision, top-1 and top-k recall, relation-type accuracy, ambiguity/completion/invalid handling, wrong-active count, entropy, prior top mass, score margin, repeatability, API latency, correction rate, hallucinated-candidate rate and state contradiction rate by split, scene family and round.

## EEG quality-transfer simulation

Reuse the frozen M25 88-trial cohort and the unchanged FAST, MEDIUM and CONSERVATIVE stopping parameters. Re-verify the M25 manifest and M23 operating-point sidecar before and after replay. The lambda sweep is LAMBDA_SWEEP_PLACEHOLDER.

For each Context prior, fusion is `Normalize(E_i * q_page_i**lambda_ctx)`. Lambda 0 is an exact EEG-only ablation. Context can accelerate only when its page prior top agrees with raw EEG top, q-page top mass meets the frozen 0.70 guard, and frozen EEG evidence, margin and stability requirements hold. The paired EEG-only stop is a hard cap; absent, ambiguous, invalid, late, page-incompatible or disagreeing Context uses exact EEG-only fallback. The selected class remains the raw EEG top.

The primary matrix is 3 coverage OP × 5 LAMBDA_SWEEP_PLACEHOLDER values × 3 frozen EEG OPs, using precomputed Context. A smaller timing sensitivity compares measured API arrival, 0.2 s and 0.4 s. Use deterministic seeded assignments over the held-out semantic predictions. When mapping measured semantic errors onto historical class labels, preserve the observed page-level top-1 correctness/error offset and q-page vector. This mapping is simulation machinery only and will be reported explicitly.

Report per trial, session and condition: accuracy/delta, all-trial and Context-active gain, accelerated/unchanged/delayed counts, assignment/authorization/application rates, wrong early stops, Context-caused errors and no-delay violations. A condition is supported only with no accuracy loss, zero Context-caused wrong stops, zero no-delay violations, frozen gates and held-out semantic support. No result is called prospective.

## Reproducibility and interpretation

All stochastic transfer mapping uses fixed published seeds. Thresholds are frozen before held-out scoring. No lambda or gate is selected on held-out results. The M25/M23/M24 inputs and all historical analysis outputs remain read-only. Performance claims must retain actual negative or null results and separate measured semantic quality from synthetic transfer assumptions.
"""
    spec = spec.replace("LAMBDA_SWEEP_PLACEHOLDER", "`0, 0.5, 1.0, 1.5, 2.0`")
    _write_once("M30_EXPERIMENT_SPEC.md", spec)
    print(json.dumps({"status": "PASS", "benchmarkSha256": validation["sha256"], "staticArtifactsWritten": 3}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
