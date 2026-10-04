from __future__ import annotations
import csv, hashlib, json, math, random, statistics, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
M34 = ROOT / "research_analysis" / "m34_high_speed_ssvep_context_20261004" / "attempt-01"
PROTOCOL_COPY = ROOT / "research_analysis" / "M35_CONTROLLED_CONTEXT_CAUSALITY_PROTOCOL.md"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(M34))
import run_train_dev_search as m34

SCHEDULE = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00]
GUARDS = [0.0, 0.1, 0.3, 0.5]
Q_GRID = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
FREQUENCIES = [7.2, 9.0, 12.0]
CHANNELS = [2, 3, 4, 5, 7]
MIN_EVIDENCE, BASE_MARGIN, BASE_STABLE, MAX_EVIDENCE = 0.20, 0.175, 2, 1.0
ALPHAS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
FLOORS = [0.0, 0.025, 0.05, 0.075, 0.10]
TOL = 1e-12

def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path, rows):
    fields = list(rows[0]) if rows else []
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def top_margin(scores):
    values = [float(x) for x in scores]
    order = sorted(range(3), key=lambda i: (-values[i], i))
    return order[0], order[1], (values[order[0]] - values[order[1]]) / max(abs(values[order[0]]), 1e-12)

def point(window, scores):
    top, second, margin = top_margin(scores)
    return {"evidenceSeconds": float(window), "scoresBySlot": [float(x) for x in scores],
            "topSlotIndex": int(top), "secondSlotIndex": int(second), "relativeMargin": float(margin)}

def load_traces():
    by = defaultdict(dict)
    for row in read_csv(M34 / "train_dev_search_per_trial.csv"):
        if row["configId"] == "FBCCA-003" and row["sessionKey"] == "B1":
            t = by["B1"].setdefault(row["trialId"], {"sessionKey":"B1","trialId":row["trialId"],
                "trueSlotIndex":int(row["trueSlotIndex"]), "points":{}})
            t["points"][float(row["evidenceSeconds"])] = point(float(row["evidenceSeconds"]), json.loads(row["scoresBySlot"]))
    for row in read_csv(M34 / "heldout_fixed_per_trial.csv"):
        session = row["sessionKey"]
        expected_channels = CHANNELS if session == "B2" else [2, 4, 7]
        if row["decoder"] == "FBCCA" and session in ("B2","S7") and json.loads(row["channelIndices"]) == expected_channels:
            t = by[row["sessionKey"]].setdefault(row["trialId"], {"sessionKey":row["sessionKey"],"trialId":row["trialId"],
                "trueSlotIndex":int(row["trueSlotIndex"]), "points":{}})
            t["points"][float(row["evidenceSeconds"])] = point(float(row["evidenceSeconds"]), json.loads(row["scoresBySlot"]))
    raw_a = m34.load_raw("A")
    trials_a = [t for t in m34.ALL_TRIALS if t["sessionKey"] == "A" and t["usable"]]
    if len(trials_a) != 30:
        raise ValueError(f"A usable trial count mismatch: {len(trials_a)}")
    for trial in trials_a:
        t = {"sessionKey":"A","trialId":trial["trialId"],"trueSlotIndex":int(trial["slotIndex"]),"points":{}}
        for window in SCHEDULE:
            epoch = m34.make_raw_epoch(raw_a, trial, 0.5, window, tuple(range(8)))
            _, scores, _ = m34.predict_fbcca(epoch[CHANNELS], FREQUENCIES, 3, 1000.0)
            t["points"][window] = point(window, scores)
        by["A"][trial["trialId"]] = t
    expected = {"A":30,"B1":29,"B2":29,"S7":30}
    for session, n in expected.items():
        if len(by[session]) != n:
            raise ValueError(f"{session} trial count mismatch: {len(by[session])} != {n}")
        for t in by[session].values():
            if set(t["points"]) != set(SCHEDULE):
                raise ValueError(f"incomplete score trajectory {session}/{t['trialId']}")
    return by, {"A":raw_a}

def dynamic_stop(t, margin=BASE_MARGIN, stable=BASE_STABLE):
    for i, window in enumerate(SCHEDULE):
        if window < MIN_EVIDENCE - TOL: continue
        p = t["points"][window]
        start = i - stable + 1
        if start < 0: continue
        if all(t["points"][SCHEDULE[j]]["topSlotIndex"] == p["topSlotIndex"] for j in range(start, i+1)) and p["relativeMargin"] + TOL >= margin:
            return dict(p)
    return dict(t["points"][MAX_EVIDENCE])

def baselines(by):
    return {s:{tid:dynamic_stop(t) for tid,t in ts.items()} for s,ts in by.items()}

def check_m34_reproduction(by, base):
    refs = {
        "B1": {r["trialId"]:r for r in read_csv(M34/"dynamic_stopping_selected_per_trial.csv")},
    }
    held = read_csv(M34/"heldout_dynamic_per_trial.csv")
    refs["B2"] = {r["trialId"]:r for r in held if r["sessionKey"]=="B2"}
    refs["S7"] = {r["trialId"]:r for r in held if r["sessionKey"]=="S7"}
    checks = {}
    for s in ("B1","B2","S7"):
        if set(refs[s]) != set(base[s]): raise ValueError(f"M34 trial ids differ in {s}")
        for tid,p in base[s].items():
            r = refs[s][tid]
            if int(p["topSlotIndex"]) != int(r["predictedSlotIndex"]) or abs(float(p["evidenceSeconds"])-float(r["evidenceSeconds"]))>TOL:
                raise ValueError(f"M34 dynamic decision mismatch {s}/{tid}")
            if not np.allclose(p["scoresBySlot"], json.loads(r["scoresBySlot"]), rtol=0, atol=1e-12):
                raise ValueError(f"M34 stop score mismatch {s}/{tid}")
        checks[s]={"status":"PASS","n":len(base[s]),"matchedClassAndWindow":len(base[s]),"selectedScoreAtol":1e-12}
    return checks

def cohort(s):
    return "development_A_B1" if s in ("A","B1") else ("posthoc_primary_B2" if s=="B2" else "posthoc_stress_S7")

