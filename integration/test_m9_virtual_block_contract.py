"""Software-only checks for the shared Unity virtual-block/M9 identity catalog."""

import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

from integration.m9_robot_adapter import (
    CONFIRMED_BATCH_PROVENANCE,
    FROZEN_SELECTION_PROVENANCE,
    confirmed_batch_to_robot_requests,
)
from integration.m9_mujoco_execution import DEFAULT_M9_SCENE_BINDINGS, SceneBindingRegistry
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
UNITY_CATALOG_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "Resources"
    / "BCI"
    / "M9"
    / "virtual_blocks.json"
)
UNITY_SCENE_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "PassthroughCameraApiSamples"
    / "MultiObjectDetection"
    / "M9VirtualManipulation.unity"
)
UNITY_BUILD_SETTINGS_PATH = REPOSITORY_ROOT / "m7_unity6000" / "ProjectSettings" / "EditorBuildSettings.asset"
UNITY_ORIGINAL_SCENE_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "PassthroughCameraApiSamples"
    / "MultiObjectDetection"
    / "MultiObjectDetection.unity"
)
UNITY_BOOTSTRAP_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "PassthroughCameraApiSamples"
    / "MultiObjectDetection"
    / "DetectionManager"
    / "Scripts"
    / "M9VirtualManipulationBootstrap.cs"
)
UNITY_FRANKA_FACTORY_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "PassthroughCameraApiSamples"
    / "MultiObjectDetection"
    / "DetectionManager"
    / "Scripts"
    / "M9FrankaVisualFactory.cs"
)
UNITY_FRANKA_RIG_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "PassthroughCameraApiSamples"
    / "MultiObjectDetection"
    / "DetectionManager"
    / "Scripts"
    / "M9FrankaVisualRig.cs"
)
UNITY_FRANKA_RUNTIME_TEST_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "Tests"
    / "Editor"
    / "M9FrankaRuntimeVisualTests.cs"
)
UNITY_FRANKA_ASSET_DIRECTORY = (
    REPOSITORY_ROOT / "m7_unity6000" / "Assets" / "Resources" / "M9" / "Franka"
)
MUJOCO_FRANKA_MJCF_PATH = REPOSITORY_ROOT / "robot_arm" / "assets" / "fr3_umi_merged.xml"


def _load_catalog():
    with UNITY_CATALOG_PATH.open("r", encoding="utf-8") as source:
        return json.load(source)


