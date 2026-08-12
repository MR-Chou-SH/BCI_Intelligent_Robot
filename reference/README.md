# Reference Materials

This directory contains legacy code, SDKs and documents used only as reference material.

The materials were selectively copied from:

`C:\Users\zsh21\Desktop\EEG_back_up`

The original source directory was not modified.

## Important Rule

Files under `reference/` are read-only by default.

New production code should not be implemented directly inside these legacy projects unless explicitly requested.

## Main Sources

### Neurodance

Contains Neurodance ND8 related:

- Python SDK
- SDK examples
- documentation

Important for understanding:

- device communication;
- EEG acquisition;
- data structures;
- timestamped EEG reading.

### Drone Demo

Contains the previous EEG-controlled drone implementation.

Important source files include:

- `OperationMain.py`
- `Drone_psycho.py`
- `ND8.py`
- `spatialFilter.py`
- `RoboMasterThread2.py`
- `Config.py`
- `wheel_core.py`

`pics2/` was not found within the specified `EEG_back_up` scan scope. No search of other directories is being performed now. Its status is “to be confirmed if needed”; if later analysis of `Drone_psycho.py` confirms that the resource is required, its original location should then be identified.

The known high-level pipeline is:

`visual stimulus → ND8 EEG → preprocessing → FBCCA → class result → drone command`

The demo is used to understand the working BCI pipeline before designing the new robotic-arm system.

## Excluded Material

The complete packaged runtime was not copied. Large generated runtimes, duplicated third-party Python libraries, executables, installers and unrelated packaged dependencies were excluded unless specifically needed as reviewed reference material.
