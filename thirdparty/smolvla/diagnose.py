"""Measure teacher-forced action error on training frames, separately from success."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--repo-id', default='local/kitchen_l1_apple50')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--episodes', type=int, default=8)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a fresh diagnostic output')
    import numpy as np
    import torch
    from torch.utils.data import default_collate
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from integration.kitchen.vla.smolvla import load
    from integration.kitchen.vla.performance import statistics
    torch.manual_seed(0)
    policy, pre, post = load(args.checkpoint, 'cuda')
    ds = LeRobotDataset(args.repo_id, root=args.dataset, video_backend='pyav',
                        delta_timestamps={'action': [i/25 for i in range(policy.config.chunk_size)]})
    episode_ids = np.asarray(ds.hf_dataset.with_format('numpy')['episode_index'])
    available = np.unique(episode_ids)
    selected = available[np.linspace(0, len(available)-1, min(args.episodes, len(available)), dtype=int)]
    rows = []
    started = time.perf_counter()
    for episode in selected:
        indices = np.flatnonzero(episode_ids == episode)
        for second in (0, 2, 4, 8, 12):
            relative = second*25
            if relative >= len(indices):
                continue
            index = int(indices[relative])
            raw = ds[index]
            target = raw['action'].numpy()
            valid = ~raw['action_is_pad'].numpy()
            state = raw['observation.state'].numpy()
            batch = pre(default_collate([raw]))
            policy.reset()
            torch.manual_seed(index)
            with torch.no_grad():
                # This measures the same normalized flow objective as training,
                # but actual action predictions are evaluated in physical units.
                with torch.autocast('cuda', dtype=torch.bfloat16):
                    loss, _ = policy(batch)
                torch.manual_seed(index)
                prediction = post(policy.predict_action_chunk(batch).clone())[0].cpu().numpy()
            error = prediction-target
            row = {'episode': int(episode), 'dataset_index': index, 'relative_seconds': second,
                   'flow_loss': float(loss),
                   'first_left_joint_mae_rad': float(np.abs(error[0,:7]).mean()),
                   'first_left_joint_max_rad': float(np.abs(error[0,:7]).max()),
                   'first_left_opening_error': float(abs(error[0,7])),
                   'chunk_left_joint_mae_rad': float(np.abs(error[valid,:7]).mean()),
                   'chunk_left_opening_mae': float(np.abs(error[valid,7]).mean()),
                   'first_right_joint_max_rad': float(np.abs(error[0,8:15]).max()),
                   'normalized_state_abs_max': float(batch['observation.state'].abs().max()),
                   'first_action': prediction[0].tolist(), 'true_first_action': target[0].tolist(),
                   'state': state.tolist()}
            rows.append(row)
    keys = [k for k,v in rows[0].items() if isinstance(v, float)]
    report = {'checkpoint': str(args.checkpoint), 'dataset': str(args.dataset),
              'samples': len(rows), 'wall_seconds': time.perf_counter()-started,
              'metrics': {k: statistics([r[k] for r in rows]) for k in keys},
              'initial_frame_metrics': {k: statistics([r[k] for r in rows if r['relative_seconds']==0]) for k in keys},
              'rows': rows,
              'scope': 'Training frames only; teacher-forced fitting diagnostic, not held-out task success. '
                       'Predictions are independent chunks without RTC prefix; stochastic sampling fixed per dataset index.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','initial_frame_metrics')}))


if __name__ == '__main__':
    main()
