from queue import Empty
from tempfile import TemporaryDirectory
import unittest

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.acquisition.nd8_serial_adapter import AcquisitionState, Nd8SerialAdapter
from eeg.sample_association.jsonl import AppendOnlyJsonl


class MockDevice:
    def __init__(self, host_callback=None, respond_to_host_mac=True):
        self.calls = []
        self.host_callback = host_callback
        self.respond_to_host_mac = respond_to_host_mac

    def start(self):
        self.calls.append("start")

    def eeg_channel_config(self, rate):
        self.calls.append(("eeg_channel_config", rate))

    def eeg_channel_enable(self):
        self.calls.append("eeg_channel_enable")

    def host_mac_info(self):
        self.calls.append("host_mac_info")
        if self.respond_to_host_mac:
            self.host_callback("001122334455")

    def eeg_disable(self):
        self.calls.append("eeg_disable")

    def close(self):
        self.calls.append("close")


def make_adapter():
    device = MockDevice()

    def factory(port, eeg_callback, host_callback):
        device.host_callback = host_callback
        return device

    adapter = Nd8SerialAdapter("COM11", device_factory=factory)
    return adapter, device


def payload(timestamp=1_700_000_000_000, samples=200):
    return {"timestamp": timestamp, "data": [[float(channel)] * samples for channel in range(8)]}


class Nd8SerialAdapterTests(unittest.TestCase):
    def test_construction_has_no_device_side_effects(self):
        adapter, device = make_adapter()
        self.assertEqual(AcquisitionState.IDLE, adapter.state)
        self.assertEqual([], device.calls)

    def test_explicit_lifecycle_only_configures_and_enables_on_start(self):
        adapter, device = make_adapter()
        adapter.open_port()
        self.assertEqual(AcquisitionState.READY, adapter.state)
        self.assertEqual(["start", "host_mac_info"], device.calls)
        self.assertTrue(adapter.host_mac_ready)
        self.assertEqual("4455", adapter.host_mac_suffix)
        adapter.start_streaming()
        self.assertEqual(AcquisitionState.STREAMING, adapter.state)
        self.assertEqual(["start", "host_mac_info", ("eeg_channel_config", 1000), "eeg_channel_enable"], device.calls)
        adapter.stop()
        adapter.close()
        self.assertEqual(["eeg_disable", "close"], device.calls[-2:])
        self.assertEqual(AcquisitionState.CLOSED, adapter.state)

    def test_callback_records_metadata_timeline_and_raw_packet_without_assuming_n(self):
        adapter, _ = make_adapter()
        adapter.open_port()
        adapter.start_streaming()
        adapter.eeg_received(payload(samples=137))
        packet = adapter.packet_queue.get_nowait()
        self.assertEqual(8, packet.channel_count)
        self.assertEqual(137, packet.sample_count)
        self.assertAlmostEqual(0.137, packet.packet_duration_seconds)
        self.assertEqual("milliseconds", packet.to_metadata().device_timestamp_unit)
        self.assertFalse(packet.to_metadata().device_timestamp_hardware_verified)
        self.assertEqual("continuous", adapter.timeline.continuity[0].status)

    def test_callback_rejects_shape_anomaly_without_enqueuing_data(self):
        adapter, _ = make_adapter()
        adapter.open_port()
        adapter.start_streaming()
        invalid = {"timestamp": 1, "data": [[1.0] * 3 for _ in range(7)]}
        adapter.eeg_received(invalid)
        self.assertEqual(["ND8 payload must contain exactly 8 channels"], adapter.callback_errors)
        with self.assertRaises(Empty):
            adapter.packet_queue.get_nowait()

    def test_timestamp_and_shape_anomalies_are_delegated_to_existing_timeline(self):
        adapter, _ = make_adapter()
        adapter.open_port()
        adapter.start_streaming()
        adapter.eeg_received(payload(timestamp=1000, samples=100))
        adapter.eeg_received(payload(timestamp=900, samples=120))
        issues = adapter.timeline.continuity[1].issues
        self.assertIn("timestamp_regression", issues)
        self.assertIn("inconsistent_sample_count", issues)

    def test_packet_constructor_preserves_supplied_receive_evidence(self):
        packet = Nd8Packet.from_sdk_payload(payload(samples=5), 9, receive_monotonic_ns=42, receive_utc="2026-08-18T00:00:00+00:00")
        self.assertEqual(42, packet.pc_receive_monotonic_ns)
        self.assertEqual("2026-08-18T00:00:00+00:00", packet.pc_receive_utc)

    def test_open_port_requires_host_mac_callback_before_ready(self):
        device = None

        def factory(port, eeg_callback, host_callback):
            nonlocal device
            device = MockDevice(host_callback, respond_to_host_mac=False)
            return device

        adapter = Nd8SerialAdapter("COM11", device_factory=factory, readiness_timeout_seconds=0.001)
        with self.assertRaisesRegex(RuntimeError, "host MAC readiness"):
            adapter.open_port()
        self.assertEqual(AcquisitionState.PORT_OPEN, adapter.state)
        self.assertEqual(["start", "host_mac_info"], device.calls)

    def test_callback_appends_raw_packet_and_metadata_to_separate_logs(self):
        with TemporaryDirectory() as directory:
            raw_path = directory + "/raw.jsonl"
            metadata_path = directory + "/metadata.jsonl"
            device = MockDevice()

            def factory(port, eeg_callback, host_callback):
                device.host_callback = host_callback
                return device

            adapter = Nd8SerialAdapter(
                "COM11",
                device_factory=factory,
                raw_packet_log=AppendOnlyJsonl(raw_path),
                metadata_log=AppendOnlyJsonl(metadata_path),
            )
            adapter.open_port()
            adapter.start_streaming()
            adapter.eeg_received(payload(samples=3))
            with open(raw_path, encoding="utf-8") as stream:
                raw = stream.read()
            with open(metadata_path, encoding="utf-8") as stream:
                metadata = stream.read()
            self.assertIn('"recordType":"nd8_raw_packet"', raw)
            self.assertIn('"sampleCountPerChannel":3', raw)
            self.assertIn('"recordType":"nd8_packet_metadata"', metadata)


if __name__ == "__main__":
    unittest.main()
