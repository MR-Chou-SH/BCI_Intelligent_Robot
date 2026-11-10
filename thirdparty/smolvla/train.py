"""Small-GPU fine-tuning of real SmolVLA; isolated from simulation dependencies."""
import argparse
import json
from pathlib import Path
import time
import sys

# Standalone entrypoint keeps training code under thirdparty, with project
# numeric contracts imported from the repository rather than vendored upstream.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo-id', default='local/kitchen_l1_apple')
    parser.add_argument('--base', default='lerobot/smolvla_base')
    parser.add_argument('--device', default='cuda', choices=['cuda', 'cpu'])
    parser.add_argument('--steps', type=int, default=5000)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--learning-rate', type=float, default=1e-4)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--save-every', type=int, default=1000)
    args = parser.parse_args()
    import torch
    from torch.utils.data import DataLoader
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
    from lerobot.policies.smolvla.processor_smolvla import make_smolvla_pre_post_processors
    from lerobot.configs import PolicyFeature, FeatureType, PreTrainedConfig
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA unavailable in this process. Check outside sandbox; CPU fallback is never automatic.')
    if args.steps < 1 or args.batch_size < 1 or args.save_every < 1:
        parser.error('Training budgets must be positive')
    if args.output.exists():
        raise FileExistsError('Use a fresh training output; no automatic overwrite')
    torch.manual_seed(args.seed)
    description = json.loads((args.dataset/'kitchen_export.json').read_text())
    if not description.get('vision_ready') or description['fps'] != 25:
        raise ValueError('Expected a genuine image-containing Kitchen25Hz dataset')
    config = PreTrainedConfig.from_pretrained(args.base)
    if not isinstance(config, SmolVLAConfig):
        raise ValueError('Expected a SmolVLA base checkpoint')
    from integration.kitchen.vla.images import camera_rig, active_views
    rig = camera_rig(description.get('camera_rig', description['image_contract']))
    views = active_views(rig)
    c = views[0]['camera']
    config.input_features = {'observation.state': PolicyFeature(FeatureType.STATE, (16,))}
    for view in views:
        image = view['camera']
        config.input_features[view['key']] = PolicyFeature(FeatureType.VISUAL, (3, image['height'], image['width']))
    config.empty_cameras = 0  # Disabled right wrist is absent, not a black placeholder.
    config.output_features = {'action': PolicyFeature(FeatureType.ACTION, (16,))}
    config.device, config.push_to_hub = args.device, False
    config.freeze_vision_encoder, config.train_expert_only, config.train_state_proj = True, True, True
    config.adapt_to_pi_aloha = config.use_delta_joint_actions_aloha = False
    config.load_vlm_weights = False  # The SmolVLA checkpoint already contains its backbone.
    config.resize_imgs_with_padding = (c['height'], c['width'])
    config.rtc_config = None  # RTC is inference-time guidance, not a different training objective.
    ds = LeRobotDataset(args.repo_id, root=args.dataset, video_backend='pyav',
                        delta_timestamps={'action': [i/25 for i in range(config.chunk_size)]})
    policy = SmolVLAPolicy.from_pretrained(args.base, config=config).to(args.device)
    import copy
    import numpy as np
    from integration.kitchen.vla.normalization import stable_stats
    stats = copy.deepcopy(ds.meta.stats)
    for key in ('action', 'observation.state'):
        values = np.asarray(ds.hf_dataset.with_format('numpy')[key], dtype=np.float64)
        stats[key] = stable_stats(values, width_state=key == 'observation.state')
    pre, post = make_smolvla_pre_post_processors(config, stats)
    params = [p for p in policy.parameters() if p.requires_grad]
    # Keep trainable/master weights fp32; autocast only affects compute activations.
    optimizer = torch.optim.AdamW(params, lr=args.learning_rate, weight_decay=1e-10)
    loader = DataLoader(ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    iterator = iter(loader)
    args.output.mkdir(parents=True)
    losses = []
    started = time.perf_counter()
    def save(step):
        directory = args.output / f'checkpoint_{step:06d}'
        policy.save_pretrained(directory)
        pre.save_pretrained(directory)
        post.save_pretrained(directory)
        (directory/'kitchen_policy.json').write_text(json.dumps({
            'schema': 'kitchen_smolvla_policy_v1', 'fps': 25, 'camera': c, 'camera_rig': rig,
            'action': 'absolute left7/opening_ratio/right7/opening_ratio',
            'state': 'measured left7/width_m/right7/width_m',
            'dataset': str(args.dataset.resolve()), 'step': step,
            'normalization': 'float64 population statistics; joint_std_floor=.001rad; opening_floor=.001ratio; width_floor=.0001m'}, indent=2)+'\n')
    policy.train()
    for step in range(1, args.steps+1):
        try:
            batch = next(iterator)
        except StopIteration:
            iterator = iter(loader)
            batch = next(iterator)
        batch = pre(batch)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type=args.device, dtype=torch.bfloat16, enabled=args.device == 'cuda'):
            loss, _ = policy(batch)
        if not torch.isfinite(loss):
            raise RuntimeError('Nonfinite training loss')
        loss.backward()
        grad = torch.nn.utils.clip_grad_norm_(params, 10., error_if_nonfinite=True)
        optimizer.step()
        losses.append(float(loss.detach()))
        if step == 1 or step % 50 == 0:
            print(json.dumps({'step': step, 'loss': losses[-1], 'grad_norm': float(grad),
                              'wall_seconds': time.perf_counter()-started}), flush=True)
        if step % args.save_every == 0 or step == args.steps:
            save(step)
    report = {'schema': 'kitchen_smolvla_training_v1', 'steps': args.steps, 'loss_first': losses[0],
              'loss_last': losses[-1], 'wall_seconds': time.perf_counter()-started,
              'device': args.device, 'trainable_parameters': sum(p.numel() for p in params),
              'cuda_peak_gib': torch.cuda.max_memory_allocated()/2**30 if args.device == 'cuda' else None,
              'generalization_evaluated': False, 'arguments': {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()}}
    (args.output/'training_report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
