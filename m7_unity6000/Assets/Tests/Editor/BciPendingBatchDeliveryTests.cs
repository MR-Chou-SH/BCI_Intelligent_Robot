using System.Linq;
using BCIIntelligentRobot.Integration;
using NUnit.Framework;

namespace BCIIntelligentRobot.Tests
{
    public class BciPendingBatchDeliveryTests
    {
        [Test]
        public void MatchingAck_ClearsTheOnlyPendingBatch()
        {
            var delivery = new BciPendingBatchDelivery();

            Assert.That(delivery.Queue("batch-a", "batch-a-line\n"), Is.True);
            Assert.That(delivery.GetUnsentLinesForConnection(1), Is.EqualTo(new[] { "batch-a-line\n" }));
            Assert.That(delivery.Acknowledge("batch-a"), Is.True);
            Assert.That(delivery.PendingCount, Is.EqualTo(0));
        }

        [Test]
        public void DisconnectBeforeAck_ReconnectResendsThenMatchingAckClears()
        {
            var delivery = new BciPendingBatchDelivery();
            delivery.Queue("batch-a", "batch-a-line\n");

            Assert.That(delivery.GetUnsentLinesForConnection(10), Is.EqualTo(new[] { "batch-a-line\n" }));
            Assert.That(delivery.GetUnsentLinesForConnection(10), Is.Empty,
                "The same live connection must not spin-send an unacknowledged batch.");
            Assert.That(delivery.GetUnsentLinesForConnection(11), Is.EqualTo(new[] { "batch-a-line\n" }),
                "A reconnect must resend the still-pending batch.");

            Assert.That(delivery.Acknowledge("batch-a"), Is.True);
            Assert.That(delivery.GetUnsentLinesForConnection(12), Is.Empty);
        }

        [Test]
        public void MismatchedAck_DoesNotClearAnotherPendingBatch()
        {
            var delivery = new BciPendingBatchDelivery();
            delivery.Queue("batch-a", "a\n");
            delivery.Queue("batch-b", "b\n");

            Assert.That(delivery.Acknowledge("batch-unknown"), Is.False);
            Assert.That(delivery.PendingCount, Is.EqualTo(2));
            Assert.That(delivery.GetUnsentLinesForConnection(1), Is.EqualTo(new[] { "a\n", "b\n" }));

            Assert.That(delivery.Acknowledge("batch-a"), Is.True);
            Assert.That(delivery.GetUnsentLinesForConnection(2), Is.EqualTo(new[] { "b\n" }));
            Assert.That(delivery.PendingBatchIds.Single(), Is.EqualTo("batch-b"));
        }
    }
}
