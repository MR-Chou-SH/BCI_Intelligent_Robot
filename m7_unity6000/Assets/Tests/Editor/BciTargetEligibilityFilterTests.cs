using BCIIntelligentRobot.Vision;
using NUnit.Framework;

namespace BCIIntelligentRobot.Tests
{
    public class BciTargetEligibilityFilterTests
    {
        [TestCase("cup")]
        [TestCase("cell phone")]
        [TestCase("cell_phone")]
        [TestCase("  KEYBOARD  ")]
        public void DefaultAllowlist_AcceptsOperationalObjectClasses(string label)
        {
            var filter = new BciTargetEligibilityFilter();

            Assert.That(filter.IsEligible(label), Is.True);
        }

        [TestCase("person")]
        [TestCase("dining table")]
        [TestCase("chair")]
        [TestCase("couch")]
        [TestCase("laptop")]
        public void DefaultAllowlist_RejectsBackgroundAndUnlistedClasses(string label)
        {
            var filter = new BciTargetEligibilityFilter();

            Assert.That(filter.IsEligible(label), Is.False);
        }
    }
}