def prior(ctx,q):
    return [float(q if i==ctx else (1-q)/2) for i in range(3)]

def simulate(t, base, ctx, q, alpha, floor, stable_updates=BASE_STABLE):
    if ctx is None: return dict(base), False
    req = max(float(floor), BASE_MARGIN - float(alpha)*(float(q)-1/3))
    bt = float(base["evidenceSeconds"])
    for i, w in enumerate(SCHEDULE):
        if w < MIN_EVIDENCE-TOL or w >= bt-TOL: continue
        p = t["points"][w]
        start=i-int(stable_updates)+1
        if start<0: continue
        stable=all(t["points"][SCHEDULE[j]]["topSlotIndex"]==p["topSlotIndex"] for j in range(start,i+1))
        if stable and p["topSlotIndex"]==int(ctx) and p["relativeMargin"]+TOL>=req:
            return dict(p), True
    return dict(base), False

def result_row(s,t,base,condition,q=None,ctx=None,alpha=0.0,floor=0.0,stable_updates=BASE_STABLE):
    p, authorized = (dict(base),False) if condition in ("EEG_ONLY","NEUTRAL_CONTEXT") else simulate(t,base,ctx,q,alpha,floor,stable_updates)
    bpred, pred = int(base["topSlotIndex"]), int(p["topSlotIndex"])
    bt, st = float(base["evidenceSeconds"]), float(p["evidenceSeconds"])
    applied = bool(authorized and st < bt-TOL)
    true = int(t["trueSlotIndex"])
    induced = int(bpred==true and pred!=true and st<bt-TOL)
    return {
      "cohort":cohort(s),"sessionKey":s,"trialId":t["trialId"],"trueSlotIndex":true,"condition":condition,
      "qTop":"" if q is None else float(q),"contextSlotIndex":"" if ctx is None else int(ctx),
      "contextPriorBySlot":json.dumps(prior(ctx,q) if ctx is not None else [1/3,1/3,1/3],separators=(",",":")),
      "alpha":float(alpha),"marginFloor":float(floor),"contextStableUpdates":int(stable_updates),"eegOnlyPrediction":bpred,"eegOnlyEvidenceSeconds":bt,
      "eegOnlyCorrect":int(bpred==true),"assistedPrediction":pred,"assistedEvidenceSeconds":st,
      "assistedCorrect":int(pred==true),"pairedGainSeconds":bt-st,"contextAuthorizedEarlier":int(applied),
      "contextApplied":int(applied),"exactEegOnlyFallback":int(not applied and pred==bpred and abs(st-bt)<=TOL),
      "wrongEarlyStop":int(pred!=true and st<MAX_EVIDENCE-TOL),"contextInducedWrongEarlyStop":induced,
      "noDelayViolation":int(st>bt+TOL),
      "eegScoreTrajectory":json.dumps([{"t":w,"scores":t["points"][w]["scoresBySlot"],
        "top":t["points"][w]["topSlotIndex"],"margin":t["points"][w]["relativeMargin"]} for w in SCHEDULE],separators=(",",":"))
    }

def search_policy(by,base):
    tuning=[]; safe=[]; failures=[]
    for stable in (2,3,4):
     for alpha in ALPHAS:
      for floor in FLOORS:
        inc=[]; congr=[]; induced=0
        for s in ("A","B1"):
          for tid,t in by[s].items():
            b=base[s][tid]
            for q in Q_GRID:
              congr.append(result_row(s,t,b,"CONGRUENT_CONTEXT",q,int(t["trueSlotIndex"]),alpha,floor,stable))
              for wrong in range(3):
                if wrong!=int(t["trueSlotIndex"]):
                  inc.append(result_row(s,t,b,"INCONGRUENT_CONTEXT",q,wrong,alpha,floor,stable))
        failures.extend({**r,"candidateDisposition":"unsafe development candidate; retained for mechanistic failure audit"}
          for r in inc if int(r["contextInducedWrongEarlyStop"]))
        induced=sum(int(r["contextInducedWrongEarlyStop"]) for r in inc)
        base_correct=sum(int(r["eegOnlyCorrect"]) for r in inc)
        assist_correct=sum(int(r["assistedCorrect"]) for r in inc)
        gain=statistics.fmean(float(r["pairedGainSeconds"]) for r in congr)
        app=statistics.fmean(int(r["contextApplied"]) for r in congr)
        item={"contextStableUpdates":stable,"alpha":alpha,"marginFloor":floor,"nIncongruentPairs":len(inc),
              "contextInducedWrongEarlyStops":induced,"baselineCorrect":base_correct,
              "incongruentCorrect":assist_correct,"incongruentAccuracyNoLowerThanBaseline":assist_correct>=base_correct,
              "meanCongruentGainSeconds":gain,"congruentApplicationRate":app,
              "safe":induced==0 and assist_correct>=base_correct}
        tuning.append(item)
        if item["safe"]: safe.append(item)
    if not safe:
      selected={"alpha":0.0,"marginFloor":BASE_MARGIN,"selection":"exact EEG-only fallback; no candidate met safety constraints"}
    else:
      safe.sort(key=lambda r:(-r["meanCongruentGainSeconds"],-r["congruentApplicationRate"],-r["contextStableUpdates"],-r["marginFloor"],r["alpha"]))
      top=safe[0]
      if top["meanCongruentGainSeconds"]<=TOL:
        selected={"alpha":0.0,"marginFloor":BASE_MARGIN,"selection":"exact EEG-only fallback; safe candidates yielded no acceleration"}
      else:
        selected={"alpha":float(top["alpha"]),"marginFloor":float(top["marginFloor"]),"contextStableUpdates":int(top["contextStableUpdates"]),
          "selection":"A/B1 only: greatest congruent gain among rules with zero incongruent-induced wrong early stops and no incongruent accuracy loss"}
    return selected,tuning,failures
