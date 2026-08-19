"""M6.1b trial/session lifecycle with explicit transition validation."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TrialPhase(str, Enum):
    SESSION_PREPARATION = "session_preparation"
    TRIAL_CUE = "trial_cue"
    PRE_STIMULUS_REST = "pre_stimulus_rest"
    STIMULATING = "stimulating"
    POST_STIMULUS_REST = "post_stimulus_rest"
    BREAK = "break"
    SESSION_COMPLETE = "session_complete"
    ABORTED = "aborted"


@dataclass(frozen=True)
class TrialTransition:
    fromPhase: str
    toPhase: str
    trialId: Optional[str]
    reason: str


class TrialStateMachine:
    """State-only protocol; timing and stimulus frames remain external."""

    def __init__(self, trial_ids, break_after=(10, 20)):
        self.trial_ids = tuple(trial_ids)
        self.break_after = frozenset(int(value) for value in break_after)
        self.phase = TrialPhase.SESSION_PREPARATION
        self.trial_position = 0
        self.current_trial_id = None
        self.transitions = []

    def _move(self, phase, reason):
        transition = TrialTransition(self.phase.value, phase.value, self.current_trial_id, reason)
        self.transitions.append(transition)
        self.phase = phase
        return transition

    def start_trial(self):
        if self.phase not in (TrialPhase.SESSION_PREPARATION, TrialPhase.POST_STIMULUS_REST, TrialPhase.BREAK):
            raise RuntimeError("cannot start a trial from {}".format(self.phase.value))
        if self.trial_position >= len(self.trial_ids):
            raise RuntimeError("all planned trials are already complete")
        self.current_trial_id = self.trial_ids[self.trial_position]
        return self._move(TrialPhase.TRIAL_CUE, "next_planned_trial")

    def begin_stimulation(self):
        if self.phase != TrialPhase.TRIAL_CUE:
            raise RuntimeError("stimulation requires trial cue")
        self._move(TrialPhase.PRE_STIMULUS_REST, "cue_complete")
        return self._move(TrialPhase.STIMULATING, "pre_rest_complete")

    def end_stimulation(self):
        if self.phase != TrialPhase.STIMULATING:
            raise RuntimeError("cannot stop a non-stimulating trial")
        return self._move(TrialPhase.POST_STIMULUS_REST, "formal_stimulation_complete")

    def finish_trial(self):
        if self.phase != TrialPhase.POST_STIMULUS_REST:
            raise RuntimeError("cannot finish trial from {}".format(self.phase.value))
        self.trial_position += 1
        if self.trial_position >= len(self.trial_ids):
            self.current_trial_id = None
            return self._move(TrialPhase.SESSION_COMPLETE, "all_trials_complete")
        if self.trial_position in self.break_after:
            return self._move(TrialPhase.BREAK, "scheduled_break")
        return self.start_trial()

    def abort(self, reason):
        if self.phase in (TrialPhase.SESSION_COMPLETE, TrialPhase.ABORTED):
            raise RuntimeError("session is already terminal")
        return self._move(TrialPhase.ABORTED, reason or "unspecified_abort")
