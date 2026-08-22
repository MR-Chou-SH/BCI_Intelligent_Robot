# M7 Unity 6000 provenance

`m7_unity6000/` is the active Unity application for M7+ within the single `BCI_Intelligent_Robot` Git repository. It is not a nested Git repository and it does not replace the M1–M6 legacy Unity project at `../vr_stimulus/`.

## Upstream baseline

- Repository: `https://github.com/oculus-samples/Unity-PassthroughCameraApiSamples`
- Upstream commit: `9105be64da8690b41154baf5629cb82dc2dbe4a7`
- Unity: `6000.0.66f2`
- MRUK / Meta Core: `85.0.0`

The upstream project supplies Passthrough Camera API access, `CameraToWorld`, `MultiObjectDetection`, and the MRUK environment-raycast path. Its source licence is retained in `LICENSE.txt`; the separately marked licence inside `Assets/PassthroughCameraApiSamples/LICENSE.txt` is retained with the corresponding assets.

## BCI-local M7.5 state imported with this working tree

This directory was imported from the verified upstream working tree, not from a clean upstream checkout. The BCI-local changes retained here are limited to the M7.5 validation setup:

- required Oculus / OpenXR / XR / Android project settings for passthrough and headset-camera access;
- visible detection-spawn marker prefab and materials;
- localization diagnostics plus the 1.5-second recent-detection hold used to make manual validation practical; and
- `Assets/Editor/M7OfficialLocalizationBuild.cs`, the command-line Android validation build entry point.

Quest 3 M7.5 acceptance passed for `bbox center → PassthroughCameraAccess.ViewportPointToRay → EnvironmentRaycastManager.Raycast → world marker`. This does not validate StableTarget binding, SSVEP selection, EEG or robot control.

## Scope boundary

Do not import the separate migration worktree wholesale. In particular, M7.4 RGB→Environment Depth UV experiments, old YOLO26n integration, experimental localization scenes, and generated Unity caches are not part of this module. Future M7 work starts here and selectively reuses legacy SSVEP code only when a direct binding requirement exists.