def summarize(rows):
    if not rows: return {"n":0}
    times=[float(r["assistedEvidenceSeconds"]) for r in rows]
    gains=[float(r["pairedGainSeconds"]) for r in rows]
    n=len(rows); applied=[float(r["pairedGainSeconds"]) for r in rows if int(r["contextApplied"])]
    return {"n":n,"correct":sum(int(r["assistedCorrect"]) for r in rows),"accuracy":statistics.fmean(int(r["assistedCorrect"]) for r in rows),
      "meanEvidenceSeconds":statistics.fmean(times),"medianEvidenceSeconds":statistics.median(times),
      "p90EvidenceSeconds":float(np.quantile(times,.9,method="linear")),"meanPairedGainSeconds":statistics.fmean(gains),
      "medianPairedGainSeconds":statistics.median(gains),
      "fractionLE020":sum(x<=.20+TOL for x in times)/n,"fractionLE025":sum(x<=.25+TOL for x in times)/n,
      "fractionLE030":sum(x<=.30+TOL for x in times)/n,"fractionLE040":sum(x<=.40+TOL for x in times)/n,
      "fractionLE050":sum(x<=.50+TOL for x in times)/n,
      "wrongEarlyStopCount":sum(int(r["wrongEarlyStop"]) for r in rows),
      "contextInducedWrongEarlyStopCount":sum(int(r["contextInducedWrongEarlyStop"]) for r in rows),
      "exactEegOnlyFallbackRate":sum(int(r["exactEegOnlyFallback"]) for r in rows)/n,
      "contextApplicationRate":sum(int(r["contextApplied"]) for r in rows)/n,
      "meanAppliedTrialGainSeconds":statistics.fmean(applied) if applied else None,
      "medianAppliedTrialGainSeconds":statistics.median(applied) if applied else None,
      "noDelayViolations":sum(int(r["noDelayViolation"]) for r in rows)}

def expand(by,base,alpha,floor,stable_updates=BASE_STABLE):
    rows=[]
    for s, trials in by.items():
      for tid,t in trials.items():
        b=base[s][tid]
        rows.append(result_row(s,t,b,"EEG_ONLY"))
        rows.append(result_row(s,t,b,"NEUTRAL_CONTEXT",1/3,None,alpha,floor))
        for q in Q_GRID:
          rows.append(result_row(s,t,b,"CONGRUENT_CONTEXT",q,int(t["trueSlotIndex"]),alpha,floor,stable_updates))
          for c in range(3):
            if c!=int(t["trueSlotIndex"]): rows.append(result_row(s,t,b,"INCONGRUENT_CONTEXT",q,c,alpha,floor,stable_updates))
    return rows

def aggregate(rows):
    groups=defaultdict(list)
    for r in rows:
      if r["condition"]=="EEG_ONLY": k=(r["sessionKey"],r["condition"],"", "")
      elif r["condition"]=="NEUTRAL_CONTEXT": k=(r["sessionKey"],r["condition"],"neutral","")
      else: k=(r["sessionKey"],r["condition"],f'{float(r["qTop"]):.2f}',str(r["contextSlotIndex"]))
      groups[k].append(r)
    out=[]
    for (s,condition,q,ctx),rs in groups.items():
      x=summarize(rs)
      out.append({"cohort":cohort(s),"sessionKey":s,"condition":condition,"qTop":q,"contextSlotIndex":ctx,
        "alpha":rs[0]["alpha"],"marginFloor":rs[0]["marginFloor"],**x})
    return out

def bootstrap_ci(v,seed,n_iter=10000):
    if not v:return [None,None]
    rng=random.Random(seed); n=len(v); vals=[]
    for _ in range(n_iter): vals.append(sum(float(v[rng.randrange(n)]) for _ in range(n))/n)
    vals.sort()
    return [vals[int(.025*(n_iter-1))],vals[int(.975*(n_iter-1))]]

def signflip_p(v,seed,n_iter=100000):
    if not v:return None
    values=np.asarray(v,dtype=float);obs=abs(float(np.mean(values)));rng=np.random.default_rng(seed);n=len(values);ext=0
    for start in range(0,n_iter,5000):
      count=min(5000,n_iter-start)
      signs=(rng.integers(0,2,size=(count,n),dtype=np.int8)*2-1).astype(float)
      ext+=int(np.sum(np.abs(signs@values/n)+TOL>=obs))
    return (ext+1)/(n_iter+1)

def mcnemar(base,assisted):
    b=sum(int(x)==1 and int(y)==0 for x,y in zip(base,assisted))
    c=sum(int(x)==0 and int(y)==1 for x,y in zip(base,assisted)); n=b+c
    p=1.0 if n==0 else min(1.0,2*sum(math.comb(n,i) for i in range(min(b,c)+1))/(2**n))
    return {"baselineCorrect_assistedWrong":b,"baselineWrong_assistedCorrect":c,"discordantPairs":n,"twoSidedExactP":p}

