"""64-case expert acquisition, per-case legal preparation and fail-closed QC.

plan writes only the case matrix. run executes real MuJoCo via the same public
request dispatcher, captures initial state/controls, verifies replay and keeps
failed attempts excluded from the qualified expert index. No images/hardware.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
import time

from integration.kitchen.tasks.catalog import catalogue,cases
from integration.kitchen.launch import ROOT,run_path


def save(path,value):
    with Path(path).open("x",encoding="utf-8") as stream:json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2)


def source_identity():
    names=sorted(str(p.relative_to(ROOT)) for package in ('planning','tasks','validation','data','runtime')
                 for p in (ROOT/'integration/kitchen'/package).glob('*.py'))
    from robot_arm.kitchen.registry import control_identity
    return {"control":control_identity(),"expert_sources":{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}}


def validate_previous(rows, output, selected, seeds):
    """Resume only sealed, indexed attempts belonging to this exact matrix."""
    expected={(case["case_id"],seed) for case in selected for seed in seeds}
    seen=set()
    for row in rows:
        key=(row["case_id"],row["seed"])
        if key not in expected or key in seen:
            raise ValueError("Duplicate or foreign resume index entry")
        seen.add(key)
        report_path=Path(row["report"]).resolve()
        if not report_path.is_relative_to(output.resolve()):
            raise ValueError("Resume report outside batch directory")
        content=report_path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=row.get("report_sha256"):
            raise ValueError("Resume report hash mismatch")
        report=json.loads(content)
        if (report["case"]["case_id"],report["seed"])!=key or report["status"]!=row["status"]:
            raise ValueError("Resume report/index identity mismatch")
        initial=report_path.parent/"initial.json"
        if report.get("initial_sha256") and hashlib.sha256(initial.read_bytes()).hexdigest()!=report["initial_sha256"]:
            raise ValueError("Resume initial state hash mismatch")
        if row["status"]=="QUALIFIED_EXPERT":
            from integration.kitchen.data.replay import inspect_trajectory
            audit=inspect_trajectory(report["trajectory"])
            if audit["sha256"]!=report["verification"]["sha256"] or not report["task_success"]:
                raise ValueError("Resume qualified trajectory mismatch")


def identity_sha256(identity):
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def one_case(job):
    """One episode: native execution -> seal -> independent replay/success."""
    from robot_arm.kitchen.platform import KitchenPlatform
    from integration.kitchen.planning.expert import KitchenExpert
    from integration.kitchen.runtime.client import LocalPlannerClient
    from integration.kitchen.validation.quality import ExpertQuality
    from .replay import verify
    case,seed,output,profile,wall_seconds,identity,diagnostics=job
    directory=Path(output);directory.mkdir(parents=True,exist_ok=False)
    report={'case':case,'seed':seed,'status':'FAILED_ATTEMPT','task_success':False,
            'images_included':False,'source_identity_sha256':identity_sha256(identity),
            'initialization_profile':profile}
    platform=client=expert=quality=None;recording=None;started=time.monotonic()
    try:
        if shutil.disk_usage(directory).free<10*1024**3:raise RuntimeError('DATA_DISK_RESERVE_10GIB')
        if source_identity()!=identity:raise RuntimeError('SOURCE_CHANGED_SINCE_BATCH_PLAN')
        platform=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=seed,
            initialization_profile=profile,data_root=directory/'data',evidence_compression='gzip',record_physics=diagnostics)
        client=LocalPlannerClient(platform,directory/'rpc.jsonl.gz' if diagnostics else None)
        expert=KitchenExpert(platform,client,wall_seconds=wall_seconds,write_checkpoints=False)
        quality=ExpertQuality(platform)
        expert.prepare(case)
        recording=expert.call('start_state_recording',mutating=True)
        expert.run_case(case,quality)
        if quality.failures:raise RuntimeError('QUALITY_CONTROLLER_STOP')
        platform.state_recorder.annotate(case,expert.events)
        expert.call('hold',mutating=True)
        expert.call('stop_state_recording',mutating=True)
        verification=verify(recording['path'])
        report['verification']={k:verification[k] for k in ('status','sha256','task_success','maximum_qpos_error','maximum_qvel_error')}
        if verification['status']!='CONTROL_RESIMULATION_PASS':raise RuntimeError('EXPERT_REPLAY_DIVERGED')
        if verification['task_success'] is not True:raise RuntimeError('REPLAY_TASK_FAILED: '+repr(verification['task_errors']))
        report.update(status='QUALIFIED_EXPERT',task_success=True)
    except Exception as error:report['error']=repr(error)
    finally:
        if platform:
            try:
                if recording and platform.state_recorder and not platform.state_recorder.closed:
                    platform.state_recorder.annotate(case,expert.events if expert else [])
                platform.close()
            except Exception as error:report.update(status='FAILED_ATTEMPT',task_success=False,error=repr(error))
        if diagnostics and quality:
            report['diagnostics']={'quality':quality.report(),'events':expert.events if expert else []}
        if report['status']=='QUALIFIED_EXPERT':
            destination=directory/'trajectory.h5'
            Path(recording['path']).replace(destination)
            report['trajectory']=str(destination)
            if not diagnostics:shutil.rmtree(directory/'data')
        elif not diagnostics:
            # Only this newly-created failed attempt; never historical/user data.
            shutil.rmtree(directory/'data',ignore_errors=True)
        report['data_retained']=bool(report.get('trajectory') or diagnostics and (directory/'data').exists())
        report['wall_seconds']=time.monotonic()-started
        save(directory/'report.json',report)
    return {'case_id':case['case_id'],'seed':seed,'status':report['status'],'task_success':report['task_success'],
            'error':report.get('error'),'report':str(directory/'report.json'),
            'report_sha256':hashlib.sha256((directory/'report.json').read_bytes()).hexdigest(),
            'wall_seconds':report['wall_seconds']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("plan","run"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--suite",choices=("atomic","composite","all"),default="all")
    parser.add_argument("--tasks",nargs="+")
    parser.add_argument("--case-ids",nargs="+")
    parser.add_argument("--seeds",nargs="+",type=int,default=[0])
    parser.add_argument("--profile",choices=("fixed","research"),default="research")
    parser.add_argument("--case-timeout",type=float,default=1200.)
    parser.add_argument("--jobs",type=int,default=1)
    parser.add_argument("--resume",action="store_true")
    parser.add_argument("--diagnostics",action="store_true",help="Opt in to large physics and RPC debug logs")
    args=parser.parse_args(argv)
    if not 1<=args.jobs<=4 or not 30<=args.case_timeout<=3600 or not 1<=len(args.seeds)<=20 or any(not 0<=s<2**32 for s in args.seeds):
        parser.error("jobs 1..4, timeout 30..3600, 1..20 uint32 seeds required")
    if len(args.seeds)!=len(set(args.seeds)):
        parser.error("Duplicate seeds would overwrite the same case; use unique seeds")
    selected=cases(args.suite)
    if args.tasks:
        known={row["task"] for row in selected}
        if set(args.tasks)-known:parser.error("Unknown task in selected suite")
        selected=[row for row in selected if row["task"] in args.tasks]
    if args.case_ids:
        known={row["case_id"] for row in selected}
        if set(args.case_ids)-known:parser.error("Unknown case ID in selected suite")
        selected=[row for row in selected if row["case_id"] in args.case_ids]
    if not selected:parser.error("Empty case matrix")
    output=run_path(args.output)
    identity=source_identity()
    plan={"schema":"kitchen_expert_batch_v2","catalogue":catalogue(),"selected_cases":selected,
          "seeds":args.seeds,"profile":args.profile,"source_identity":identity,
          "case_timeout":args.case_timeout,"jobs":args.jobs,"images_included":False,"diagnostics":args.diagnostics}
    if args.resume:
        old=json.loads((output/"plan.json").read_text())
        if old!=plan:parser.error("Resume requires identical plan/sources; choose a fresh output after code changes")
    else:
        output.mkdir(parents=True,exist_ok=False)
        save(output/"plan.json",plan)
    if args.action=="plan":
        print(json.dumps({"status":"PLAN_WRITTEN","atomic_instances":20,"composite_instances":44,"selected_cases":len(selected),"plan":str(output/"plan.json")}))
        return 0
    previous=[]
    journal=output/"index.jsonl"
    if journal.exists():previous=[json.loads(line) for line in journal.read_text().splitlines()]
    if previous:
        try:validate_previous(previous,output,selected,args.seeds)
        except (ValueError,KeyError,OSError) as error:parser.error(str(error))
    done={(r["case_id"],r["seed"]) for r in previous}
    jobs=[]
    for case in selected:
        for seed in args.seeds:
            if (case["case_id"],seed) in done:continue
            slug=case["case_id"].replace(":","_")
            directory=output/f"{slug}_seed{seed}"
            if directory.exists():parser.error("Unindexed partial case exists; preserve it and use a fresh run")
            jobs.append((case,seed,str(directory),args.profile,args.case_timeout,identity,args.diagnostics))
    results=list(previous)
    with ProcessPoolExecutor(max_workers=args.jobs,mp_context=multiprocessing.get_context("spawn")) as pool:
        pending={pool.submit(one_case,job):job for job in jobs}
        for future in as_completed(pending):
            result=future.result()
            with journal.open("a",encoding="utf-8") as stream:stream.write(json.dumps(result,ensure_ascii=False,allow_nan=False)+"\n")
            results.append(result)
            print(json.dumps(result,ensure_ascii=False),flush=True)
    qualified=[r for r in results if r["status"]=="QUALIFIED_EXPERT"]
    summary={"schema":"kitchen_expert_batch_summary_v2","expected_attempts":len(selected)*len(args.seeds),
             "attempted":len(results),"qualified":len(qualified),"failed":len(results)-len(qualified),
             "complete":len(qualified)==len(selected)*len(args.seeds),"qualified_index":qualified,
             "results":results,"images_included":False}
    summary_path=output/("summary_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".json")
    save(summary_path,summary)
    print(json.dumps({"complete":summary["complete"],"qualified":len(qualified),"summary":str(summary_path)}))
    return 0 if summary["complete"] else 1


if __name__=="__main__":raise SystemExit(main())
