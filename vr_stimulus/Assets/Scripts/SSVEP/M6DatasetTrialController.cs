using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

namespace BCIIntelligentRobot.VRStimulus
{
    [DefaultExecutionOrder(-50)]
    public sealed class M6DatasetTrialController : MonoBehaviour
    {
        private enum Phase { WaitingForPlan, Preparation, Cue, PreRest, Stimulating, PostRest, Break, Complete, Aborted }

        [SerializeField] private M5TrialStimulusController m_Stimulus;
        [SerializeField] private StimulusEventTransportClient m_Transport;
        [SerializeField] private Transform m_CueAnchor;
        [SerializeField] private float m_CueDistance = 2f;
        [SerializeField] private bool m_VisualDemoMode;

        private TextMesh m_Text;
        private Coroutine m_Run;
        private Phase m_Phase = Phase.WaitingForPlan;
        private string m_SessionId;
        private readonly HashSet<string> m_SeenTrialIds = new HashSet<string>(StringComparer.Ordinal);

        private void Start()
        {
            if (m_Stimulus == null) m_Stimulus = GetComponent<M5TrialStimulusController>();
            if (m_Transport == null) m_Transport = GetComponent<StimulusEventTransportClient>();
            CreateCueText();
            SetText("Waiting for PC session plan...");
            Debug.Log("M6DIAG controller_initialized visualDemoMode=" + m_VisualDemoMode +
                " transportBound=" + (m_Transport != null) +
                " stimulusBound=" + (m_Stimulus != null) +
                " cameraBound=" + (m_CueAnchor != null) + " state=" + m_Phase, this);
            Debug.Log("M6DIAG waiting_for_dataset_session_plan", this);
        }

        private void Update()
        {
            if (m_Run != null) return;
            if (m_VisualDemoMode && m_Transport == null)
            {
                m_Run = StartCoroutine(RunPlan(CreateSyntheticDemoPlan(), true));
                return;
            }
            if (m_VisualDemoMode && m_Transport != null)
            {
                DatasetTrialPlanMessage demoPlan;
                if (m_Transport.TryDequeueDatasetPlan(out demoPlan))
                    m_Run = StartCoroutine(RunPlan(demoPlan, true));
                else
                    m_Run = StartCoroutine(RunPlan(CreateSyntheticDemoPlan(), true));
                return;
            }
            if (m_Transport != null && m_Transport.TryDequeueDatasetPlan(out DatasetTrialPlanMessage plan))
            {
                Debug.Log("M6DIAG dataset_session_plan_dequeued sessionId=" + plan.sessionId +
                    " trialCount=" + (plan.trials == null ? 0 : plan.trials.Length), this);
                string error;
                if (!ValidatePlan(plan, out error))
                {
                    Debug.LogError("M6DIAG dataset_session_plan_rejected reason=" + error, this);
                    AbortSession("Invalid PC ground-truth plan: " + error);
                    return;
                }
                m_SessionId = plan.sessionId;
                if (m_Stimulus == null || !m_Stimulus.AdoptDatasetSessionId(m_SessionId))
                {
                    AbortSession("M5 stimulus controller could not adopt PC dataset session ID");
                    return;
                }
                Debug.Log("M6DIAG dataset_session_plan_accepted sessionId=" + m_SessionId, this);
                m_Run = StartCoroutine(RunPlan(plan, m_VisualDemoMode));
            }
        }

        private static DatasetTrialPlanMessage CreateSyntheticDemoPlan()
        {
            var plan = new DatasetTrialPlanMessage
            {
                protocolVersion = 1,
                messageType = "dataset_session_plan",
                sessionId = "m6-demo-" + Guid.NewGuid().ToString("N"),
                protocol = new DatasetProtocolTiming
                {
                    preparationSeconds = 3f, cueSeconds = 1f, preStimulusRestSeconds = 0.5f,
                    stimulusSeconds = 4f, postStimulusRestSeconds = 1f,
                    breakAfterTrials = new int[0], breakSeconds = 0f
                },
                trials = new DatasetTrialPlanItem[30]
            };
            string[] targets = { "target_left", "target_center", "target_right" };
            float[] frequencies = { 7.2f, 9f, 12f };
            for (int i = 0; i < plan.trials.Length; i++)
            {
                int classIndex = i % targets.Length;
                plan.trials[i] = new DatasetTrialPlanItem
                {
                    sessionId = plan.sessionId,
                    trialId = "m6-demo-trial-" + (i + 1).ToString("D2"),
                    trialIndex = i,
                    targetId = targets[classIndex],
                    targetSide = classIndex == 0 ? "LEFT" : classIndex == 1 ? "CENTER" : "RIGHT",
                    nominalFrequencyHz = frequencies[classIndex],
                    expectedStimulusDurationSeconds = 4f
                };
            }
            return plan;
        }

