using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.XR.ARSubsystems;

namespace BCIIntelligentRobot.Tests
{
    public sealed class WorldTargetRayMathTests
    {
        private static readonly XRFov SymmetricFov = new XRFov(
            -Mathf.PI * 0.25f,
            Mathf.PI * 0.25f,
            Mathf.PI * 0.25f,
            -Mathf.PI * 0.25f);

        [Test]
        public void CenterUvUsesEnvironmentDepthPoseAndOpenXrNegativeZForward()
        {
            Pose pose = new Pose(new Vector3(1f, 2f, 3f), Quaternion.identity);

            bool success = WorldTargetRayMath.TryCreateWorldRay(pose, SymmetricFov, new Vector2(0.5f, 0.5f), out Ray ray);

            Assert.That(success, Is.True);
            Assert.That(ray.origin, Is.EqualTo(pose.position));
            Assert.That(ray.direction.x, Is.EqualTo(0f).Within(0.0001f));
            Assert.That(ray.direction.y, Is.EqualTo(0f).Within(0.0001f));
            Assert.That(ray.direction.z, Is.EqualTo(-1f).Within(0.0001f));
        }

        [Test]
        public void EdgeUvsFollowTheProvidedFieldOfView()
        {
            Pose pose = new Pose(Vector3.zero, Quaternion.identity);

            Assert.That(WorldTargetRayMath.TryCreateWorldRay(pose, SymmetricFov, Vector2.zero, out Ray lowerLeft), Is.True);
            Assert.That(WorldTargetRayMath.TryCreateWorldRay(pose, SymmetricFov, Vector2.one, out Ray upperRight), Is.True);
            Assert.That(lowerLeft.direction.x, Is.LessThan(0f));
            Assert.That(lowerLeft.direction.y, Is.LessThan(0f));
            Assert.That(upperRight.direction.x, Is.GreaterThan(0f));
            Assert.That(upperRight.direction.y, Is.GreaterThan(0f));
        }

        [Test]
        public void OutOfRangeUvDoesNotProduceASyntheticRay()
        {
            bool success = WorldTargetRayMath.TryCreateWorldRay(
                new Pose(Vector3.zero, Quaternion.identity),
                SymmetricFov,
                new Vector2(1.01f, 0.5f),
                out _);

            Assert.That(success, Is.False);
        }

        [Test]
        public void FiniteEnvironmentDepthDecodesToMeters()
        {
            XRNearFarPlanes nearFar = new XRNearFarPlanes(0.2f, 10f);

            Assert.That(WorldTargetRayMath.TryDecodeEnvironmentDepthMeters(0f, nearFar, out float nearDepth), Is.True);
            Assert.That(WorldTargetRayMath.TryDecodeEnvironmentDepthMeters(1f, nearFar, out float farDepth), Is.True);
            Assert.That(nearDepth, Is.EqualTo(0.2f).Within(0.0001f));
            Assert.That(farDepth, Is.EqualTo(10f).Within(0.001f));
        }

        [Test]
        public void InfiniteFarEnvironmentDepthDecodesToMeters()
        {
            bool success = WorldTargetRayMath.TryDecodeEnvironmentDepthMeters(
                0.5f,
                new XRNearFarPlanes(0.2f, float.PositiveInfinity),
                out float depthMeters);

            Assert.That(success, Is.True);
            Assert.That(depthMeters, Is.EqualTo(0.4f).Within(0.0001f));
        }

        [Test]
        public void QuestInfiniteFarSampleDecodesToMeasuredDistance()
        {
            bool success = WorldTargetRayMath.TryDecodeEnvironmentDepthMeters(
                0.215576f,
                new XRNearFarPlanes(0.1f, float.PositiveInfinity),
                out float depthMeters);

            Assert.That(success, Is.True);
            Assert.That(depthMeters, Is.EqualTo(0.1275f).Within(0.0001f));
        }

        [Test]
        public void MedianDepthRejectsInvalidSamples()
        {
            var samples = new[] { 0f, float.NaN, 0.2f, 0.4f, 0.3f, 1f };

            bool success = WorldTargetRayMath.TryGetMedianValidDepth(samples, 3, out float median);

            Assert.That(success, Is.True);
            Assert.That(median, Is.EqualTo(0.3f).Within(0.0001f));
        }

        [Test]
        public void DepthAlongEnvironmentRayProducesWorldPosition()
        {
            var ray = new Ray(new Vector3(1f, 2f, 3f), Vector3.forward);

            bool success = WorldTargetRayMath.TryCreateWorldPosition(ray, 2f, out Vector3 worldPosition);

            Assert.That(success, Is.True);
            Assert.That(worldPosition, Is.EqualTo(new Vector3(1f, 2f, 5f)));
        }
    }
}
