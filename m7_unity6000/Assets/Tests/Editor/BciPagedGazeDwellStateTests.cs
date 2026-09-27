using NUnit.Framework;
using BCIIntelligentRobot.Integration;

namespace BCIIntelligentRobot.Tests
{
    public sealed class BciPagedGazeDwellStateTests
    {
        [Test]
        public void EnabledControlActivatesOnceAfterDwellAndRequiresExitBeforeReentry()
        {
            var state = new BCIIntelligentRobot.Integration.BciGazeDwellState(0.8f);

            Assert.That(state.Update("next", true, 10.0f), Is.False);
            Assert.That(state.Update("next", true, 10.79f), Is.False);
            // Keep the positive threshold check away from the exact float
            // boundary used by the 0.8 s dwell contract.
            Assert.That(state.Update("next", true, 10.81f), Is.True);
            Assert.That(state.Update("next", true, 11.2f), Is.False);

            state.Update(null, false, 11.3f);
            Assert.That(state.Update("next", true, 11.4f), Is.False);
            Assert.That(state.Update("next", true, 12.2f), Is.True);
        }

        [Test]
        public void TriggerBandUsesOnePointFiveSecondsAndRequiresExitBeforeSecondTrigger()
        {
            Assert.That(BciPagedGazeInteractor.TriggerDwellSeconds, Is.EqualTo(1.5f));
            var state = new BciGazeDwellState(BciPagedGazeInteractor.TriggerDwellSeconds);

            Assert.That(state.Update("eeg_trigger", true, 10.0f), Is.False);
            Assert.That(state.Update("eeg_trigger", true, 11.49f), Is.False,
                "A gaze dwell shorter than 1.5 seconds must not trigger.");
            Assert.That(state.Update("eeg_trigger", true, 11.51f), Is.True);
            Assert.That(state.Update("eeg_trigger", true, 12.0f), Is.False,
                "Holding gaze on the band must not fire twice.");

            Assert.That(state.Update(null, false, 12.1f), Is.False,
                "Gaze exit clears the fired latch.");
            Assert.That(state.Update("eeg_trigger", true, 13.0f), Is.False);
            Assert.That(state.Update("eeg_trigger", true, 14.51f), Is.True,
                "A new 1.5 second dwell after gaze exit must be accepted.");
        }

        [Test]
        public void DisabledControlAndTargetSwitchResetDwell()
        {
            var state = new BCIIntelligentRobot.Integration.BciGazeDwellState(0.8f);

            Assert.That(state.Update("next", false, 1.0f), Is.False);
            Assert.That(state.Update("next", true, 1.1f), Is.False);
            Assert.That(state.Update("previous", true, 1.7f), Is.False);
            Assert.That(state.Update("previous", true, 2.49f), Is.False);
            // Use a small margin beyond 0.8 seconds. With single-precision
            // timestamps, 2.5f - 1.7f can be 0.79999995f and is therefore
            // correctly treated as just short of the full dwell interval.
            Assert.That(state.Update("previous", true, 2.51f), Is.True);
        }

        [Test]
        public void M19PendingTrialTimeoutUsesUnscaledElapsedTimeAndFailsClosedAtDeadline()
        {
            Assert.That(
                BCIIntelligentRobot.Integration.BciPagedTargetQueueController.HasPendingTrialTimedOut(
                    10f, 39.99f, 30f),
                Is.False);
            Assert.That(
                BCIIntelligentRobot.Integration.BciPagedTargetQueueController.HasPendingTrialTimedOut(
                    10f, 40f, 30f),
                Is.True);
            Assert.That(
                BCIIntelligentRobot.Integration.BciPagedTargetQueueController.HasPendingTrialTimedOut(
                    10f, 100f, 0f),
                Is.False);
        }
    }
}
