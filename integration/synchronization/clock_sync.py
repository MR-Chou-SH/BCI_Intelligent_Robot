def calculate_ntp_sample(q1, p2, p3, q4):
    """Return RTT and PC-minus-Quest offset for four monotonic timestamps."""
    rtt = (q4 - q1) - (p3 - p2)
    offset = ((p2 - q1) + (p3 - q4)) / 2.0
    return {"roundTripSeconds": rtt, "offsetPcMinusQuestSeconds": offset}


class AffineClockMapper:
    """Least-squares PC ~= a * Quest + b fit over accepted raw sync samples."""

    def __init__(self, maximum_samples=120):
        self.maximum_samples = maximum_samples
        self._pairs = []

    @property
    def sample_count(self):
        return len(self._pairs)

    def add(self, quest_midpoint, pc_midpoint):
        self._pairs.append((float(quest_midpoint), float(pc_midpoint)))
        self._pairs = self._pairs[-self.maximum_samples:]

    def coefficients(self):
        if len(self._pairs) < 2:
            return None
        mean_x = sum(x for x, _ in self._pairs) / len(self._pairs)
        mean_y = sum(y for _, y in self._pairs) / len(self._pairs)
        variance = sum((x - mean_x) ** 2 for x, _ in self._pairs)
        if variance == 0:
            return None
        slope = sum((x - mean_x) * (y - mean_y) for x, y in self._pairs) / variance
        return slope, mean_y - slope * mean_x

    def map(self, quest_seconds):
        coefficients = self.coefficients()
        return None if coefficients is None else coefficients[0] * quest_seconds + coefficients[1]

    def residual_rms_seconds(self):
        coefficients = self.coefficients()
        if coefficients is None:
            return None
        a, b = coefficients
        squared = [(y - (a * x + b)) ** 2 for x, y in self._pairs]
        return (sum(squared) / len(squared)) ** 0.5
