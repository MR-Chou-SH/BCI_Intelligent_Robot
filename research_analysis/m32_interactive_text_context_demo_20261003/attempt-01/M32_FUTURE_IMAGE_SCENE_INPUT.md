# Future Image-to-Structured-Scene Input

Image understanding is not implemented in M32. A future vision frontend should produce the same structured scene consumed by `SemanticContextEngine`, without changing the Context API:

```text
camera/image
  -> detector or vision-language perception
  -> tracked object observations
  -> Structured Scene
  -> M30 SemanticContextEngine
  -> q_global
```

## Suggested observation fields

- `track_id`: stable ID for one object across consecutive images;
- `display_name`, `object_type`, and aliases;
- `bounding_box` or 3D pose when available;
- explicitly observed `color` and `current_state`, each with source/confidence metadata;
- `selectable` and any UI-facing occlusion/visibility state;
- perception confidence for object identity, attributes and pose as separate values;
- observation timestamp and sensor/frame identifier.

## Interface and uncertainty

An `ImageSceneParser` or detector adapter should return a versioned structured-scene object with stable IDs and explicit evidence. The M30 engine should receive only the normalized scene, accepted selection history and remaining candidates. It should not depend on image bytes, camera SDKs, or a specific detector.

Perception confidence must remain separate from semantic Context score and q. A low-confidence color should not become an observed fact; unknown values should remain absent or carry an explicit uncertain observation record. The UI can request human confirmation for ambiguous detections rather than silently collapsing them.

## Object tracking and ID stability

IDs should persist while a tracked object remains the same physical instance, even if its label or bounding box shifts between frames. Track merges/splits, long occlusion, and re-identification should be explicit events. A fresh scene parse may create a new scene ID, but stable track IDs should be reused only when the vision layer has sufficient evidence. Selection history must be remapped or invalidated when identity is uncertain.

No image API, detector, camera access, Quest integration or tracking implementation is part of M32.
