using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR.ARSubsystems;

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// Converts a normalized UV in an OpenXR environment-depth view into a world-space ray.
    /// The caller is responsible for proving that its source UV is calibrated to that depth view.
    /// </summary>
    public static class WorldTargetRayMath
    {
        public static bool TryCreateWorldRay(
            Pose environmentDepthViewPose,
            XRFov environmentDepthFov,
            Vector2 normalizedDepthUv,
            out Ray worldRay)
        {
            worldRay = default;
            if (!IsFinite(normalizedDepthUv.x) || !IsFinite(normalizedDepthUv.y) ||
                normalizedDepthUv.x < 0f || normalizedDepthUv.x > 1f ||
                normalizedDepthUv.y < 0f || normalizedDepthUv.y > 1f)
            {
                return false;
            }

            float left = Mathf.Tan(environmentDepthFov.angleLeft);
            float right = Mathf.Tan(environmentDepthFov.angleRight);
            float up = Mathf.Tan(environmentDepthFov.angleUp);
            float down = Mathf.Tan(environmentDepthFov.angleDown);
            if (!IsFinite(left) || !IsFinite(right) || !IsFinite(up) || !IsFinite(down))
                return false;

            // OpenXR view space looks down negative Z. The pose is supplied by the
            // environment-depth frame itself, not by the HMD center-eye transform.
            Vector3 viewDirection = new Vector3(
                Mathf.Lerp(left, right, normalizedDepthUv.x),
                Mathf.Lerp(down, up, normalizedDepthUv.y),
                -1f).normalized;
            Vector3 worldDirection = environmentDepthViewPose.rotation * viewDirection;
            if (!IsFinite(worldDirection.x) || !IsFinite(worldDirection.y) || !IsFinite(worldDirection.z))
                return false;

            worldRay = new Ray(environmentDepthViewPose.position, worldDirection);
            return true;
        }

        /// <summary>
        /// Decodes a normalized XR_META_environment_depth texture sample to a positive
        /// view-space distance in meters. The extension uses the same depth encoding as
        /// XR_KHR_composition_layer_depth. A far plane smaller than the near plane denotes
        /// the extension's infinite-far projection.
        /// </summary>
        public static bool TryDecodeEnvironmentDepthMeters(
            float normalizedDepth,
            XRNearFarPlanes nearFarPlanes,
            out float depthMeters)
        {
            depthMeters = default;
            float near = nearFarPlanes.nearZ;
            float far = nearFarPlanes.farZ;
            bool infiniteFar = float.IsPositiveInfinity(far) || (IsFinite(far) && far < near);
            if (!IsFinite(normalizedDepth) || normalizedDepth < 0f || normalizedDepth > 1f ||
                !IsFinite(near) || near <= 0f ||
                float.IsNaN(far) || float.IsNegativeInfinity(far) || far <= 0f ||
                (!infiniteFar && far <= near))
            {
                return false;
            }

            if (!infiniteFar)
            {
                float denominator = far - (normalizedDepth * (far - near));
                if (!IsFinite(denominator) || denominator <= Mathf.Epsilon)
                    return false;

                depthMeters = (near * far) / denominator;
            }
            else
            {
                float denominator = 1f - normalizedDepth;
                if (!IsFinite(denominator) || denominator <= Mathf.Epsilon)
                    return false;

                depthMeters = near / denominator;
            }

            return IsFinite(depthMeters) && depthMeters > 0f;
        }

        /// <summary>
        /// Uses a small median filter while ignoring invalid normalized depth samples.
        /// </summary>
        public static bool TryGetMedianValidDepth(
            IList<float> normalizedDepthSamples,
            int minimumValidSamples,
            out float medianNormalizedDepth)
        {
            medianNormalizedDepth = default;
            if (normalizedDepthSamples == null || minimumValidSamples <= 0)
                return false;

            var valid = new List<float>(normalizedDepthSamples.Count);
            for (int i = 0; i < normalizedDepthSamples.Count; i++)
            {
                float sample = normalizedDepthSamples[i];
                if (IsFinite(sample) && sample > 0f && sample < 1f)
                    valid.Add(sample);
            }

            if (valid.Count < minimumValidSamples)
                return false;

            valid.Sort();
            int middle = valid.Count / 2;
            medianNormalizedDepth = (valid.Count & 1) == 0
                ? (valid[middle - 1] + valid[middle]) * 0.5f
                : valid[middle];
            return true;
        }

        public static bool TryCreateWorldPosition(Ray worldRay, float depthMeters, out Vector3 worldPosition)
        {
            worldPosition = default;
            if (!IsFinite(depthMeters) || depthMeters <= 0f ||
                !IsFinite(worldRay.origin.x) || !IsFinite(worldRay.origin.y) || !IsFinite(worldRay.origin.z) ||
                !IsFinite(worldRay.direction.x) || !IsFinite(worldRay.direction.y) || !IsFinite(worldRay.direction.z))
            {
                return false;
            }

            worldPosition = worldRay.GetPoint(depthMeters);
            return IsFinite(worldPosition.x) && IsFinite(worldPosition.y) && IsFinite(worldPosition.z);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }
    }
}
