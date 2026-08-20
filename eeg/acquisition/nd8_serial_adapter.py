"""Explicit-lifecycle ND8 serial adapter for M5.3 live acquisition."""

from enum import Enum
from queue import SimpleQueue
from threading import Event
from typing import Callable, Optional

from eeg.sample_association.jsonl import AppendOnlyJsonl
from eeg.sample_association.timeline import EegPacketTimeline

from .nd8_packet import Nd8Packet


class AcquisitionState(str, Enum):
    IDLE = "idle"
    PORT_OPEN = "port_open"
    READY = "ready"
    STREAMING = "streaming"
    STOPPING = "stopping"
    CLOSED = "closed"


def create_nd8_serial_device(
    com_port: str,
    on_eeg_received: Callable[[dict], None],
    on_host_mac_received: Callable[[str], None],
):
    """Create the SDK callback object only when the caller explicitly opens a port."""
    try:
        from neuro_dance.nd_device_process import NdDeviceBase
    except ImportError as error:
        raise RuntimeError("Neurodance Python SDK is unavailable; install/use its environment before live testing") from error

    class CallbackDevice(NdDeviceBase):
        def __init__(self):
            NdDeviceBase.__init__(self, mode="serial", com=com_port, tcp_ip="", tcp_port=None, host_mac_bytes=None)

        def eeg_received(self, data):
            on_eeg_received(data)

        def host_mac_received(self, host_mac):
            on_host_mac_received(host_mac)

    return CallbackDevice()


class Nd8SerialAdapter:
    """Minimal serial acquisition boundary with no automatic device-side actions.

    Construction does not pair, unpair, scan, configure rate, enable EEG, or
    begin streaming. ``open_port`` performs one required read-only dongle MAC
    query and waits for its SDK callback before becoming ready. Only
    ``start_streaming`` invokes the SDK configuration and enable commands.
    """

    def __init__(
        self,
        com_port: str,
        nominal_sampling_rate_hz: float = 1000.0,
        device_factory: Callable[[str, Callable[[dict], None], Callable[[str], None]], object] = create_nd8_serial_device,
        metadata_log: Optional[AppendOnlyJsonl] = None,
        raw_packet_log: Optional[AppendOnlyJsonl] = None,
        packet_observer: Optional[Callable[[object, object], None]] = None,
        live_packet_observer: Optional[Callable[[Nd8Packet, object], None]] = None,
        readiness_timeout_seconds: float = 5.0,
    ):
        if not com_port:
            raise ValueError("COM port is required")
        if nominal_sampling_rate_hz <= 0:
            raise ValueError("nominal sampling rate must be positive")
        if readiness_timeout_seconds <= 0:
            raise ValueError("readiness timeout must be positive")
        self.com_port = com_port
        self.nominal_sampling_rate_hz = float(nominal_sampling_rate_hz)
        self._device_factory = device_factory
        self._metadata_log = metadata_log
        self._raw_packet_log = raw_packet_log
        self._packet_observer = packet_observer
        # Kept separate from packet_observer so existing association consumers
        # retain their metadata-only boundary.  The live decoder needs samples.
        self._live_packet_observer = live_packet_observer
        self.readiness_timeout_seconds = float(readiness_timeout_seconds)
        self.timeline = EegPacketTimeline()
        self.packet_queue = SimpleQueue()
        self.state = AcquisitionState.IDLE
        self._device = None
        self._next_packet_sequence = 0
        self.callback_errors: list[str] = []
        self._host_mac_ready = Event()
        self._host_mac = None

    def open_port(self):
        if self.state not in (AcquisitionState.IDLE, AcquisitionState.CLOSED):
            raise RuntimeError(f"cannot open port from {self.state.value}")
        self._host_mac_ready.clear()
        self._host_mac = None
        self._device = self._device_factory(self.com_port, self.eeg_received, self.host_mac_received)
        self._device.start()
        self.state = AcquisitionState.PORT_OPEN
        self._device.host_mac_info()
        if not self._host_mac_ready.wait(self.readiness_timeout_seconds):
            raise RuntimeError("dongle host MAC readiness callback timed out")
        self.state = AcquisitionState.READY

    @property
    def host_mac_ready(self):
        return self._host_mac_ready.is_set()

    @property
    def host_mac_suffix(self):
        return self._host_mac[-4:] if self._host_mac else None

    def host_mac_received(self, host_mac: str):
        if isinstance(host_mac, str) and host_mac:
            self._host_mac = host_mac
            self._host_mac_ready.set()

    def start_streaming(self):
        if self.state != AcquisitionState.READY:
            raise RuntimeError(f"cannot start streaming from {self.state.value}")
        self._device.eeg_channel_config(int(self.nominal_sampling_rate_hz))
        self._device.eeg_channel_enable()
        self.state = AcquisitionState.STREAMING

    def eeg_received(self, sdk_payload: dict):
        """SDK callback: validate, timestamp locally, append continuity, enqueue."""
        if self.state != AcquisitionState.STREAMING:
            return
        try:
            packet = Nd8Packet.from_sdk_payload(
                sdk_payload,
                packet_sequence=self._next_packet_sequence,
                nominal_sampling_rate_hz=self.nominal_sampling_rate_hz,
            )
            continuity = self.timeline.append(packet.to_metadata())
            self.packet_queue.put(packet)
            if self._raw_packet_log is not None:
                self._raw_packet_log.append(packet.raw_log_record())
            if self._metadata_log is not None:
                self._metadata_log.append(packet.metadata_log_record(continuity))
            if self._packet_observer is not None:
                self._packet_observer(packet.to_metadata(), continuity)
            if self._live_packet_observer is not None:
                self._live_packet_observer(packet, continuity)
            self._next_packet_sequence += 1
        except (TypeError, ValueError) as error:
            self.callback_errors.append(str(error))

    def stop(self):
        if self.state not in (AcquisitionState.READY, AcquisitionState.STREAMING):
            raise RuntimeError(f"cannot stop from {self.state.value}")
        self.state = AcquisitionState.STOPPING
        if self.state == AcquisitionState.STOPPING and self._device is not None:
            # This is an explicit stop operation, never an implicit reconnect action.
            self._device.eeg_disable()
        self.state = AcquisitionState.READY

    def close(self):
        if self.state == AcquisitionState.CLOSED:
            return
        if self.state == AcquisitionState.STREAMING:
            self.stop()
        if self._device is not None:
            self._device.close()
        self._device = None
        self.state = AcquisitionState.CLOSED
