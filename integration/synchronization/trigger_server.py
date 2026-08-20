import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .clock_sync import AffineClockMapper
from .event_log import AppendOnlyJsonl
from .protocol import PROTOCOL_VERSION, SequenceTracker, encode_line, validate_stimulus_event


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class TriggerServer:
    def __init__(self, log_directory, event_observer=None, dataset_plan=None):
        directory = Path(log_directory)
        self.events = AppendOnlyJsonl(directory / "pc-stimulus-events.jsonl")
        self.diagnostics = AppendOnlyJsonl(directory / "pc-synchronization.jsonl")
        self.sequences = SequenceTracker()
        self.mappers = {}
        self.sync_state = {}
        self.writers = {}
        self.event_observer = event_observer
        self.dataset_plan = dataset_plan

    async def handle_connection(self, reader, writer):
        connection_id = uuid.uuid4().hex
        self.mappers[connection_id] = AffineClockMapper()
        self.sync_state[connection_id] = {"latestAcceptedPcMonotonicNs": None}
        peer = str(writer.get_extra_info("peername"))
        self.diagnostics.append({"recordType": "connection_opened", "connectionId": connection_id,
                                 "peer": peer, "pcUtc": utc_now()})
        try:
            self.writers[connection_id] = writer
            if self.dataset_plan is not None:
                await self._send_dataset_plan(writer, connection_id, self.dataset_plan)
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                p2_ns = time.perf_counter_ns()
                p2_utc = utc_now()
                if len(raw) > 1_048_576:
                    self.diagnostics.append({"recordType": "malformed_message", "connectionId": connection_id,
                                             "pcReceiveMonotonicNs": p2_ns, "error": "line_too_long"})
                    continue
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    self.diagnostics.append({"recordType": "malformed_message", "connectionId": connection_id,
                                             "pcReceiveMonotonicNs": p2_ns, "error": str(error)})
                    continue
                message_type = message.get("messageType")
                try:
                    if message_type == "stimulus_event":
                        await self._handle_event(message, writer, connection_id, p2_ns, p2_utc)
                    elif message_type == "clock_sync_request":
                        await self._handle_sync_request(message, writer, connection_id, p2_ns)
                    elif message_type == "clock_sync_result":
                        self._handle_sync_result(message, connection_id, p2_ns)
                    else:
                        raise ValueError("unexpected_message_type")
                except (KeyError, TypeError, ValueError) as error:
                    self.diagnostics.append({"recordType": "rejected_message", "connectionId": connection_id,
                                             "pcReceiveMonotonicNs": p2_ns, "error": str(error)})
        except (ConnectionError, asyncio.IncompleteReadError) as error:
            self.diagnostics.append({"recordType": "connection_error", "connectionId": connection_id,
                                     "pcUtc": utc_now(), "error": str(error)})
        finally:
            self.writers.pop(connection_id, None)
            writer.close()
            await writer.wait_closed()
            self.diagnostics.append({"recordType": "connection_closed", "connectionId": connection_id,
                                     "pcUtc": utc_now()})

    async def _send_dataset_plan(self, writer, connection_id, dataset_plan):
        plan_message = {"protocolVersion": PROTOCOL_VERSION, "messageType": "dataset_session_plan", **dataset_plan}
        writer.write(encode_line(plan_message))
        await writer.drain()
        trials = plan_message.get("trials", [])
        counts = {target: sum(item.get("targetId") == target for item in trials)
                  for target in ("target_left", "target_center", "target_right")}
        self.diagnostics.append({"recordType": "dataset_session_plan_sent", "connectionId": connection_id,
                                 "sessionId": plan_message.get("sessionId"), "trialCount": len(trials),
                                 "classCounts": counts, "pcUtc": utc_now()})

    async def broadcast_dataset_plan(self, dataset_plan):
        """Send an explicitly approved plan only after the live preflight completes."""
        if not self.writers:
            raise RuntimeError("no Quest connection is available for dataset plan")
        self.dataset_plan = dataset_plan
        await asyncio.gather(*(self._send_dataset_plan(writer, connection_id, dataset_plan)
                               for connection_id, writer in list(self.writers.items())))

    async def _handle_event(self, message, writer, connection_id, p2_ns, p2_utc):
        validation = validate_stimulus_event(message)
        event = message.get("eventPayload") if isinstance(message.get("eventPayload"), dict) else {}
        session_id = event.get("sessionId", "")
        sequence = event.get("sequence", -1)
        sequence_result = (self.sequences.observe(session_id, sequence)
                           if validation == "valid" else None)
        mapper = self.mappers[connection_id]
        estimate = mapper.map(event.get("questMonotonicSeconds", 0.0)) if validation == "valid" else None
        clock_sync = self._clock_snapshot(connection_id, p2_ns)
        record = {
            "recordType": "stimulus_event_received", "protocolVersion": PROTOCOL_VERSION,
            "connectionId": connection_id, "pcReceiveMonotonicNs": p2_ns,
            "pcReceiveUtc": p2_utc, "validationStatus": validation,
            "sequenceStatus": sequence_result.status if sequence_result else "not_checked",
            "estimatedPcEventMonotonicNs": int(estimate * 1e9) if estimate is not None else None,
            "clockSync": clock_sync,
            "originalQuestEvent": event,
        }
        self.events.append(record)
        if self.event_observer is not None:
            self.event_observer(record)
        ack = {
            "protocolVersion": PROTOCOL_VERSION, "messageType": "ack",
            "sessionId": session_id, "sequence": sequence, "connectionId": connection_id,
            "pcReceiveMonotonicNs": p2_ns, "pcReceiveUtc": p2_utc,
            "sequenceStatus": sequence_result.status if sequence_result else "not_checked",
            "validationStatus": validation,
        }
        writer.write(encode_line(ack))
        await writer.drain()

    async def _handle_sync_request(self, message, writer, connection_id, p2_ns):
        p3_ns = time.perf_counter_ns()
        response = {
            "protocolVersion": PROTOCOL_VERSION, "messageType": "clock_sync_response",
            "syncSequence": message.get("syncSequence"),
            "q1QuestMonotonicSeconds": message.get("q1QuestMonotonicSeconds"),
            "p2PcReceiveMonotonicNs": p2_ns, "p3PcSendMonotonicNs": p3_ns,
            "connectionId": connection_id,
        }
        writer.write(encode_line(response))
        await writer.drain()

    def _handle_sync_result(self, message, connection_id, receive_ns):
        q1 = float(message["q1QuestMonotonicSeconds"])
        q4 = float(message["q4QuestMonotonicSeconds"])
        p2 = int(message["p2PcReceiveMonotonicNs"]) / 1e9
        p3 = int(message["p3PcSendMonotonicNs"]) / 1e9
        rtt = float(message.get("roundTripSeconds", -1.0))
        sample_accepted = 0.0 <= rtt <= 0.25
        if sample_accepted:
            self.mappers[connection_id].add((q1 + q4) / 2.0, (p2 + p3) / 2.0)
            self.sync_state[connection_id]["latestAcceptedPcMonotonicNs"] = receive_ns
        coefficients = self.mappers[connection_id].coefficients()
        self.diagnostics.append({
            "recordType": "clock_sync_sample", "connectionId": connection_id,
            "pcResultReceiveMonotonicNs": receive_ns, "rawSample": message,
            "sampleAcceptedForAffineFit": sample_accepted,
            "affineA": coefficients[0] if coefficients else None,
            "affineB": coefficients[1] if coefficients else None,
            "affineResidualRmsSeconds": self.mappers[connection_id].residual_rms_seconds(),
            "offsetSignConvention": "pc_minus_quest",
        })

    def _clock_snapshot(self, connection_id, now_ns):
        mapper = self.mappers[connection_id]
        latest = self.sync_state[connection_id]["latestAcceptedPcMonotonicNs"]
        residual = mapper.residual_rms_seconds()
        return {
            "status": "ready" if mapper.coefficients() is not None else "unavailable",
            "acceptedSampleCount": mapper.sample_count,
            "affineResidualRmsSeconds": residual,
            "latestAcceptedPcMonotonicNs": latest,
            "maximumAcceptedRttSeconds": 0.25,
            "clockIsSoftwareOnly": True,
        }


async def run(args):
    endpoint = TriggerServer(args.log_dir)
    server = await asyncio.start_server(endpoint.handle_connection, args.host, args.port, limit=1_048_577)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print(f"M5.2 trigger server listening on {addresses}; logs={args.log_dir}", flush=True)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11000)
    parser.add_argument("--log-dir", default="integration/synchronization/runtime_logs")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
