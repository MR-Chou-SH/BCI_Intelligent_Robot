"""Bounded high-speed offline trials with measured, local timing repairs."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import time
import gc
import multiprocessing
from .files import digest,read,replace_json
from .retiming_experiment import load_backup
from .speed_stress import trial,schedule
from .collect_experts import source_identity,identity_sha256,MAX_JOBS
from integration.kitchen.planning.local_timing import base_caps,repair_recipes,collision_pair

# A repair that moves the first failure later is refined with the next failure.
MAX_REPAIR_DEPTH=3
MAX_REPAIR_TRIALS=10


def compose(parent,child):
    """Keep every earlier repair; a later failure can only add slower local timing."""
    caps=dict(parent['caps'])
    for target,cap in child['caps'].items():caps[target]=min(cap,caps.get(target,cap))
    windows=list(parent['windows'])+[w for w in child['windows'] if w not in parent['windows']]
    recipe={'name':parent['name']+'+'+child['name'],'factor':min(parent['factor'],child['factor']),
            'caps':caps,'windows':windows}
    if parent.get('wash_gate') or child.get('wash_gate'):recipe['wash_gate']=True
    return recipe


def same_timing(a,b):
    return {k:v for k,v in a.items() if k!='name'}=={k:v for k,v in b.items() if k!='name'}


def optimize(backup,directory,*,identity=None,timeout=900):
    """Return a qualified component, or None so the caller retains its backup."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    data,context,base=load_backup(backup);identity=source_identity() if identity is None else identity
    expected={'schema':'kitchen_adaptive_timing_v1','backup_sha256':base['verification']['sha256'],
              'source_identity_sha256':identity_sha256(identity),'timeout':timeout}
    plan_path=directory/'plan.json'
    if plan_path.exists():
        if read(plan_path)!=expected:raise ValueError('Adaptive plan/source mismatch')
    else:replace_json(plan_path,expected)
    original={p.name:digest(p) for p in Path(backup).iterdir() if p.is_file()}
    records=[];successes=[];generation_errors=[]
    def attempt(recipe,*,selectable=True):
        path=directory/recipe['name'];report_path=path/'report.json'
        recipe_path=directory/(recipe['name']+'.json')
        if recipe_path.exists():
            if read(recipe_path)!=recipe:raise ValueError('Adaptive recipe mismatch')
        else:replace_json(recipe_path,recipe)
        if report_path.exists():r=read(report_path)
        elif path.exists():
            generation_errors.append({'recipe':recipe['name'],'reason':'Interrupted attempt preserved'});return None
        else:
            # Record while trying: a selected candidate is replayed, not executed twice.
            r=trial(backup,path,recipe['factor'],4,record=selectable,verify_recording=False,segment_factors=recipe['caps'],
                    windows=recipe['windows'],timeout=timeout,wash_gate=recipe.get('wash_gate',False))
            gc.collect() # Break private controller/observer cycles between trials.
        records.append({'recipe':recipe['name'],'factor':recipe['factor'],'report':str(report_path),
            'report_sha256':digest(report_path),'status':r['status'],'simulated_seconds':r.get('actual_seconds'),
            'error':r.get('error'),'local_factors':recipe['caps'],'local_windows':recipe['windows']})
        if r['task_success'] and selectable:successes.append((r['actual_seconds'],recipe))
        return r
    first={'name':'global4_contact_caps','factor':4.,'caps':base_caps(context),'windows':[]}
    failed=attempt(first)
    if failed is not None and collision_pair(failed):
        reference=attempt({'name':'source1_collision_audit','factor':1.,'caps':base_caps(context),'windows':[]},selectable=False)
        if reference is not None and not reference['task_success'] and collision_pair(reference)==collision_pair(failed):
            generation_errors.append({'reason':'SOURCE_PATH_COLLISION_AT_1X','report':records[-1]['report'],
                                      'pair':collision_pair(reference)})
            failed=None # Timing changes cannot certify an already colliding source path.
    if failed is not None and not failed['task_success']:
        def planned(recipe):
            commands,_,_=schedule(data,context,recipe['factor'],segment_factors=recipe['caps'],windows=recipe['windows'])
            return len(commands)/25
        # (planned seconds, depth, recipe, failure tick the recipe must pass)
        pending=[(planned(r),1,r,failed.get('failure_source_tick')) for r in repair_recipes(data,context,failed)]
        seen={r['name'] for _,_,r,_ in pending};tried=0
        while pending and tried<MAX_REPAIR_TRIALS:
            # Refine repairs that progressed first, otherwise shortest plan first.
            pending.sort(key=lambda x:(-x[1],x[0]))
            minimum,depth,recipe,parent_tick=pending.pop(0)
            if successes and min(x[0] for x in successes)<=minimum+1e-9:continue
            r=attempt(recipe);tried+=1
            if r is None or r['task_success'] or depth>=MAX_REPAIR_DEPTH:continue
            tick=r.get('failure_source_tick')
            if tick is None or parent_tick is None or tick<=parent_tick:continue
            for child in repair_recipes(data,context,r):
                composed=compose(recipe,child)
                if composed['name'] in seen or same_timing(composed,recipe):continue
                seen.add(composed['name']);pending.append((planned(composed),depth+1,composed,tick))
        if not successes:
            # Whole-trajectory fallback is last, after targeted repairs.
            for factor in (3.,2.):
                attempt({'name':f'global{factor:g}_fallback','factor':factor,'caps':base_caps(context),'windows':[]})
                if successes:break
    selected=None;selected_path=None
    for _,recipe in sorted(successes,key=lambda x:x[0]):
        recorded=directory/recipe['name']
        path=recorded if (recorded/'trajectory.h5').exists() else directory/('recorded_'+recipe['name'])
        qualified=path/'qualified.json'
        if qualified.exists():
            from .optimized_collection import check_report
            selected=check_report(qualified,directory,base['case'],base['seed'],identity);selected_path=qualified;break
        if path==recorded:
            r=read(recorded/'report.json');verification_path=recorded/'verification.json'
            if verification_path.exists():verification=read(verification_path)
            else:
                from .replay import verify
                verification=verify(r['trajectory']);replace_json(verification_path,verification)
            if verification['status']!='CONTROL_RESIMULATION_PASS' or not verification['task_success']:
                generation_errors.append({'recipe':recipe['name'],'reason':'INDEPENDENT_REPLAY_FAILED'});continue
            r={**r,'status':'QUALIFIED_EXPERIMENT','verification':verification}
        elif (path/'report.json').exists():r=read(path/'report.json')
        elif path.exists():continue
        else:r=trial(backup,path,recipe['factor'],4,record=True,segment_factors=recipe['caps'],windows=recipe['windows'],timeout=timeout,wash_gate=recipe.get('wash_gate',False))
        if not r['task_success']:continue
        from .annotations import build_annotations
        annotation=path/'annotations.json'
        replace_json(annotation,build_annotations(r['trajectory']))
        selected={**r,'status':'QUALIFIED_EXPERT','simulated_seconds':r['actual_seconds'],
            'annotations':str(annotation),'annotations_sha256':digest(annotation),
            'initialization_profile':base['initialization_profile'],
            'source_identity_sha256':identity_sha256(identity),'adaptive_recipe':recipe}
        replace_json(qualified,selected);selected_path=qualified;break
    if original!={p.name:digest(p) for p in Path(backup).iterdir() if p.is_file()}:raise ValueError('Adaptive backup changed')
    if source_identity()!=identity:raise ValueError('Adaptive sources changed during run')
    decision={'case_id':base['case']['case_id'],'seed':base['seed'],'trials':records,
        'selected_report':str(selected_path) if selected_path else None,'selected_seconds':selected.get('simulated_seconds') if selected else None,
        'source_files_unchanged':True,'generation_errors':generation_errors,
        'unresolved_reason':generation_errors[-1]['reason'] if selected is None and generation_errors else None}
    replace_json(directory/'decision.json',decision)
    return selected,selected_path,records,generation_errors


