"""Thread-safe live ND8 source bridge; no port is opened by this module."""
import threading
import time
import numpy as np

from .pseudo_online import ReplayPacket, RollingEegBuffer, DEFAULT_PSEUDO_ONLINE_CONFIG, LABELS


class LiveOnlineController:
    """Callback-safe active-trial controller sharing M6.5b decoder semantics."""
    def __init__(self, backend, selected_channels, prediction_log=None, decision_log=None, config=None):
        self.backend, self.selected_channels = backend, selected_channels
        self.config, self.buffer = config or DEFAULT_PSEUDO_ONLINE_CONFIG, RollingEegBuffer()
        self.prediction_log, self.decision_log = prediction_log, decision_log
        self._lock, self.active = threading.RLock(), None
        self._seen_trials, self._last_packet_sequence, self._generation = set(), None, 0

    def start_trial(self, association):
        with self._lock:
            identity = (association["sessionId"], association["trialId"])
            if self.active is not None or identity in self._seen_trials: return False
            self._seen_trials.add(identity); self._generation += 1
            self.active = {"sessionId": association["sessionId"], "trialId": association["trialId"],
                           "startSample": int(association["estimatedGlobalSampleIndex"]), "predictions": [],
                           "nextStop": int(association["estimatedGlobalSampleIndex"]) + self.config.onset_guard_samples + self.config.analysis_sample_count,
                           "generation": self._generation, "lastLabel": None, "consecutiveCount": 0, "decision": None}
            return True

    def ingest_packet(self, packet_metadata, continuity, samples):
        packet = ReplayPacket(np.asarray(samples, dtype=float), int(continuity.cumulative_first_sample_index),
                              int(packet_metadata.packet_sequence), int(packet_metadata.pc_receive_monotonic_ns), continuity.status)
        with self._lock:
            if self._last_packet_sequence is not None and packet.packet_sequence <= self._last_packet_sequence:
                return []
            self._last_packet_sequence = packet.packet_sequence
            self.buffer.append(packet)
            state = self.active
            if state is None: return []
            jobs = []
            while state["nextStop"] <= state["startSample"] + 4000 and self.buffer.stop_sample >= state["nextStop"]:
                jobs.append((state["nextStop"] - self.config.analysis_sample_count, state["nextStop"], packet,
                             state["generation"]))
                state["nextStop"] += 200
        emitted = []
        for start, stop, latest, generation in jobs:  # compute outside callback lock
            with self._lock:
                if self.active is not state or state["generation"] != generation: break
                try: data = self.buffer.window(start, stop)[self.selected_channels]
                except ValueError: continue
            begun=time.perf_counter_ns(); index, scores=self.backend.predict(data); record={"sessionId":state["sessionId"],"trialId":state["trialId"],"predictionIndex":len(state["predictions"]),"predictedClass":LABELS[index],"candidateScores":{str(f):float(v) for f,v in zip(self.config.target_frequencies_hz,scores)},"analysisWindowStart":start,"analysisWindowEnd":stop,"relativeToStimulusStartSeconds":(stop-state["startSample"])/1000.0,"latestPacketSequence":latest.packet_sequence,"computeDurationNs":time.perf_counter_ns()-begun,"continuityState":"contiguous"}
            with self._lock:
                if self.active is not state or state["generation"] != generation: break
                state["predictions"].append(record); emitted.append(record)
                if record["predictedClass"] == state["lastLabel"]: state["consecutiveCount"] += 1
                else: state["lastLabel"], state["consecutiveCount"] = record["predictedClass"], 1
                if state["decision"] is None and state["consecutiveCount"] >= 2:
                    state["decision"] = {"sessionId":state["sessionId"], "trialId":state["trialId"],
                        "finalDecisionLabel":record["predictedClass"], "decisionMade":True,
                        "decisionPredictionIndex":record["predictionIndex"],
                        "decisionRelativeTimeSeconds":record["relativeToStimulusStartSeconds"],
                        "stabilizer":"2-Consecutive", "predictionSequence":list(state["predictions"])}
                    if self.decision_log: self.decision_log.append(state["decision"])
            if self.prediction_log: self.prediction_log.append(record)
        return emitted

    def stop_trial(self, reason="stimulus_stopped"):
        with self._lock:
            state, self.active = self.active, None
        if state is None: return None
        decision = state["decision"] or {"sessionId":state["sessionId"],"trialId":state["trialId"],
            "finalDecisionLabel":None,"decisionMade":False,"decisionPredictionIndex":None,
            "decisionRelativeTimeSeconds":None,"stabilizer":"2-Consecutive",
            "predictionSequence":state["predictions"],"reason":reason}
        if state["decision"] is None and self.decision_log: self.decision_log.append(decision)
        return decision