def paired_statistics(rows):
    dev=[r for r in rows if r["sessionKey"] in ("A","B1")]
    base={(r["sessionKey"],r["trialId"]):r for r in dev if r["condition"]=="EEG_ONLY"}
    groups=defaultdict(list)
    for r in dev:
      if r["condition"] in ("CONGRUENT_CONTEXT","INCONGRUENT_CONTEXT"):
        groups[(r["condition"],f'{float(r["qTop"]):.2f}',str(r["contextSlotIndex"]))].append(r)
    result=[]
    for i,(k,rs) in enumerate(sorted(groups.items())):
      b=[int(base[(r["sessionKey"],r["trialId"])]["assistedCorrect"]) for r in rs]
      a=[int(r["assistedCorrect"]) for r in rs]; gains=[float(r["pairedGainSeconds"]) for r in rs]
      result.append({"condition":k[0],"qTop":float(k[1]),"contextSlotIndex":k[2],"pairedN":len(rs),
        "meanPairedGainSeconds":statistics.fmean(gains),"medianPairedGainSeconds":statistics.median(gains),
        "bootstrap95CiMeanGainSeconds":bootstrap_ci(gains,35000+i),
        "pairedSignFlipTwoSidedMonteCarloP":signflip_p(gains,36000+i),
        "accuracy":statistics.fmean(a),"pairedAccuracyDelta":statistics.fmean(y-x for x,y in zip(b,a)),
        "mcnemarExact":mcnemar(b,a),"contextInducedWrongEarlyStopCount":sum(int(r["contextInducedWrongEarlyStop"]) for r in rs)})
    neutral=[r for r in dev if r["condition"]=="NEUTRAL_CONTEXT"]
    exact=all(int(r["assistedPrediction"])==int(base[(r["sessionKey"],r["trialId"])]["assistedPrediction"])
      and abs(float(r["assistedEvidenceSeconds"])-float(base[(r["sessionKey"],r["trialId"])]["assistedEvidenceSeconds"]))<=TOL for r in neutral)
    pooled=[]
    for condition in ("CONGRUENT_CONTEXT","INCONGRUENT_CONTEXT"):
      for q in Q_GRID:
        selected=[r for r in dev if r["condition"]==condition and abs(float(r["qTop"])-q)<TOL]
        by_trial=defaultdict(list)
        for r in selected:by_trial[(r["sessionKey"],r["trialId"])].append(r)
        gains=[];base_correct=[];assisted_ok=[];apps=[]
        for key,rs in by_trial.items():
          gains.append(statistics.fmean(float(r["pairedGainSeconds"]) for r in rs))
          base_correct.append(int(base[key]["assistedCorrect"]))
          assisted_ok.append(int(all(int(r["assistedCorrect"]) for r in rs)))
          apps.append(int(any(int(r["contextApplied"]) for r in rs)))
        pooled.append({"condition":condition,"qTop":q,"pairedTrialN":len(by_trial),
          "meanPairedGainSeconds":statistics.fmean(gains),"medianPairedGainSeconds":statistics.median(gains),
          "bootstrap95CiMeanGainSeconds":bootstrap_ci(gains,47000+len(pooled)),
          "pairedSignFlipTwoSidedMonteCarloP":signflip_p(gains,48000+len(pooled)),
          "conservativeAccuracy":statistics.fmean(assisted_ok),
          "meanPairedAccuracyDelta":statistics.fmean(y-x for x,y in zip(base_correct,assisted_ok)),
          "mcnemarExactConservative":mcnemar(base_correct,assisted_ok),
          "contextApplicationRateByTrial":statistics.fmean(apps),
          "contextInducedWrongEarlyStopEvents":sum(int(r["contextInducedWrongEarlyStop"]) for r in selected),
          "wrongContextAggregation":"Incongruent gains are averaged within EEG trial; conservative correctness requires both wrong-target replays to remain correct."})
    return {"method":{"latency":"paired trial bootstrap 95% percentile CI, 10,000 deterministic resamples; paired sign-flip permutation, 100,000 Monte Carlo resamples with +1 correction",
      "accuracy":"exact two-sided McNemar test","smallSample":"Descriptive controlled replay; no population-level inference."},
      "developmentSessions":["A","B1"],"posthocNotUsedForSelection":["B2","S7"],"pooledDevelopmentByStrength":pooled,
      "neutralExactlyMatchesEegOnlyClassAndEvidence":exact,"results":result}

def oracle_analysis(by,base):
    specs=[("oracle_any_agreement_1",1,0.0),("oracle_stable2_no_margin",2,0.0),
      ("oracle_stable1_margin005",1,.05),("oracle_stable2_margin005",2,.05),
      ("oracle_stable2_baseline_margin",2,BASE_MARGIN)]
    trial_rows=[]; summary=[]
    for s,ts in by.items():
      for tid,t in ts.items():
        b=base[s][tid]
        for name,stable,margin in specs:
          chosen=None
          for i,w in enumerate(SCHEDULE):
            if w<MIN_EVIDENCE-TOL or w>=float(b["evidenceSeconds"])-TOL:continue
            p=t["points"][w]; start=i-stable+1
            if start<0:continue
            is_stable=all(t["points"][SCHEDULE[j]]["topSlotIndex"]==p["topSlotIndex"] for j in range(start,i+1))
            if is_stable and p["topSlotIndex"]==int(t["trueSlotIndex"]) and p["relativeMargin"]+TOL>=margin:
              chosen=p;break
          p=chosen or b
          trial_rows.append({"cohort":cohort(s),"sessionKey":s,"trialId":tid,"condition":name,
            "trueSlotIndex":int(t["trueSlotIndex"]),"eegOnlyPrediction":int(b["topSlotIndex"]),
            "eegOnlyEvidenceSeconds":float(b["evidenceSeconds"]),"assistedPrediction":int(p["topSlotIndex"]),
            "assistedEvidenceSeconds":float(p["evidenceSeconds"]),"assistedCorrect":int(p["topSlotIndex"]==int(t["trueSlotIndex"])),
            "pairedGainSeconds":float(b["evidenceSeconds"])-float(p["evidenceSeconds"]),"contextApplied":int(chosen is not None)})
    grouped=defaultdict(list)
    for r in trial_rows:grouped[(r["cohort"],r["sessionKey"],r["condition"])].append(r)
    for (c,s,name),rs in grouped.items():
      ts=[float(r["assistedEvidenceSeconds"]) for r in rs];g=[float(r["pairedGainSeconds"]) for r in rs]
      app=[float(r["pairedGainSeconds"]) for r in rs if int(r["contextApplied"])]
      summary.append({"cohort":c,"sessionKey":s,"condition":name,"n":len(rs),
        "accuracy":statistics.fmean(int(r["assistedCorrect"]) for r in rs),"meanEvidenceSeconds":statistics.fmean(ts),
        "medianEvidenceSeconds":statistics.median(ts),"p90EvidenceSeconds":float(np.quantile(ts,.9,method="linear")),
        "meanOracleGainSeconds":statistics.fmean(g),"meanGainAmongAccelerated":statistics.fmean(app) if app else None,
        "fractionLE030":sum(x<=.30+TOL for x in ts)/len(ts),
        "applicationRate":statistics.fmean(int(r["contextApplied"]) for r in rs),
        "interpretation":"Oracle Context is an upper-bound replay; emitted class remains raw EEG argmax."})
    return {"specifications":[{"condition":n,"stableUpdates":k,"minimumRelativeMargin":m} for n,k,m in specs],
      "summaries":summary,"perTrial":trial_rows}

