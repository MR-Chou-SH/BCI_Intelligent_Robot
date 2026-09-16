using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    /// <summary>
    /// Opts the existing M9 runtime scene into the independent M13.6 visual
    /// receiver without editing the frozen M8 selection lifecycle.
    /// </summary>
    internal static class M13_6VisualSyncAutoInstaller
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void AttachToM9RuntimeIfPresent()
        {
            M9VirtualManipulationBootstrap bootstrap =
                Object.FindObjectOfType<M9VirtualManipulationBootstrap>();
            if (bootstrap == null || bootstrap.GetComponent<M13_6VisualSyncReceiver>() != null)
                return;

            bootstrap.gameObject.AddComponent<M13_6VisualSyncReceiver>();
            Debug.Log("M13_6_VISUAL receiver_attached_to_m9_bootstrap");
        }
    }
}
