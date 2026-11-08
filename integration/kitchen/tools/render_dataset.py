"""Optional MP4 review export, separate from image-free collection."""
from pathlib import Path
import json
from integration.kitchen.launch import run_path
from integration.kitchen.data.collect_experts import save
from integration.kitchen.data.files import read,digest

def render(output, plan, qualified):
    from integration.kitchen.data.replay import video
    rows = []
    for row in qualified:
        row = dict(row)
        report = read(row['report'])
        trajectory = Path(report['trajectory'])
        parent = output / 'videos' / row['case_id'].replace(':', '_') / f"sample_{row['sample']:02d}"
        parent.mkdir(parents=True, exist_ok=True)
        receipt = parent / 'qualified_video.json'
        if receipt.exists():
            record = read(receipt)
            if record.get('settings')!={'video':plan['video'],'lightweight':plan.get('lightweight',False)}:
                raise ValueError('Video settings changed; use a fresh render directory')
            if record['trajectory_sha256'] != report['verification']['sha256']:
                raise ValueError('Video source hash mismatch')
            for name, expected in record['files'].items():
                path = run_path(name)
                if not path.is_relative_to(parent) or digest(path) != expected:
                    raise ValueError('Video artifact hash/path mismatch')
            row['video'] = record['video']
        else:
            number = 1
            while (parent / f'render_{number:03d}').exists():
                number += 1
            destination = parent / f'render_{number:03d}' / 'trajectory.mp4'
            destination.parent.mkdir()
            try:
                result = video(trajectory, destination, **plan['video'], max_seconds=3600.)
                if result['source_duration_s'] >= 3600:
                    raise RuntimeError('Video duration cap reached; refusing truncated review')
                record = {'video': str(destination), 'trajectory_sha256': result['trajectory_sha256'],
                          'settings':{'video':plan['video'],'lightweight':plan.get('lightweight',False)},
                          'files': {str(p): digest(p) for p in (destination, destination.with_suffix('.video.json'))}}
                save(receipt, record)
                row['video'] = str(destination)
            except Exception as error:
                row['video_error'] = repr(error)
        row['trajectory'] = str(trajectory)
        rows.append(row)
        print(json.dumps({'case_id': row['case_id'], 'sample': row['sample'],
                          'video': row.get('video'), 'error': row.get('video_error')}, ensure_ascii=False), flush=True)
    return rows


def main(argv=None):
    import argparse,os,fcntl
    parser=argparse.ArgumentParser(description='Render only final qualified manifest samples, never backups or trial files.')
    parser.add_argument('--manifest',type=run_path,required=True)
    parser.add_argument('--output',type=run_path,required=True)
    parser.add_argument('--speed',type=float,default=1.)
    parser.add_argument('--fps',type=int,default=25)
    parser.add_argument('--width',type=int,default=960)
    parser.add_argument('--height',type=int,default=540)
    parser.add_argument('--camera',default='v3_front')
    parser.add_argument('--zoom',type=float,default=.9)
    parser.add_argument('--lookat',type=float,nargs=3,default=[.7,.15,.95])
    parser.add_argument('--lightweight',action='store_true',help='Review graphics without MSAA, shadows or reflections')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv)
    if not (.25<=args.speed<=8 and 1<=args.fps<=60 and 0<args.zoom<=1
            and args.width>0 and args.height>0 and not args.width%2 and not args.height%2):parser.error('Invalid video parameters')
    source=read(args.manifest)
    if not isinstance(source,list) or not source:parser.error('Manifest must contain qualified samples')
    # Validate all sealed receipts before starting a long rendering job.
    from integration.kitchen.data.collect_experts import validate_previous
    from integration.kitchen.data.optimized_collection import validate_backup_receipt
    seen=set()
    for row in source:
        report_path=run_path(row['report'])
        if not report_path.is_relative_to(args.manifest.parent):raise ValueError('Report outside source batch')
        report=read(report_path)
        if row['status']!='QUALIFIED_EXPERT' or row['trajectory']!=report['trajectory']:
            raise ValueError('Manifest does not reference final qualified data')
        key=(row['case_id'],row['sample'])
        if key in seen:raise ValueError('Duplicate case/sample in manifest')
        seen.add(key)
        validate_previous([row],args.manifest.parent,[report['case']],[row['seed']])
        validate_backup_receipt(report,report_path.parent)
    settings={'manifest':str(args.manifest),'manifest_sha256':digest(args.manifest),
        'video':{'camera':args.camera,'speed':args.speed,'fps':args.fps,'width':args.width,
                 'height':args.height,'zoom':args.zoom,'lookat':args.lookat},'lightweight':args.lightweight}
    if args.output.exists() and not args.resume:parser.error('Existing output requires --resume')
    if args.resume and not (args.output/'render_plan.json').exists():parser.error('No render plan to resume')
    if args.output==args.manifest.parent or args.manifest.is_relative_to(args.output):
        parser.error('Use a separate render output directory')
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'.render.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan_path=args.output/'render_plan.json'
        if plan_path.exists():
            if read(plan_path)!=settings:parser.error('Render plan/source changed; use a new directory')
        else:save(plan_path,settings)
        os.environ.setdefault('MUJOCO_GL','egl')
        import mujoco
        original=mujoco.Renderer
        if args.lightweight:
            class ReviewRenderer(original):
                def __init__(self,model,*a,**kw):
                    model.vis.quality.offsamples=0
                    super().__init__(model,*a,**kw)
                def update_scene(self,*a,**kw):
                    super().update_scene(*a,**kw)
                    self.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW]=False
                    self.scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION]=False
            mujoco.Renderer=ReviewRenderer
        try:rows=render(args.output,settings,source)
        finally:mujoco.Renderer=original
        from integration.kitchen.data.files import replace_json
        replace_json(args.output/'index.json',{'schema':'kitchen_manifest_review_v1',**settings,'samples':rows,
            'rendered':sum(bool(r.get('video')) for r in rows),'target':len(rows),'images_included':False})
    return 0 if all(r.get('video') for r in rows) else 1


if __name__=='__main__':raise SystemExit(main())

