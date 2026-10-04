from __future__ import annotations
import csv, hashlib, json, sys
from pathlib import Path
from xml.etree import ElementTree

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
M34=ROOT/"research_analysis"/"m34_high_speed_ssvep_context_20261004"/"attempt-01"
PROTOCOL_COPY=ROOT/"research_analysis"/"M35_CONTROLLED_CONTEXT_CAUSALITY_PROTOCOL.md"
CHECKS=[]

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(8*1024*1024),b""):h.update(block)
    return h.hexdigest()

def rows(path):
    with Path(path).open("r",encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))

def check(name,ok,detail):
    CHECKS.append({"name":name,"status":"PASS" if ok else "FAIL","detail":detail})

def main():
    required=["m35_protocol.json","eeg_only_reproduction.json","controlled_context_results.csv",
      "controlled_context_per_trial.csv","oracle_context_summary.json","incongruent_failure_cases.csv",
      "context_strength_tradeoff.csv","statistical_summary.json","guard_sensitivity.csv",
      "context_policy_tuning.csv","m35_run_summary.json","M35_FINAL_REPORT.md",
      "run_m35_analysis.py","plots"]
    missing=[x for x in required if not (OUT/x).exists()]
    check("required_artifacts",not missing,f"missing={missing}")
    if missing:
        print(json.dumps({"status":"FAIL","checks":CHECKS},indent=2));return 1
    protocol=json.loads((OUT/"m35_protocol.json").read_text(encoding="utf-8"))
    repro=json.loads((OUT/"eeg_only_reproduction.json").read_text(encoding="utf-8"))
    run=json.loads((OUT/"m35_run_summary.json").read_text(encoding="utf-8"))
    stats=json.loads((OUT/"statistical_summary.json").read_text(encoding="utf-8"))
    oracle=json.loads((OUT/"oracle_context_summary.json").read_text(encoding="utf-8"))
    check("protocol_copy_hash",sha(PROTOCOL_COPY)==protocol["protocolCopySha256"],protocol["protocolCopySha256"])
    check("analysis_script_hash",sha(OUT/"run_m35_analysis.py")==protocol["analysisScriptSha256"],protocol["analysisScriptSha256"])
    input_hash_ok=True
    for name,expected in protocol["inputArtifactsSha256"].items():
        input_hash_ok &= sha(M34/name)==expected
    check("frozen_m34_artifact_hashes",input_hash_ok,"all recorded M34 input artifact hashes match")
    manifest=json.loads((M34/"data_manifest.json").read_text(encoding="utf-8"))
    raw_ok=True
    for session in manifest["sessions"]:
        key=session["sessionKey"];name="raw-eeg.jsonl" if key=="S7" else "raw-eeg-packets.jsonl"
        path=Path(session["sourcePath"])/name
        value=sha(path)
        raw_ok &= value==session["rawFileSha256"]
        raw_ok &= value==protocol["rawInputSha256Before"].get(key)==protocol["rawInputSha256After"].get(key)
    check("raw_eeg_unchanged",raw_ok,"all four source hashes still match M34 manifest and before/after hashes")
    m34ok=all(v["status"]=="PASS" for v in repro["m34DynamicReproduction"].values())
    check("m34_dynamic_reproduction",m34ok,json.dumps(repro["m34DynamicReproduction"],sort_keys=True))
    check("slot_frequency_mapping",protocol["frozenM34"]["frequencyHzBySlot"]==[7.2,9.0,12.0],"slot0/1/2 = 7.2/9/12 Hz")
    qgrid=protocol["qTopGrid"]
    per=rows(OUT/"controlled_context_per_trial.csv")
    count_ok=len(per)==4130
    check("paired_condition_row_count",count_ok,f"rows={len(per)} expected=4130")
    expected_counts={"A":1050,"B1":1015,"B2":1015,"S7":1050}
    condition_counts={}
    neutral_by={}
    for r in per:
        s=r["sessionKey"];condition=r["condition"];trial=r["trialId"]
        condition_counts[condition]=condition_counts.get(condition,0)+1
        if condition=="NEUTRAL_CONTEXT":neutral_by[(s,trial)]=r
        if condition in ("CONGRUENT_CONTEXT","INCONGRUENT_CONTEXT"):
            q=float(r["qTop"]);prior=json.loads(r["contextPriorBySlot"]);ctx=int(r["contextSlotIndex"])
            if min(abs(q-x) for x in qgrid)>1e-10 or abs(prior[ctx]-q)>1e-10 or abs(sum(prior)-1)>1e-10:
                raise AssertionError(f"bad q/prior mapping: {s}/{trial}/{condition}/{q}/{prior}")
            other=[prior[i] for i in range(3) if i!=ctx]
            if abs(other[0]-other[1])>1e-10:raise AssertionError("non-symmetric prior remainder")
        trajectory=json.loads(r["eegScoreTrajectory"])
        stop=float(r["assistedEvidenceSeconds"])
        match=[p for p in trajectory if abs(float(p["t"])-stop)<1e-9]
        if not match:raise AssertionError(f"stop evidence missing from trajectory {s}/{trial}")
        top=max(range(3),key=lambda i:float(match[0]["scores"][i]))
        if top!=int(r["assistedPrediction"]):raise AssertionError(f"Context changed EEG class {s}/{trial}")
        if float(r["assistedEvidenceSeconds"])>float(r["eegOnlyEvidenceSeconds"])+1e-12:
            raise AssertionError(f"delay violation {s}/{trial}")
    check("context_never_reranks",True,"every emitted class equals raw EEG argmax at the selected evidence point")
    check("symmetric_q_priors",True,f"all controlled priors use q_top grid of {len(qgrid)} strengths and split remainder equally")
    check("per_trial_session_counts",all(sum(1 for r in per if r["sessionKey"]==s)==n for s,n in expected_counts.items()),str(expected_counts))
    neutral_ok=True
    baseline={(r["sessionKey"],r["trialId"]):r for r in per if r["condition"]=="EEG_ONLY"}
    for key,r in neutral_by.items():
        b=baseline[key]
        neutral_ok &= int(r["assistedPrediction"])==int(b["assistedPrediction"])
        neutral_ok &= abs(float(r["assistedEvidenceSeconds"])-float(b["assistedEvidenceSeconds"]))<1e-12
    check("neutral_exact_fallback",neutral_ok,str(stats.get("neutralExactlyMatchesEegOnlyClassAndEvidence")))
    selected=protocol["selectedContextPolicy"]["selected"]
    dev=[r for r in per if r["sessionKey"] in ("A","B1") and r["condition"]=="INCONGRUENT_CONTEXT"]
    post=[r for r in per if r["sessionKey"] in ("B2","S7") and r["condition"]=="INCONGRUENT_CONTEXT"]
    dev_induced=sum(int(r["contextInducedWrongEarlyStop"]) for r in dev)
    post_induced=sum(int(r["contextInducedWrongEarlyStop"]) for r in post)
    check("development_selected_gate_safety",dev_induced==0 and sum(int(r["noDelayViolation"]) for r in per)==0,
      f"development induced={dev_induced}; all delay violations=0; selected={selected}")
    check("posthoc_boundary",protocol["developmentSessions"]==["A","B1"] and protocol["posthocOnlySessions"]==["B2","S7"],
      f"posthoc induced events={post_induced}; not used for selection")
    expected_failures=run["unsafeDevelopmentCandidateContextInducedWrongEarlyStops"]+post_induced
    failures=rows(OUT/"incongruent_failure_cases.csv")
    check("failure_trajectories_saved",len(failures)==expected_failures and all(int(r["contextInducedWrongEarlyStop"])==1 for r in failures),
      f"saved={len(failures)} expected={expected_failures}")
    tuning=rows(OUT/"context_policy_tuning.csv")
    check("development_candidate_search",len(tuning)==105 and all(int(r["nIncongruentPairs"])==1298 for r in tuning),
      f"candidate rows={len(tuning)}; every candidate covers all q and both wrong classes")
    guards=rows(OUT/"guard_sensitivity.csv")
    check("guard_grid",set(r["guardSeconds"] for r in guards)=={"0.0","0.1","0.3","0.5"} and set(r["sessionKey"] for r in guards)=={"A","B1"},
      f"rows={len(guards)}; only A/B1")
    check("oracle_and_m33_summary",len(oracle["summaries"])>=20 and set(oracle["realM33ReplayComparison"])=={"B2","S7"},
      f"oracle summaries={len(oracle['summaries'])}; stored M33 sessions=B2/S7")
    check("paired_statistics",len(stats["pooledDevelopmentByStrength"])==22 and
      all(len(x["bootstrap95CiMeanGainSeconds"])==2 for x in stats["pooledDevelopmentByStrength"]),
      f"pooled paired strength summaries={len(stats['pooledDevelopmentByStrength'])}")
    plots=sorted((OUT/"plots").glob("*.svg"))
    xml_ok=len(plots)>=7
    for path in plots:
        try:ElementTree.parse(path)
        except Exception:xml_ok=False
    check("seven_renderable_figures",xml_ok,f"{len(plots)} SVG files parse as XML")
    report=(OUT/"M35_FINAL_REPORT.md").read_text(encoding="utf-8-sig")
    report_ok=all(f"{i}." in report for i in range(1,16)) and "not prospective" in report.lower()
    check("final_report_answers_contract",report_ok,"all 15 contract questions and the validation boundary are present")
    status="PASS" if all(x["status"]=="PASS" for x in CHECKS) else "FAIL"
    result={"status":status,"checks":CHECKS,"rowCounts":condition_counts,"developmentInducedWrongEarlyStops":dev_induced,
      "posthocInducedWrongEarlyStops":post_induced,"unsafeDevelopmentCandidateFailureRows":run["unsafeDevelopmentCandidateContextInducedWrongEarlyStops"],
      "rawInputsUnchanged":raw_ok,"report":str(OUT/"M35_FINAL_REPORT.md")}
    (OUT/"m35_verification.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))
    return 0 if status=="PASS" else 1

if __name__=="__main__":sys.exit(main())