def m33_summary():
    src=read_csv(M34/"context_heldout_per_trial_seed.csv")
    rows=[r for r in src if r["scenario"]=="precomputed_before_eeg" and r["sessionKey"] in ("B2","S7")]
    out={}
    for s in ("B2","S7"):
      rs=[r for r in rows if r["sessionKey"]==s]
      out[s]={"nTrialSeedPairs":len(rs),"meanBaselineStopSeconds":statistics.fmean(float(r["baselineStopSeconds"]) for r in rs),
        "meanM33ReplayStopSeconds":statistics.fmean(float(r["contextStopSeconds"]) for r in rs),
        "meanPairedGainSeconds":statistics.fmean(float(r["pairedGainSeconds"]) for r in rs),
        "applicationRate":statistics.fmean(int(r["contextApplied"]) for r in rs),
        "meanAppliedGainSeconds":statistics.fmean(float(r["pairedGainSeconds"]) for r in rs if int(r["contextApplied"])) if any(int(r["contextApplied"]) for r in rs) else None,
        "contextCausedErrorCount":sum(int(r["contextCausedError"]) for r in rs),
        "accuracy":statistics.fmean(int(r["contextCorrect"]) for r in rs),
        "source":"M34 precomputed M33 seeded historical transfer replay; post-hoc only, not prospective paired acquisition."}
    return out

def guard_analysis(raw_by):
    out=[]
    for s in ("A","B1"):
      raw=raw_by.get(s)
      if raw is None: raw=m34.load_raw(s)
      raw_by[s]=raw
      trials=[t for t in m34.ALL_TRIALS if t["sessionKey"]==s and t["usable"]]
      for guard in GUARDS:
        traces=[]
        for tr in trials:
          t={"sessionKey":s,"trialId":tr["trialId"],"trueSlotIndex":int(tr["slotIndex"]),"points":{}}
          for w in SCHEDULE:
            epoch=m34.make_raw_epoch(raw,tr,guard,w,tuple(range(8)))
            _,scores,_=m34.predict_fbcca(epoch[CHANNELS],FREQUENCIES,3,1000.0)
            t["points"][w]=point(w,scores)
          traces.append(t)
        for w in SCHEDULE:
          pred=[t["points"][w]["topSlotIndex"] for t in traces];truth=[t["trueSlotIndex"] for t in traces]
          recalls=[sum(pred[i]==slot for i,x in enumerate(truth) if x==slot)/sum(x==slot for x in truth) for slot in range(3)]
          out.append({"sessionKey":s,"guardSeconds":guard,"evidenceSeconds":w,"nominalOnsetRelativeDecisionSeconds":guard+w,
            "n":len(traces),"correct":sum(a==b for a,b in zip(pred,truth)),"accuracy":statistics.fmean(a==b for a,b in zip(pred,truth)),
            "balancedAccuracy":statistics.fmean(recalls),"perClassAccuracyBySlot":json.dumps(recalls,separators=(",",":")),
            "meanDynamicEvidenceSeconds":""})
        stops=[dynamic_stop(t) for t in traces]
        out.append({"sessionKey":s,"guardSeconds":guard,"evidenceSeconds":"dynamic_E200_M175_S2",
          "nominalOnsetRelativeDecisionSeconds":"guard + evidence, reported separately","n":len(traces),
          "correct":sum(p["topSlotIndex"]==t["trueSlotIndex"] for p,t in zip(stops,traces)),
          "accuracy":statistics.fmean(p["topSlotIndex"]==t["trueSlotIndex"] for p,t in zip(stops,traces)),
          "balancedAccuracy":"","perClassAccuracyBySlot":"","meanDynamicEvidenceSeconds":statistics.fmean(p["evidenceSeconds"] for p in stops)})
    return out
