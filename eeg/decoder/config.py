"""Fixed, auditable M6.2a Standard CCA configuration."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DecoderConfig:
    target_frequencies_hz: tuple = (7.2, 9.0, 12.0)
    harmonic_count: int = 3
    input_sampling_rate_hz: float = 1000.0
    decoder_sampling_rate_hz: float = 1000.0
    onset_guard_seconds: float = 0.5
    analysis_duration_seconds: float = 3.0
    preprocessing: str = "demean_per_channel_no_filter_no_resample"
    channel_selection_rule: str = "signal_quality_quality_equals_usable"

    @property
    def analysis_sample_count(self):
        return int(round(self.analysis_duration_seconds * self.decoder_sampling_rate_hz))

    @property
    def onset_guard_samples(self):
        return int(round(self.onset_guard_seconds * self.input_sampling_rate_hz))

    def to_dict(self):
        value = asdict(self)
        value["targetFrequenciesHz"] = list(value.pop("target_frequencies_hz"))
        value["harmonicCount"] = value.pop("harmonic_count")
        value["inputSamplingRateHz"] = value.pop("input_sampling_rate_hz")
        value["decoderSamplingRateHz"] = value.pop("decoder_sampling_rate_hz")
        value["onsetGuardSeconds"] = value.pop("onset_guard_seconds")
        value["analysisDurationSeconds"] = value.pop("analysis_duration_seconds")
        value["analysisSampleCount"] = self.analysis_sample_count
        value["onsetGuardSamples"] = self.onset_guard_samples
        value["channelSelectionRule"] = value.pop("channel_selection_rule")
        return value