def review_job(job):
    backup,directory=job;started=time.perf_counter()
    try:selected,path,trials,errors=optimize(backup,directory)
    except Exception as error:
        row={'case_id':read(Path(backup)/'report.json')['case']['case_id'],'source':str(backup),
            'qualified':False,'error':repr(error),'wall_seconds':time.perf_counter()-started}
        replace_json(Path(directory)/'review_error.json',row)
        print(__import__('json').dumps(row),flush=True);return row
    row={'case_id':read(Path(backup)/'report.json')['case']['case_id'],'source':str(backup),
        'qualified':selected is not None,'seconds':selected.get('simulated_seconds') if selected else None,
        'recipe':selected.get('adaptive_recipe') if selected else None,'report':str(path) if path else None,
        'trials':len(trials),'wall_seconds':time.perf_counter()-started}
    print(__import__('json').dumps(row),flush=True);return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case-ids',nargs='+');parser.add_argument('--jobs',type=int,default=2)
    args=parser.parse_args()
    if not 1<=args.jobs<=MAX_JOBS:parser.error(f'jobs must be 1..{MAX_JOBS}')
    sources={}
    for path in sorted(args.backup_root.glob('attempts/**/backup/report.json')):
        r=read(path);key=r['case']['case_id']
        if r['status']=='QUALIFIED_EXPERT' and (not args.case_ids or key in args.case_ids):sources.setdefault(key,path.parent)
    if not sources or args.case_ids and set(args.case_ids)!=set(sources):parser.error('Missing qualified source')
    args.output.mkdir(parents=True,exist_ok=False);rows=[]
    with ProcessPoolExecutor(max_workers=args.jobs,mp_context=multiprocessing.get_context('spawn'),max_tasks_per_child=1) as pool:
        for row in pool.map(review_job,[(p,args.output/k.replace(':','_')) for k,p in sorted(sources.items())]):
            rows.append(row);replace_json(args.output/'summary.json',rows)


if __name__=='__main__':main()
