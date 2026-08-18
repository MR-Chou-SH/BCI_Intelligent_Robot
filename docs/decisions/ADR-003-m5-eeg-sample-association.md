# ADR-003: M5 EEG sample association evidence hierarchy

Status: Accepted for M5.3 implementation

## Decision

M5.3 preserves raw EEG packet metadata, Quest-PC stimulus events, and derived
association records independently. It maps an estimated PC-domain stimulus time to
an EEG sample only through an explicit evidence hierarchy: verified hardware sample
counter first, verified device-timestamp-to-PC mapping second, then PC packet-receive
time as a lower-confidence fallback.

The legacy Drone2.1 code parses a packet timestamp and treats it as a first-sample
millisecond timestamp. That is an unverified legacy assumption, not an ND8 protocol
fact. It is therefore never silently promoted to verified timing in M5.3.

## Consequences

Every result records mapping method, quality, continuity state, uncertainty, and
whether hardware timing was verified. M5.3 does not classify EEG or alter raw EEG.
Live ND8 validation is required before any device-timestamp result can be considered
hardware-timed.

## Post-sync association gate

The SDK can transition from an initial non-Unix timestamp domain to a stable
Unix-millisecond domain during startup. Initial pre-sync packets are therefore
excluded from formal sample association. A trial may begin only after the adapter
has observed and recorded the stable Unix-millisecond post-sync segment. This gate
validates a software-level timestamp mapping only; it does not verify hardware
timing, physical optical onset, or physical optical phase.

## Operational runtime prerequisite

The vendor ND8 SDK is executed only from an isolated, project-external Windows x64
CPython 3.9 environment. Its native `core.pyd` is CPython-3.9-ABI-specific; it is
not copied into the repository or mixed with the project's other Python runtimes.
The environment includes the original vendor wheel, `pyserial==3.5`, and NumPy.
Adapter code must remain compatible with CPython 3.9. Import success is a software
environment check only and does not validate serial access, EEG streaming, packet
timestamp semantics, or hardware sample timing.
