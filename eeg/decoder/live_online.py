"""Thread-safe live ND8 source bridge; no port is opened by this module."""
import threading
import time
import numpy as np

from .pseudo_online import ReplayPacket, RollingEegBuffer, DEFAULT_PSEUDO_ONLINE_CONFIG, LABELS, stabilize


class LiveOnlineController:
    """Callback-safe active-trial controller sharing M6.5b decoder semantics."""
    def __init__(self, backend, selected_channels, prediction_log=None, decision_log=None, config=None):
        self.backend, self.selected_channels = backend, selected_channels
        self.config, self.buffer = config or DEFAULT_PSEUDO_ONLINE_CONFIG, RollingEegBuffer()
        self.prediction_log, self.decision_log = prediction_log, decision_log
        self._lock, self.active = threading.RLock(), None

    def start_trial(self, association):
        with self._lock:
            if self.active is not None: return False
            self.active = {"sessionId": association["sessionId"], "trialId": association["trialId"],
                           "startSample": int(association["estimatedGlobalSampleIndex"]), "predictions": [],
                           "nextStop": int(association["estimatedGlobalSampleIndex"]) + self.config.onset_guard_samples + self.config.analysis_sample_count}
            return True

    def ingest_packet(self, packet_metadata, continuity, samples):
        packet = ReplayPacket(np.asarray(samples, dtype=float), int(continuity.cumulative_first_sample_index),
                              int(packet_metadata.packet_sequence), int(packet_metadata.pc_receive_monotonic_ns), continuity.status)
        with self._lock:
            self.buffer.append(packet)
            state = self.active
            if state is None: return []
            jobs = []
            while state["nextStop"] <= state["startSample"] + 4000 and self.buffer.stop_sample >= state["nextStop"]:
                try: jobs.append((state["nextStop"] - self.config.analysis_sample_count, state["nextStop"], packet))
                except ValueError: break
                state["nextStop"] += 200
        emitted = []
        for start, stop, latest in jobs:  # compute outside callback lock
            with self._lock:
                if self.active is not state: break
                try: data = self.buffer.window(start, stop)[self.selected_channels]
                except ValueError: continue
            begun=time.perf_counter_ns(); index, scores=self.backend.predict(data); record={"sessionId":state["sessionId"],"trialId":state["trialId"],"predictionIndex":len(state["predictions"]),"predictedClass":LABELS[index],"candidateScores":{str(f):float(v) for f,v in zip(self.config.target_frequencies_hz,scores)},"analysisWindowStart":start,"analysisWindowEnd":stop,"relativeToStimulusStartSeconds":(stop-state["startSample"])/1000.0,"latestPacketSequence":latest.packet_sequence,"computeDurationNs":time.perf_counter_ns()-begun,"continuityState":"contiguous"}
            with self._lock:
                if self.active is not state: break
                state["predictions"].append(record); emitted.append(record)
            if self.prediction_log: self.prediction_log.append(record)
        return emitted

    def stop_trial(self, reason="stimulus_stopped"):
        with self._lock:
            state, self.active = self.active, None
        if state is None: return None
        decision=stabilize(state["predictions"],2); decision.update({"sessionId":state["sessionId"],"trialId":state["trialId"],"stabilizer":"2-Consecutive","predictionSequence":state["predictions"],"reason":decision["reason"] if decision["decisionMade"] else reason})
        if self.decision_log: self.decision_log.append(decision)
        return decision
