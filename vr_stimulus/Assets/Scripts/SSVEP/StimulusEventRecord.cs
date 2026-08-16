using System;

namespace BCIIntelligentRobot.VRStimulus
{
    [Serializable]
    public sealed class StimulusTargetEventSnapshot
    {
        public string targetId;
        public int targetIndex;
        public int framesPerHalfCycle;
        public int phaseOffsetFrames;
        public bool isWhite;
    }

    [Serializable]
    public sealed class StimulusEventRecord
    {
        public int schemaVersion = 1;
        public string eventType;
        public string sessionId;
        public string trialId;
        public long sequence;
        public string trialState;
        public int unityFrame;
        public int commonStartFrame;
        public int globalStimulusFrame;
        public int lastActiveGlobalStimulusFrame;
        public double questMonotonicSeconds;
        public string utc;
        public bool xrRefreshRateAvailable;
        public float xrRefreshRateHz;
        public string targetConfigurationId;
        public StimulusTargetEventSnapshot[] targets;
        public string stopReason;
        public string timingSemantics;
    }
}
