# M8.1 Quest Selection Transport Closeout

Date: 2026-08-23

## Result

M8.1 is completed / PASS on Quest 3. The PC supplied canonical class indices only; Quest resolved each decision through its own frozen `BciSelectionSnapshot`.

- class 0 → slot 0 → `target-0001`;
- class 1 → slot 1 → `target-0007`;
- class 2 → slot 2 → `target-0005`;
- duplicate decision → `DuplicateDecision`;
- unknown selection ID → `UnknownSelectionId`.

## Android TCP lessons

The Quest transport now treats `Read() == 0` as remote EOF and enters its reconnect path. Android may report an idle read as `SocketError.WouldBlock`; this is treated equivalently to the configured short read timeout, with a bounded worker wait to avoid busy-spin. It is not a replacement for real socket failure handling.

M7 passthrough, StableTarget and the fixed three-slot SSVEP pipeline remained operational throughout acceptance. This evidence does not start real ND8 online integration, establish EEG/optical timing, or authorize robot control.
