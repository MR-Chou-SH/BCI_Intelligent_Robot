# 2026-09-26 M19 MuJoCo–Quest Continuous World Fix

## Finding

M19 created one adapter, `MjModel`, and `MjData` for its dispatcher, but left
`persistent_world` disabled. The backend therefore called `mj_resetData` on
that same `MjData` before every selected item. The M19 factory also selected
the legacy heterogeneous M9 scene and omitted M13.6 runtime anchors and the
56 mm Quest visual-size field. The confirmed M16 payload has no physical
placement `buildSlotIndex`, so the existing persistent context path needed to
resolve M19's fixed destinations from logical block identity instead.

## Changes

- M19 now uses the existing M13.6 uniform 80 mm physical cube scene, 0.70
  MuJoCo-to-Quest transform, runtime phase anchors, and canonical 56 mm Quest
  visual size.
- One persistent MuJoCo world serves the entire ordered M19 batch. Each
  logical block has a fixed, non-overlapping destination on the visible table
  row. The destination resolver does not derive identity or placement from an
  EEG slot or invent a `buildSlotIndex`.
- M13.6's existing resting/active collision categories protect released blocks
  and preserve tabletop contact. After release, the existing step callback
  continues at the M19 telemetry cadence for 240 simulation steps before the
  final `completed` frame is emitted.
- Quest startup blocks now use the same 56 mm size and table-supported center
  as the first M13.6 telemetry frame; their frozen horizontal catalog anchors
  are unchanged.

## Verification

The targeted Python/MuJoCo regressions passed: two-item Blue→Green preservation,
three-item Blue→Green→Red final poses and order, first-frame alignment, stable
visual size, post-release tabletop height and low final velocity, M13.6 visual
regressions, persistent-backend regressions, M19 telemetry pacing, and the
synthetic selection-order acceptance.

The Unity EditMode test for the startup pose helper was added but not run. No
Unity instance was started, no Quest build was made, and no Quest, ND8, COM, or
physical hardware was operated. Real Quest visual acceptance of the changed
startup center and continuous batch remains a next-day physical check.
