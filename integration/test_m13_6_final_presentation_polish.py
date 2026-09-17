import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity"
BOOTSTRAP = ROOT / (
    "m7_unity6000/Assets/PassthroughCameraApiSamples/"
    "MultiObjectDetection/DetectionManager/Scripts/M9VirtualManipulationBootstrap.cs"
)
BINDING = ROOT / "m7_unity6000/Assets/BCI/TargetBinding/BciSsvepTargetBinding.cs"
CONTROLLER = ROOT / "m7_unity6000/Assets/BCI/Integration/BciTargetBatchController.cs"
RECEIVER = ROOT / (
    "m7_unity6000/Assets/PassthroughCameraApiSamples/"
    "MultiObjectDetection/DetectionManager/Scripts/M13_6VisualSyncReceiver.cs"
)


class M136FinalPresentationPolishTests(unittest.TestCase):
    def test_viewpoint_uses_runtime_camera_workspace_authority(self):
        scene = SCENE.read_text(encoding="utf-8")
        bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertEqual(scene.count("m_Name: '[BuildingBlock] Camera Rig'"), 1)
        camera_block = scene.split("m_Name: '[BuildingBlock] Camera Rig'", 1)[1]
        self.assertIn("m_IsActive: 1", camera_block.split("--- !u!4", 1)[0])
        self.assertIn("Camera.main", bootstrap)
        self.assertIn(
            "Vector3 workspacePosition = headPosition + forward * WorkspaceDistanceMeters;",
            bootstrap,
        )
        self.assertIn(
            "Quaternion workspaceRotation = Quaternion.LookRotation(-forward, Vector3.up);",
            bootstrap,
        )

    def test_startup_blocks_use_m136_canonical_visual_size(self):
        bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn("M13_6VisualBlockEdgeMeters = 0.056f", bootstrap)
        self.assertIn("block.transform.localScale = M13_6VisualBlockSize", bootstrap)

    def test_visual_execution_uses_one_presentation_lifecycle(self):
        binding = BINDING.read_text(encoding="utf-8")
        controller = CONTROLLER.read_text(encoding="utf-8")
        receiver = RECEIVER.read_text(encoding="utf-8")
        self.assertIn("PresentationLifecycleState", binding)
        self.assertIn("SetExecutionPresentationHidden", binding)
        self.assertIn("SetM13_6ExecutionPresentation", controller)
        self.assertIn("m_batchController.SetM13_6ExecutionPresentation", receiver)
        self.assertIn('"executing"', receiver)
        self.assertIn('"usb_synthetic"', receiver)


if __name__ == "__main__":
    unittest.main()