def esc(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def chart(path,title,series,xlabels,ymin,ymax,ylabel,xlabel="q_top"):
    W,H=1000,620;L,R,T,B=90,35,85,85;pw=W-L-R;ph=H-T-B
    colors=["#2463a6","#d05a37","#278a62","#8a55aa","#333333"]
    def X(i):return L+i*pw/max(1,len(xlabels)-1)
    def Y(v):return T+(ymax-float(v))/(ymax-ymin)*ph
    a=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
       '<rect width="100%" height="100%" fill="white"/>',
       f'<text x="{W/2}" y="35" text-anchor="middle" font-family="Arial" font-size="21" font-weight="bold">{esc(title)}</text>']
    for i in range(6):
      v=ymin+(ymax-ymin)*i/5;y=Y(v);a += [f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="#ddd"/>',
       f'<text x="{L-9}" y="{y+4:.1f}" text-anchor="end" font-family="Arial" font-size="12">{v:.3g}</text>']
    a += [f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H-B}" stroke="#333"/>',
      f'<line x1="{L}" y1="{H-B}" x2="{W-R}" y2="{H-B}" stroke="#333"/>',
      f'<text x="{W/2}" y="{H-22}" text-anchor="middle" font-family="Arial" font-size="14">{esc(xlabel)}</text>',
      f'<text x="20" y="{H/2}" transform="rotate(-90 20 {H/2})" text-anchor="middle" font-family="Arial" font-size="14">{esc(ylabel)}</text>']
    for i,label in enumerate(xlabels):
      x=X(i);a.append(f'<text x="{x:.1f}" y="{H-B+20}" text-anchor="middle" font-family="Arial" font-size="11">{esc(label)}</text>')
    for si,line in enumerate(series):
      color=line.get("color",colors[si%len(colors)]);pts=[(i,v) for i,v in enumerate(line["values"]) if v is not None]
      if pts:
        d=" ".join(("M" if j==0 else "L")+f" {X(i):.1f} {Y(v):.1f}" for j,(i,v) in enumerate(pts))
        a.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3"/>')
        for i,v in pts:a.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4" fill="{color}"/>')
      lx=L+8+(si%2)*350;ly=T+15+(si//2)*22
      a += [f'<line x1="{lx}" y1="{ly}" x2="{lx+22}" y2="{ly}" stroke="{color}" stroke-width="3"/>',
       f'<text x="{lx+28}" y="{ly+5}" font-family="Arial" font-size="12">{esc(line["name"])}</text>']
    a.append("</svg>");Path(path).write_text("\n".join(a)+"\n",encoding="utf-8")

def make_figures(rows,oracle,m33):
    d="development_A_B1"; qs=[f"{q:.2f}" for q in Q_GRID]; plots=OUT/"plots";plots.mkdir(exist_ok=True)
    def selected(cond,q=None):
      return [r for r in rows if r["cohort"]==d and r["condition"]==cond and
        ((q is None and (r["qTop"]=="" or cond=="NEUTRAL_CONTEXT")) or
         (q is not None and r["qTop"]!="" and abs(float(r["qTop"])-q)<TOL))]
    base=[statistics.fmean(float(r["assistedEvidenceSeconds"]) for r in selected("EEG_ONLY"))]*len(Q_GRID)
    neutral=[statistics.fmean(float(r["assistedEvidenceSeconds"]) for r in selected("NEUTRAL_CONTEXT"))]*len(Q_GRID)
    cmeans=[];imeans=[];cg=[];ig=[];ca=[];ia=[];wrong=[];wrong_b2=[];wrong_s7=[];le300=[]
    for q in Q_GRID:
      c=selected("CONGRUENT_CONTEXT",q);i=selected("INCONGRUENT_CONTEXT",q)
      cmeans.append(statistics.fmean(float(r["assistedEvidenceSeconds"]) for r in c))
      imeans.append(statistics.fmean(float(r["assistedEvidenceSeconds"]) for r in i))
      cg.append(statistics.fmean(float(r["pairedGainSeconds"]) for r in c))
      ig.append(statistics.fmean(float(r["pairedGainSeconds"]) for r in i))
      ca.append(statistics.fmean(int(r["assistedCorrect"]) for r in c))
      ia.append(statistics.fmean(int(r["assistedCorrect"]) for r in i))
      wrong.append(sum(int(r["contextInducedWrongEarlyStop"]) for r in i)/len(i))
      b2=[r for r in rows if r["sessionKey"]=="B2" and r["condition"]=="INCONGRUENT_CONTEXT" and r["qTop"]!="" and abs(float(r["qTop"])-q)<TOL]
      s7=[r for r in rows if r["sessionKey"]=="S7" and r["condition"]=="INCONGRUENT_CONTEXT" and r["qTop"]!="" and abs(float(r["qTop"])-q)<TOL]
      wrong_b2.append(sum(int(r["contextInducedWrongEarlyStop"]) for r in b2)/max(1,len(b2)))
      wrong_s7.append(sum(int(r["contextInducedWrongEarlyStop"]) for r in s7)/max(1,len(s7)))
      le300.append(sum(float(r["assistedEvidenceSeconds"])<=.30+TOL for r in c)/len(c))
    chart(plots/"01_mean_evidence_by_condition.svg","Development A/B1: mean evidence by controlled Context",
      [{"name":"EEG-only","values":base},{"name":"Congruent","values":cmeans},{"name":"Incongruent pooled","values":imeans},{"name":"Neutral","values":neutral}],
      qs,.2,1.0,"Evidence seconds")
    lo=min(ig+cg)-.02;hi=max(ig+cg)+.02
    if hi<=lo:hi=lo+.04
    chart(plots/"02_strength_vs_latency_gain.svg","Context strength vs paired evidence gain",
      [{"name":"Congruent","values":cg},{"name":"Incongruent pooled","values":ig}],qs,lo,hi,"EEG-only minus Context (s)")
    chart(plots/"03_strength_vs_accuracy.svg","Context strength vs accuracy",
      [{"name":"Congruent","values":ca},{"name":"Incongruent pooled","values":ia}],qs,0,1,"Accuracy")
    chart(plots/"04_strength_vs_wrong_early_stops.svg","Wrong early-stop rate: development and post-hoc replay",
      [{"name":"A/B1 development","values":wrong},{"name":"B2 post-hoc","values":wrong_b2},{"name":"S7 post-hoc","values":wrong_s7}],
      qs,0,max(.01,max(wrong+wrong_b2+wrong_s7)*1.15),"Fraction")
    chart(plots/"05_strength_vs_fraction_le_300ms.svg","Fraction completing by 300 ms",
      [{"name":"Congruent","values":le300}],qs,0,1,"Fraction")
    o=[];m=[]
    for s in ("B2","S7"):
      r=next(x for x in oracle["summaries"] if x["sessionKey"]==s and x["condition"]=="oracle_any_agreement_1")
      o.append(float(r["meanOracleGainSeconds"]));m.append(float(m33[s]["meanPairedGainSeconds"]))
    chart(plots/"06_oracle_vs_real_m33_acceleration.svg","Post-hoc only: Oracle vs stored M33 replay",
      [{"name":"Oracle upper bound","values":o},{"name":"M34 precomputed M33 replay","values":m}],
      ["B2","S7"],min(0,min(o)-.02),max(o)*1.15+.01,"Mean paired gain (s)","Post-hoc session")
    representative_plot(plots/"07_representative_trajectories.svg",rows)
    return sorted(p.name for p in plots.glob("*.svg"))

def representative_plot(path,rows):
    dev=[r for r in rows if r["sessionKey"] in ("A","B1")]
    success=next((r for r in dev if r["condition"]=="CONGRUENT_CONTEXT" and int(r["contextApplied"]) and int(r["assistedCorrect"])),None)
    reject=next((r for r in dev if r["condition"]=="INCONGRUENT_CONTEXT" and int(r["exactEegOnlyFallback"])),None)
    # Find one failure under the most aggressive mechanism, only on A/B1, for a labeled stress illustration.
    base_rows={(r["sessionKey"],r["trialId"]):r for r in dev if r["condition"]=="EEG_ONLY"}
    traces={}
    for key,r in base_rows.items():
      points=json.loads(r["eegScoreTrajectory"])
      traces[key]={"sessionKey":key[0],"trialId":key[1],"trueSlotIndex":int(r["trueSlotIndex"]),
        "points":{float(x["t"]):point(float(x["t"]),x["scores"]) for x in points}}
    if success is None:
      for (s,tid),t in traces.items():
        b=dynamic_stop(t)
        for q in reversed(Q_GRID):
          trial_success=result_row(s,t,b,"CONGRUENT_CONTEXT",q,int(t["trueSlotIndex"]),max(ALPHAS),0.0,2)
          if int(trial_success["contextApplied"]) and int(trial_success["assistedCorrect"]):
            success=trial_success
            success["illustrativePolicy"]="most aggressive tested A/B1 candidate; not selected for deployment"
            break
        if success is not None:break
    danger=None
    for alpha in reversed(ALPHAS):
      for floor in FLOORS:
       for s in ("A","B1"):
        for (ss,tid),t in traces.items():
         if ss!=s:continue
         b=dynamic_stop(t)
         if b["topSlotIndex"]!=int(t["trueSlotIndex"]):continue
         for q in reversed(Q_GRID):
          for ctx in range(3):
           if ctx==int(t["trueSlotIndex"]):continue
           p,ap=simulate(t,b,ctx,q,alpha,floor)
           if ap and p["topSlotIndex"]!=int(t["trueSlotIndex"]):
            danger={"sessionKey":s,"trialId":tid,"trueSlotIndex":int(t["trueSlotIndex"]),"contextSlotIndex":ctx,
              "eegOnlyEvidenceSeconds":b["evidenceSeconds"],"assistedEvidenceSeconds":p["evidenceSeconds"],
              "eegScoreTrajectory":json.dumps([{"scores":t["points"][w]["scoresBySlot"]} for w in SCHEDULE])}
            break
          if danger:break
         if danger:break
        if danger:break
       if danger:break
      if danger:break
    if success is None or reject is None:raise ValueError("Missing representative successful or safe rejection trial")
    if danger is None:raise ValueError("No dangerous wrong-Context example found in development data")
    examples=[("Successful congruent acceleration",success),("Safe incongruent rejection",reject),("Dangerous wrong-Context early stop",danger)]
    W,H=1280,900;L,PW,PH=95,1110,185;parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
      '<rect width="100%" height="100%" fill="white"/>','<text x="640" y="32" text-anchor="middle" font-family="Arial" font-size="21">Representative A/B1 EEG score trajectories</text>']
    colors=["#2463a6","#d05a37","#278a62"]
    for panel,(title,r) in enumerate(examples):
      y0=100+panel*260;x0=95
      tr=json.loads(r["eegScoreTrajectory"]);scores=[x["scores"] for x in tr]
      truth=int(r["trueSlotIndex"]);ctx=r["contextSlotIndex"]
      lo=min(v for p in scores for v in p);hi=max(v for p in scores for v in p)
      if hi-lo<1e-12:hi=lo+1
      def X(i):return x0+i*PW/(len(SCHEDULE)-1)
      def Y(v):return y0+PH-(float(v)-lo)/(hi-lo)*PH
      parts += [f'<text x="{x0}" y="{y0-14}" font-family="Arial" font-size="15" font-weight="bold">{esc(title)} — trial {esc(r["trialId"])}</text>',
        f'<rect x="{x0}" y="{y0}" width="{PW}" height="{PH}" fill="none" stroke="#555"/>']
      for slot in range(3):
        d=" ".join(("M" if i==0 else "L")+f" {X(i):.1f} {Y(scores[i][slot]):.1f}" for i in range(len(SCHEDULE)))
        parts.append(f'<path d="{d}" fill="none" stroke="{colors[slot]}" stroke-width="3"/>')
      for stop,label,col in ((float(r["eegOnlyEvidenceSeconds"]),"EEG-only","#333"),(float(r["assistedEvidenceSeconds"]),"Context","#b30000")):
        x=x0+(stop-SCHEDULE[0])/(SCHEDULE[-1]-SCHEDULE[0])*PW
        parts.append(f'<line x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y0+PH}" stroke="{col}" stroke-dasharray="6 4"/>')
        parts.append(f'<text x="{x+3:.1f}" y="{y0+13}" font-family="Arial" font-size="10" fill="{col}">{label} {stop:.2f}s</text>')
      parts.append(f'<text x="{x0}" y="{y0+PH+28}" font-family="Arial" font-size="12">true slot={truth}; context slot={ctx}; green=slot 2 / 12 Hz, red=slot 1 / 9 Hz, blue=slot 0 / 7.2 Hz</text>')
    parts.append("</svg>");Path(path).write_text("\n".join(parts)+"\n",encoding="utf-8")