class M9VirtualBlockContractTests(unittest.TestCase):
    def test_catalog_has_explicit_unique_virtual_id_mapping_and_fixed_slots(self):
        catalog = _load_catalog()
        self.assertEqual(1, catalog["schemaVersion"])
        self.assertEqual(
            [(0, 7.2, 5), (1, 9.0, 4), (2, 12.0, 3)],
            [
                (slot["slotIndex"], slot["nominalFrequencyHz"], slot["framesPerHalfCycle"])
                for slot in catalog["slots"]
            ],
        )
        self.assertEqual(4, len(catalog["blocks"]))
        self.assertEqual(
            {"x": 0.085, "y": 0.085, "z": 0.085},
            catalog["blockSizeMeters"],
        )

        target_ids = [block["targetId"] for block in catalog["blocks"]]
        logical_ids = [block["logicalBlockId"] for block in catalog["blocks"]]
        self.assertEqual(len(target_ids), len(set(target_ids)))
        self.assertEqual(len(logical_ids), len(set(logical_ids)))
        for block in catalog["blocks"]:
            self.assertEqual("virtual_block", block["sourceKind"])
            self.assertTrue(block["active"])
            self.assertTrue(block["selectable"])
            self.assertTrue(block["slotEligible"])
            self.assertTrue(block["targetId"].startswith("m9-vblock-"))
            self.assertFalse(block["targetId"].startswith("obj_"))
            self.assertTrue(block["logicalBlockId"].startswith("block_"))
            self.assertFalse(block["logicalBlockId"].startswith("obj_"))

    def test_synthetic_frozen_class_batch_maps_only_through_explicit_catalog(self):
        catalog = _load_catalog()
        blocks = sorted(
            (
                block
                for block in catalog["blocks"]
                if block["active"] and block["selectable"] and block["slotEligible"]
            ),
            key=lambda block: (block["localPositionMeters"]["x"], block["targetId"]),
        )
        self.assertGreaterEqual(len(blocks), 3)
        target_mapping = load_virtual_block_target_mapping()
        self.assertEqual(
            {block["targetId"]: block["logicalBlockId"] for block in catalog["blocks"]},
            target_mapping,
        )
        selections = [
            {
                "selectionId": "m9-virtual-test-selection-{}".format(slot),
                "predictedClassIndex": slot,
                "slotIndex": slot,
                "targetId": block["targetId"],
                "semanticLabel": block["semanticLabel"],
                "hasWorldPosition": True,
                "worldPosition": block["localPositionMeters"],
                "resolvedUtc": "2026-09-14T00:00:00Z",
                "provenance": FROZEN_SELECTION_PROVENANCE,
            }
            for slot, block in enumerate(blocks[:3])
        ]
        message = {
            "protocolVersion": 1,
            "messageType": "target_batch_confirmed",
            "batchId": "m9-virtual-test-batch-001",
            "confirmedBatch": {
                "batchId": "m9-virtual-test-batch-001",
                "groupId": "m8-group-virtual-test",
                "groupIndex": 1,
                "submittedUtc": "2026-09-14T00:00:01Z",
                "provenance": CONFIRMED_BATCH_PROVENANCE,
                "selections": selections,
            },
        }

        requests = confirmed_batch_to_robot_requests(message, target_mapping)
        self.assertEqual(3, len(requests))
        self.assertEqual(
            tuple(block["logicalBlockId"] for block in blocks[:3]),
            tuple(request.logical_block_id for request in requests),
        )
        self.assertEqual(
            tuple(block["targetId"] for block in blocks[:3]),
            tuple(request.source_target_id for request in requests),
        )
        self.assertEqual((0, 1, 2), tuple(request.slot_index for request in requests))
        self.assertTrue(
            all(request.selection_provenance == FROZEN_SELECTION_PROVENANCE for request in requests)
        )
        self.assertTrue(
            all(request.batch_provenance == CONFIRMED_BATCH_PROVENANCE for request in requests)
        )

        scene_bindings = SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS)
        self.assertEqual(set(DEFAULT_M9_SCENE_BINDINGS), set(target_mapping.values()))
        self.assertEqual(
            ("obj_0", "obj_1", "obj_2"),
            tuple(
                scene_bindings.resolve_binding(request.logical_block_id).simulator_object_name
                for request in requests
            ),
        )

        with self.assertRaisesRegex(ValueError, "not registered"):
            confirmed_batch_to_robot_requests(message, {"different-target": "block_red_01"})

    def test_dedicated_default_scene_keeps_real_target_scene_and_disables_real_detector_roots(self):
        scene = UNITY_SCENE_PATH.read_text(encoding="utf-8")
        settings = UNITY_BUILD_SETTINGS_PATH.read_text(encoding="utf-8")
        self.assertIn("guid: 03b7794c49a34c82bc0358c66c3da451", scene)
        self.assertIn("m_Name: M9VirtualManipulationBootstrap", scene)
        self.assertIn("- {fileID: 905041112}", scene.split("SceneRoots:", 1)[1])
        for prefab_instance, target_id, prefab_guid in (
            ("1721288808", "5464317018167149569", "749c2b64d9125458da0686ace4bec00b"),
            ("1019656467722773290", "8722207748505512710", "259c0c899b7e0c14aa431eefdc0aacc6"),
            ("7042133910910641334", "7042133912099342345", "946c4bcf5ee78c2438bd71dd2f9fe1f9"),
            ("7177215558053442464", "5228241647023865434", "927c1b782bb796b4b9b433d533cbccca"),
            ("1373946500", "7847449403238870662", "abfaad578d05e4a09838223134c41c46"),
        ):
            marker = "--- !u!1001 &{}".format(prefab_instance)
            section = scene.split(marker, 1)[1].split("--- !u!", 1)[0]
            self.assertIn(
                "target: {{fileID: {}, guid: {}, type: 3}}".format(target_id, prefab_guid),
                section,
            )
            self.assertRegex(section, r"propertyPath: m_IsActive\s+value: 0")

        scene_entries = [
            (enabled, path)
            for enabled, path in re.findall(r"- enabled: ([01])\s+path: ([^\r\n]+)", settings)
        ]
        self.assertEqual(
            ("1", "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity"),
            scene_entries[0],
        )
        self.assertIn(
            ("1", "Assets/PassthroughCameraApiSamples/MultiObjectDetection/MultiObjectDetection.unity"),
            scene_entries,
        )

    def test_m9_is_fully_virtual_while_m7_passthrough_stays_available(self):
        m9_scene = UNITY_SCENE_PATH.read_text(encoding="utf-8")
        m7_scene = UNITY_ORIGINAL_SCENE_PATH.read_text(encoding="utf-8")

        passthrough_name = "m_Name: '[BuildingBlock] Passthrough'"
        m9_object_start = m9_scene.rfind("--- !u!1 &", 0, m9_scene.index(passthrough_name))
        m9_object_end = m9_scene.find("--- !u!", m9_scene.index(passthrough_name))
        m9_passthrough_object = m9_scene[m9_object_start:m9_object_end]
        self.assertRegex(m9_passthrough_object, r"m_IsActive:\s+0")
        self.assertIn("isInsightPassthroughEnabled: 0", m9_scene)
        self.assertIn("_trackingOriginType: 1", m9_scene)
        self.assertIn("m_BackGroundColor: {r: 0.3, g: 0.37, b: 0.44, a: 1}", m9_scene)

        prefab_instances = re.findall(
            r"(?ms)^--- !u!1001 .*?(?=^--- !u!|\Z)", m9_scene
        )
        for prefab_name in (
            "DetectionManagerPrefab",
            "DetectionUiMenuPrefab",
            "SentisInferenceManagerPrefab",
            "PassthroughCameraAccessPrefab",
            "EnvironmentRaycastPrefab",
            "ReturnToStartScene",
        ):
            matching_instances = [
                block
                for block in prefab_instances
                if re.search(
                    r"propertyPath: m_Name\s+value: " + re.escape(prefab_name),
                    block,
                )
            ]
            self.assertEqual(1, len(matching_instances), prefab_name)
            self.assertRegex(
                matching_instances[0],
                r"propertyPath: m_IsActive\s+value: 0",
            )

        m7_object_start = m7_scene.rfind("--- !u!1 &", 0, m7_scene.index(passthrough_name))
        m7_object_end = m7_scene.find("--- !u!", m7_scene.index(passthrough_name))
        m7_passthrough_object = m7_scene[m7_object_start:m7_object_end]
        self.assertRegex(m7_passthrough_object, r"m_IsActive:\s+1")
        self.assertIn("isInsightPassthroughEnabled: 1", m7_scene)
        m7_return_to_start = [
            block
            for block in re.findall(r"(?ms)^--- !u!1001 .*?(?=^--- !u!|\Z)", m7_scene)
            if re.search(
                r"propertyPath: m_Name\s+value: ReturnToStartScene",
                block,
            )
        ]
        self.assertEqual(1, len(m7_return_to_start))
        self.assertNotRegex(m7_return_to_start[0], r"propertyPath: m_IsActive\s+value: 0")

    def test_m9_workspace_and_franka_visual_assets_keep_explicit_runtime_boundaries(self):
        bootstrap = UNITY_BOOTSTRAP_PATH.read_text(encoding="utf-8")
        factory = UNITY_FRANKA_FACTORY_PATH.read_text(encoding="utf-8")
        rig = UNITY_FRANKA_RIG_PATH.read_text(encoding="utf-8")
        asset_directory = UNITY_FRANKA_ASSET_DIRECTORY / "Meshes"

        self.assertIn("WorkspaceDistanceMeters = 1.45f", bootstrap)
        self.assertIn("workspacePosition.y = headPosition.y - TableSurfaceDropBelowHeadMeters", bootstrap)
        self.assertIn("CreateWoodTable(catalog)", bootstrap)
        self.assertIn("CreateVirtualRoom()", bootstrap)
        self.assertIn("VirtualFloorY = 0f", bootstrap)
        self.assertIn("InstructionTextLocalPosition", bootstrap)
        self.assertIn("InstructionTextLocalEulerAngles", bootstrap)
        self.assertIn("ConfigureVirtualBackground(viewCamera)", bootstrap)
        self.assertIn('new GameObject("M9WorkspaceRoot")', bootstrap)
        self.assertIn('CreateWorkspaceChild("Table")', bootstrap)
        self.assertIn('CreateWorkspaceChild("Blocks")', bootstrap)
        self.assertIn("block.transform.SetParent(m_blocksRoot, false)", bootstrap)
        self.assertIn("M13_6VisualBlockEdgeMeters = 0.056f", bootstrap)
        self.assertIn("block.transform.localScale = M13_6VisualBlockSize", bootstrap)
        self.assertIn('SampleNavigationRootName = "ReturnToStartScene"', bootstrap)
        self.assertIn("DisableM9SampleNavigation()", bootstrap)
        self.assertIn("sampleNavigation.SetActive(false)", bootstrap)
        self.assertIn("M9FrankaVisualFactory.Create(m_leftRobotAnchor", bootstrap)
        self.assertIn('Shader.Find("Standard") ?? Shader.Find("Unlit/Color")', bootstrap)

        for link in range(8):
            self.assertIn('"fr3_link{}"'.format(link), factory)
        for joint in range(1, 8):
            self.assertIn('"fr3_joint{}"'.format(joint), factory)
        self.assertIn('"umi_umi_gripper_base"', factory)
        self.assertIn('"umi_left_finger_joint"', factory)
        self.assertIn('"umi_right_finger_joint"', factory)
        self.assertIn("Resources.Load<GameObject>", factory)
        self.assertIn("TrySetJointValue", rig)

        mjcf = ET.parse(MUJOCO_FRANKA_MJCF_PATH).getroot()
        expected_mesh_names = set()
        for mesh in mjcf.findall("./asset/mesh"):
            mesh_name = mesh.attrib["name"]
            if mesh.attrib.get("content_type") == "model/obj":
                expected_mesh_names.add(Path(mesh.attrib["file"]).stem)
            elif mesh.attrib.get("content_type") == "model/stl" and mesh_name.startswith("umi_"):
                expected_mesh_names.add(mesh_name)
        declared_meshes = re.search(
            r"RequiredMeshes\s*=\s*\{(.*?)\};", factory, re.DOTALL
        )
        self.assertIsNotNone(declared_meshes)
        self.assertEqual(
            expected_mesh_names,
            set(re.findall(r'"([a-z0-9_]+)"', declared_meshes.group(1))),
        )
        mesh_paths = list(asset_directory.glob("*.obj"))
        self.assertEqual(expected_mesh_names, {path.stem for path in mesh_paths})
        for mesh_path in mesh_paths:
            with mesh_path.open("r", encoding="utf-8") as mesh_file:
                mesh_lines = mesh_file.readlines()
            self.assertTrue(any(line.startswith("f ") for line in mesh_lines), mesh_path.name)
            self.assertFalse(any(line.startswith(("mtllib ", "usemtl ")) for line in mesh_lines))
        self.assertTrue((UNITY_FRANKA_ASSET_DIRECTORY / "Licenses" / "franka_fr3_APACHE-2.0.txt").is_file())
        self.assertTrue((UNITY_FRANKA_ASSET_DIRECTORY / "Licenses" / "umi_gripper_MIT.txt").is_file())

    def test_m9_franka_mesh_frames_scale_and_dual_arm_anchors_are_explicit(self):
        bootstrap = UNITY_BOOTSTRAP_PATH.read_text(encoding="utf-8")
        factory = UNITY_FRANKA_FACTORY_PATH.read_text(encoding="utf-8")

        self.assertIn('CreateWorkspaceChild("LeftRobotAnchor")', bootstrap)
        self.assertIn('CreateWorkspaceChild("RightRobotAnchor")', bootstrap)
        self.assertIn("M13_6RobotBaseYMeters = 0.40f", bootstrap)
        self.assertIn("M13_6RobotBaseZMeters = 0.75f", bootstrap)
        self.assertIn("M13_6RobotBaseAnchorLocalPosition()", bootstrap)
        self.assertIn("M9FrankaVisualFactory.Create(m_leftRobotAnchor", bootstrap)
        self.assertNotIn("M9FrankaVisualFactory.Create(m_rightRobotAnchor", bootstrap)

        self.assertIn("private const float VisualScale = 0.7f", factory)
        self.assertIn("root.transform.localPosition = Vector3.zero", factory)
        self.assertIn("root.transform.localScale = Vector3.one * VisualScale", factory)
        self.assertIn('new GameObject(objectName + "_GeometryFrame")', factory)
        self.assertIn("geometryFrame.localPosition = position", factory)
        self.assertIn("geometryFrame.localRotation = rotation", factory)
        self.assertIn("Vector3 ImportedObjAxisCorrection", factory)
        self.assertIn("geometryFrame.localScale = Vector3.Scale(scale, ImportedObjAxisCorrection)", factory)
        self.assertIn("Instantiate(meshes[resourceName], geometryFrame, false)", factory)
        instantiated_mesh_setup = factory.split(
            "GameObject instance = UnityEngine.Object.Instantiate(meshes[resourceName], geometryFrame, false);",
            1,
        )[1].split("Renderer[] renderers", 1)[0]
        self.assertNotRegex(instantiated_mesh_setup, r"instance\.transform\.local(?:Position|Rotation|Scale)")

    def test_m9_bootstrap_waits_for_tracked_hmd_and_emits_one_shot_spatial_diagnostics(self):
        bootstrap = UNITY_BOOTSTRAP_PATH.read_text(encoding="utf-8")

        self.assertIn("private IEnumerator Start()", bootstrap)
        self.assertIn("WaitForInitialHeadPose", bootstrap)
        self.assertIn("XRSettings.isDeviceActive", bootstrap)
        self.assertIn("InputDevices.GetDeviceAtXRNode(XRNode.Head)", bootstrap)
        self.assertIn("CommonUsages.isTracked", bootstrap)
        self.assertIn("CommonUsages.devicePosition", bootstrap)
        self.assertIn("CommonUsages.deviceRotation", bootstrap)
        self.assertIn("Time.realtimeSinceStartup - waitStartedAt < InitialPoseTimeoutSeconds", bootstrap)
        self.assertIn("stablePoseFrames >= 2", bootstrap)
        self.assertIn("HorizontalForward(viewCamera.transform.forward)", bootstrap)
        self.assertIn("xr_head_device_camera_world", bootstrap)
        self.assertIn('new GameObject("M9WorkspaceRoot")', bootstrap)
        self.assertIn("M9_SPATIAL startup", bootstrap)
        self.assertIn("viewCamera.cullingMask", bootstrap)
        self.assertIn("renderer.sharedMaterial.shader.isSupported", bootstrap)
        self.assertIn("renderer.enabled && renderer.gameObject.activeInHierarchy", bootstrap)
        self.assertIn("nearClipPlane", bootstrap)
        self.assertIn("farClipPlane", bootstrap)

    def test_m9_instruction_text_is_unbacked_floor_text_with_explicit_front_face(self):
        bootstrap = UNITY_BOOTSTRAP_PATH.read_text(encoding="utf-8")
        factory = UNITY_FRANKA_FACTORY_PATH.read_text(encoding="utf-8")
        runtime_test = UNITY_FRANKA_RUNTIME_TEST_PATH.read_text(encoding="utf-8")

        self.assertNotIn("InstructionPanel", bootstrap)
        self.assertIn("CreateFloorInstructionText()", bootstrap)
        self.assertIn("InstructionTextLocalPosition", bootstrap)
        self.assertIn("new Vector3(0f, -0.985f, 0.4f)", bootstrap)
        self.assertIn("InstructionTextLocalEulerAngles", bootstrap)
        self.assertIn("new Vector3(80f, 180f, 0f)", bootstrap)
        self.assertIn('new GameObject("InstructionText")', bootstrap)
        self.assertIn("labelObject.transform.SetParent(m_workspaceRoot, false)", bootstrap)
        self.assertIn("labelObject.transform.localPosition = InstructionTextLocalPosition", bootstrap)
        self.assertIn("labelObject.transform.localRotation = Quaternion.Euler(InstructionTextLocalEulerAngles)", bootstrap)
        self.assertIn("labelObject.transform.localScale = Vector3.one", bootstrap)
        create_text = bootstrap.split("private void CreateFloorInstructionText", 1)[1].split(
            "private void ConfigureVirtualBackground", 1
        )[0]
        self.assertNotIn("CreatePrimitive", create_text)
        self.assertNotIn("AddComponent<Renderer>", create_text)
        self.assertNotIn("SetRendererColor", create_text)
        self.assertIn("instruction_text_world_forward", bootstrap)
        self.assertIn("instruction_text_normal_up_dot", bootstrap)
        self.assertIn("instruction_text_glyph_right_dot_camera", bootstrap)
        self.assertIn("M9FrankaVisualFactory.LogTransformDiagnostics(robot.transform)", bootstrap)
        self.assertIn("M9_FRANKA_XFORM", factory)
        self.assertIn("M9_FRANKA_RUNTIME", factory)
        self.assertIn("robot_local_position_unscaled", factory)
        self.assertIn("joint_local_position", factory)
        self.assertIn("import_local_position", factory)
        self.assertIn("renderer_bounds", factory)
        self.assertIn("renderer_path=", factory)
        self.assertIn("parent_chain=", factory)
        self.assertIn("vertex_count=", factory)
        self.assertIn("mesh_bounds=", factory)
        self.assertIn("active_in_hierarchy=", factory)
        self.assertIn("materials=", factory)

        self.assertIn("EveryLicensedVisualInstantiatesUnderItsBodyWithAnEnabledMeshRenderer", runtime_test)
        self.assertIn("OldFoldedPoseDoesNotReparentOrDisableTheRenderedChain", runtime_test)
        self.assertIn("importedRoot.localPosition, Is.EqualTo(Vector3.zero)", runtime_test)
        self.assertIn("importedRoot.localRotation, Is.EqualTo(Quaternion.identity)", runtime_test)
        self.assertIn("importedRoot.localScale, Is.EqualTo(Vector3.one)", runtime_test)
        self.assertIn("filter.sharedMesh.vertexCount, Is.GreaterThan(0)", runtime_test)

        expected_pregrasp_pose = (
            "0.084207f", "-1.087620f", "1.221978f", "-2.349414f",
            "0.992534f", "1.681765f", "0.822657f",
        )
        self.assertEqual(
            expected_pregrasp_pose,
            tuple(
                value
                for _, value in re.findall(
                    r"const float joint([1-7])PoseRadians = ([+-]?[0-9.]+f);",
                    factory,
                )
            ),
        )
        self.assertIn("6-DOF IK utility for a robot-local tabletop pre-grasp target", factory)

        # M9.4 is a HUD cleanup; it must preserve the reviewed M9.3 layout.
        self.assertIn("WorkspaceDistanceMeters = 1.45f", bootstrap)
        self.assertIn("TableSurfaceDropBelowHeadMeters = 0.55f", bootstrap)
        self.assertIn("M13_6RobotBaseYMeters = 0.40f", bootstrap)
        self.assertIn("M13_6RobotBaseZMeters = 0.75f", bootstrap)
        self.assertIn("private const float VisualScale = 0.7f", factory)


if __name__ == "__main__":
    unittest.main()