        private IEnumerator RunPlan(DatasetTrialPlanMessage plan, bool demoMode)
        {
            m_Phase = Phase.Preparation;
            Debug.Log("M6DIAG enter_preparation countdownSeconds=" +
                (demoMode ? 3f : plan.protocol.preparationSeconds), this);
            yield return Countdown("PREPARATION\nGet ready", demoMode ? 3f : plan.protocol.preparationSeconds);
            var trials = demoMode ? SelectDemoTrials(plan.trials) : plan.trials;
            for (int i = 0; i < trials.Length; i++)
            {
                DatasetTrialPlanItem item = trials[i];
                m_Phase = Phase.Cue;
                Debug.Log("M6DIAG trial_armed trialId=" + item.trialId + " trialIndex=" + item.trialIndex +
                    " targetId=" + item.targetId, this);
                SetText(string.Format("Trial {0} / {1}\nLOOK {2}", i + 1, trials.Length, item.targetSide));
                Debug.Log("M6DIAG cue_shown targetId=" + item.targetId, this);
                yield return new WaitForSeconds(demoMode ? 1f : plan.protocol.cueSeconds);
                m_Phase = Phase.PreRest;
                SetText("READY");
                yield return new WaitForSeconds(demoMode ? 0.5f : plan.protocol.preStimulusRestSeconds);
                if (m_Stimulus == null || !m_Stimulus.IsIdle ||
                    !m_Stimulus.RequestStartTrial(item.trialId, item.targetId, item.trialIndex, item.nominalFrequencyHz))
                {
                    AbortSession("M5 stimulus controller was not idle for planned trial " + item.trialId);
                    yield break;
                }
                m_Phase = Phase.Stimulating;
                Debug.Log("M6DIAG enter_stimulating stimulus_start_requested trialId=" + item.trialId, this);
                SetText(string.Empty);
                yield return new WaitForSeconds(plan.protocol.stimulusSeconds);
                m_Stimulus.RequestStopTrial("m6_1b_fixed_stimulus_duration");
                float timeout = 2f;
                while (!m_Stimulus.IsIdle && timeout > 0f)
                {
                    timeout -= Time.deltaTime;
                    yield return null;
                }
                m_Phase = Phase.PostRest;
                SetText("REST");
                yield return new WaitForSeconds(demoMode ? 1f : plan.protocol.postStimulusRestSeconds);
                if (!demoMode && Array.IndexOf(plan.protocol.breakAfterTrials, item.trialIndex) >= 0)
                {
                    m_Phase = Phase.Break;
                    yield return Countdown("BREAK", plan.protocol.breakSeconds);
                }
            }
            m_Phase = Phase.Complete;
            SetText("SESSION COMPLETE");
            m_Run = null;
        }

        private static DatasetTrialPlanItem[] SelectDemoTrials(DatasetTrialPlanItem[] plan)
        {
            var selected = new List<DatasetTrialPlanItem>();
            string[] targets = { "target_left", "target_center", "target_right" };
            foreach (string target in targets)
            {
                for (int i = 0; i < plan.Length; i++)
                {
                    if (plan[i].targetId == target)
                    {
                        selected.Add(plan[i]);
                        break;
                    }
                }
            }
            return selected.ToArray();
        }

        private IEnumerator Countdown(string title, float seconds)
        {
            float remaining = seconds;
            while (remaining > 0f)
            {
                int shown = Mathf.CeilToInt(remaining);
                SetText(title + "\n" + shown);
                yield return null;
                remaining -= Time.deltaTime;
            }
        }

        public void AbortSession(string reason)
        {
            if (m_Run != null) StopCoroutine(m_Run);
            m_Run = null;
            if (m_Stimulus != null && m_Stimulus.IsStimulating)
                m_Stimulus.RequestStopTrial("m6_1b_aborted");
            m_Phase = Phase.Aborted;
            SetText("SESSION ABORTED\n" + reason);
            Debug.LogError("M6.1b dataset session aborted: " + reason, this);
        }

        private bool ValidatePlan(DatasetTrialPlanMessage plan, out string error)
        {
            error = string.Empty;
            if (plan == null || plan.protocol == null || plan.trials == null || plan.trials.Length != 30)
            { error = "expected exactly 30 trials"; return false; }
            var counts = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int i = 0; i < plan.trials.Length; i++)
            {
                DatasetTrialPlanItem item = plan.trials[i];
                if (item == null || item.trialIndex != i + 1 || item.sessionId != plan.sessionId ||
                    string.IsNullOrEmpty(item.trialId) || !m_SeenTrialIds.Add(item.trialId))
                { error = "trial identity/order is invalid"; return false; }
                if (item.targetId != "target_left" && item.targetId != "target_center" && item.targetId != "target_right")
                { error = "unexpected target id"; return false; }
                if (!counts.ContainsKey(item.targetId)) counts[item.targetId] = 0;
                counts[item.targetId]++;
            }
            if (!counts.ContainsKey("target_left") || counts["target_left"] != 10 ||
                !counts.ContainsKey("target_center") || counts["target_center"] != 10 ||
                !counts.ContainsKey("target_right") || counts["target_right"] != 10)
            { error = "class balance is not 10/10/10"; return false; }
            return true;
        }

        private void CreateCueText()
        {
            GameObject cue = new GameObject("M6DatasetCueText");
            cue.transform.SetParent(m_CueAnchor, false);
            cue.transform.localPosition = new Vector3(0f, 0f, m_CueDistance);
            m_Text = cue.AddComponent<TextMesh>();
            m_Text.anchor = TextAnchor.MiddleCenter;
            m_Text.alignment = TextAlignment.Center;
            m_Text.fontSize = 64;
            m_Text.characterSize = 0.06f;
            m_Text.color = Color.white;
        }

        private void SetText(string value)
        {
            if (m_Text != null) m_Text.text = value;
        }

        private void OnApplicationPause(bool paused)
        {
            if (paused && m_Phase != Phase.Complete && m_Phase != Phase.Aborted)
                AbortSession("application paused");
        }
    }
}