def session_baseline_summary(rows):
    out={}
    for s in ("A","B1","B2","S7"):
      rs=[r for r in rows if r["sessionKey"]==s and r["condition"]=="EEG_ONLY"]
      out[s]={"n":len(rs),"correct":sum(int(r["assistedCorrect"]) for r in rs),
        "accuracy":statistics.fmean(int(r["assistedCorrect"]) for r in rs),
        "meanEvidenceSeconds":statistics.fmean(float(r["assistedEvidenceSeconds"]) for r in rs),
        "medianEvidenceSeconds":statistics.median(float(r["assistedEvidenceSeconds"]) for r in rs),
        "p90EvidenceSeconds":float(np.quantile([float(r["assistedEvidenceSeconds"]) for r in rs],.9,method="linear"))}
    return out

def main():
    manifest=json.loads((M34/"data_manifest.json").read_text(encoding="utf-8"))
    raw_paths={};before={}
    for s in manifest["sessions"]:
      key=s["sessionKey"];name="raw-eeg.jsonl" if key=="S7" else "raw-eeg-packets.jsonl"
      p=Path(s["sourcePath"])/name
      before[key]=sha256_file(p)
      if before[key]!=s["rawFileSha256"]:raise ValueError(f"source hash differs from M34 manifest: {key}")
      raw_paths[key]=p
    by,raw_by=load_traces();base=baselines(by)
    reproduction=check_m34_reproduction(by,base)
    policy,tuning,tuning_failures=search_policy(by,base);alpha=float(policy["alpha"]);floor=float(policy["marginFloor"]);stable=int(policy.get("contextStableUpdates",BASE_STABLE))
    per=expand(by,base,alpha,floor,stable);aggs=aggregate(per);stats=paired_statistics(per)
    oracle=oracle_analysis(by,base);m33=m33_summary();oracle["realM33ReplayComparison"]=m33
    guard_path=OUT/"guard_sensitivity.csv"
    if guard_path.exists():
      guards=read_csv(guard_path)
    else:
      guards=guard_analysis(raw_by)
      write_csv(guard_path,guards)
    figures=make_figures(per,oracle,m33)
    after={s:sha256_file(p) for s,p in raw_paths.items()}
    if before!=after:raise ValueError("Raw EEG inputs changed during analysis.")
    protocol={
      "milestone":"M35 Controlled Context Causality","softwareOnly":True,"physicalHardwareUsed":False,
      "environment":{"pythonVersion":sys.version,"numpyVersion":np.__version__},
      "protocolCopySha256":sha256_file(PROTOCOL_COPY),
      "analysisScriptSha256":sha256_file(Path(__file__)),
      "frozenM34":{"decoder":"FBCCA-003","channels":CHANNELS,"S7CommonStressChannels":[2,4,7],"frequencyHzBySlot":FREQUENCIES,"harmonics":3,
        "filterVariant":"three-band","guardSeconds":.5,"evidenceScheduleSeconds":SCHEDULE,
        "dynamicPolicy":{"id":"E200_M175_S2","minimumEvidenceSeconds":MIN_EVIDENCE,"minimumRelativeMargin":BASE_MARGIN,
          "requiredConsecutiveTopUpdates":BASE_STABLE,"maximumEvidenceSeconds":MAX_EVIDENCE},
        "classInvariant":"Every condition emits argmax raw EEG score; Context changes only earlier-stop authorization."},
      "contextConditions":["EEG_ONLY","CONGRUENT_CONTEXT","INCONGRUENT_CONTEXT (both wrong classes per trial)","NEUTRAL_CONTEXT","ORACLE"],
      "qTopGrid":Q_GRID,"prior":"q_top on Context class; remaining mass equally split between other two classes.",
      "developmentSessions":["A","B1"],"posthocOnlySessions":["B2","S7"],
      "selectedContextPolicy":{"selected":policy,
        "mechanism":f"When the synthetic Context top equals the raw EEG top, reduce the frozen 0.175 relative-margin stopping threshold by alpha*(q_top-1/3), bounded below by the selected floor. Require {stable} consecutive raw EEG top updates; emitted class remains raw EEG argmax. If no earlier authorization, return exact EEG-only decision.",
        "selectionBoundary":"A/B1 only; zero Context-induced wrong early stops across both wrong classes and all q strengths, no incongruent accuracy loss; among safe rules maximize congruent paired gain and application rate.",
        "candidateGrid":{"contextStableUpdates":[2,3,4],"alpha":ALPHAS,"marginFloor":FLOORS},"tuningRows":tuning},
      "m34Reproduction":reproduction,"rawInputSha256Before":before,"rawInputSha256After":after,"rawInputsUnchanged":before==after,
      "inputArtifactsSha256":{n:sha256_file(M34/n) for n in ("data_manifest.json","train_dev_search_per_trial.csv","heldout_fixed_per_trial.csv",
        "heldout_dynamic_per_trial.csv","dynamic_stopping_selected_per_trial.csv","context_heldout_per_trial_seed.csv","run_train_dev_search.py","m34_decoders.py")},
      "generatedFigures":figures}
    write_json(OUT/"m35_protocol.json",protocol)
    write_json(OUT/"eeg_only_reproduction.json",{"status":"PASS","stack":protocol["frozenM34"],
      "perSession":session_baseline_summary(per),"m34DynamicReproduction":reproduction,
      "limits":["B2/S7 are M34-consumed sets and are post-hoc only.","A/B1 alone select Context policy.","Timing remains nominal software-onset/sample-anchor timing; no physical optical timing verified."]})
    write_csv(OUT/"controlled_context_per_trial.csv",per)
    write_csv(OUT/"controlled_context_results.csv",aggs)
    write_json(OUT/"oracle_context_summary.json",oracle)
    posthoc_failures=[{**r,"candidateDisposition":"frozen A/B1-selected rule; post-hoc only"}
      for r in per if r["sessionKey"] in ("B2","S7") and r["condition"]=="INCONGRUENT_CONTEXT" and int(r["contextInducedWrongEarlyStop"])]
    write_csv(OUT/"incongruent_failure_cases.csv",tuning_failures+posthoc_failures)
    write_csv(OUT/"context_strength_tradeoff.csv",[r for r in aggs if r["condition"] in ("CONGRUENT_CONTEXT","INCONGRUENT_CONTEXT")])
    write_csv(OUT/"guard_sensitivity.csv",guards)
    write_csv(OUT/"context_policy_tuning.csv",tuning)
    write_json(OUT/"statistical_summary.json",stats)
    wrong_dev=sum(int(r["contextInducedWrongEarlyStop"]) for r in per if r["sessionKey"] in ("A","B1") and r["condition"]=="INCONGRUENT_CONTEXT")
    summary={"status":"PASS","nBySession":{s:len(ts) for s,ts in by.items()},"eegOnly":session_baseline_summary(per),
      "selectedPolicy":policy,"developmentContextInducedWrongEarlyStops":wrong_dev,
      "unsafeDevelopmentCandidateContextInducedWrongEarlyStops":len(tuning_failures),
      "posthocContextInducedWrongEarlyStops":sum(int(r["contextInducedWrongEarlyStop"]) for r in per if r["sessionKey"] in ("B2","S7") and r["condition"]=="INCONGRUENT_CONTEXT"),
      "m34Reproduction":reproduction,"rawInputHashesMatchManifest":before==after,"figures":figures,
      "interpretation":"Controlled mechanistic/oracle replay; not real-time or prospective Context validation."}
    write_json(OUT/"m35_run_summary.json",summary)
    print(json.dumps({"status":"PASS","selectedPolicy":policy,"eegOnly":summary["eegOnly"],
      "developmentContextInducedWrongEarlyStops":wrong_dev,"figures":figures,"output":str(OUT)},indent=2))

if __name__=="__main__":main()
