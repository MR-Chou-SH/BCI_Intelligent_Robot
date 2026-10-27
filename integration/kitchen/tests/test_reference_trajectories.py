"""Small golden contract for the handoff, not full physical task validation."""
import json
import unittest

from integration.kitchen.planning import reference_trajectories as ref


class ReferenceContract(unittest.TestCase):
    def test_file_hashes_targets_and_control_clocks(self):
        result = ref.inspect(ref.ASSETS)
        self.assertEqual(result["apple_frames"], 6294)
        self.assertEqual(result["burger_frames"], 3218)
        self.assertEqual(result["burger_ticks"], 64360)
        manifest = ref.load(ref.ASSETS/"manifest.json")
        self.assertEqual(ref.sha(ref.ASSETS/"historical_source/reference_extractor_at_export.py"),
                         manifest["extraction_tool_sha256"])
        sources = ref.load(ref.ASSETS/"generator_sources.json")
        for source in sources["sources"]:
            self.assertEqual(ref.sha(ref.ASSETS/"historical_source"/source["filename"]),
                             source["published_sha256"])

    def test_no_historical_pass_promotion(self):
        apple = ref.load(ref.ASSETS/"apple_metadata.json")
        burger = ref.load(ref.ASSETS/"burger_metadata.json")
        self.assertEqual(apple["public_api_full_cycle"], "NOT_VERIFIED")
        self.assertEqual(apple["extracted_full_reexecution"], "NOT_TESTED")
        self.assertEqual(burger["extracted_full_reexecution"], "NOT_TESTED")
        self.assertEqual(burger["final_scope"], "HELD_OUTSIDE_NOT_TABLE_RELEASE_OR_TRAY_DELIVERY")
        self.assertNotEqual(burger["source_control_hash"], burger["control_hash"])

    def test_contact_and_transport_remain_separate(self):
        with (ref.ASSETS/"burger_public_calls.jsonl").open(encoding="utf-8") as stream:
            calls = [json.loads(line) for line in stream]
        phases = set()
        for call in calls:
            self.assertEqual(set(call), {"method", "args", "start_tick", "end_tick"})
            self.assertNotIn("token", call["args"])
            self.assertNotIn("writer_id", call["args"])
            contract = call["args"].get("contact_contract")
            if contract:
                phases.add(contract["phase"])
        self.assertEqual(phases, {"acquire", "carry", "device", "release"})
        self.assertEqual(calls[-1]["method"], "hold")

    def test_local_randomization_has_new_identity_without_relabeling_references(self):
        from robot_arm.kitchen.registry import control_identity
        from robot_arm.kitchen import research_initialization
        identity = control_identity()
        self.assertNotEqual(identity["control_hash"], ref.CONTROL)
        self.assertEqual(identity["configuration"]["sources"]["research_initialization.py"],
                         ref.sha(research_initialization.__file__))
        for name in ("apple", "burger"):
            self.assertEqual(ref.load(ref.ASSETS / (name + "_metadata.json"))["control_hash"], ref.CONTROL)


if __name__ == "__main__":
    unittest.main()
