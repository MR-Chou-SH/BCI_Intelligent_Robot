# ADR-002: M5 Quest-PC trigger transport

Status: Accepted for M5.2 engineering validation

## Context

M5.2 must deliver the exact M5.1 software stimulus events to a PC, acknowledge
receipt, and estimate the relationship between independent monotonic clocks without
allowing network work to alter the verified frame-driven stimulus.

## Decision

Use one persistent TCP connection with newline-delimited UTF-8 JSON and explicit
application ACKs. Unity performs only low-rate JSON creation and queue insertion on
the main thread; all socket connect/read/write/reconnect work runs on a background
thread. The PC preserves the original Quest event, stamps receive monotonic/UTC
times, validates session sequence, and writes append-only JSONL.

Clock samples use q1 (Quest send), p2 (PC receive), p3 (PC send), and q4 (Quest
receive). The stored offset sign is `PC - Quest`, calculated as
`((p2-q1)+(p3-q4))/2`; RTT is `(q4-q1)-(p3-p2)`. A PC-side least-squares mapping
`PC ~= a*Quest+b` is secondary metadata; every raw sample remains authoritative.

The independent M5.2 scene requests its first trial after a fixed Unity-frame delay.
Connection success is not a condition for starting, stopping, or scheduling stimuli.

## Consequences

TCP and JSON are simple to inspect and sufficient for low-rate engineering events,
but they are not real-time EEG triggers and do not establish physical optical onset.
Real-device LAN, disconnect/reconnect, and timing validation are required before
M5.2 acceptance.
