"""Small kitchen adapter around a verified, locally copied DiscoDemo implementation."""
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import numpy as np


def verify_upstream():
    root=Path(__file__).resolve().parent
    receipt=json.loads((root/'source_receipt.json').read_text())
    for name,sha in receipt['files'].items():
        file=root/'upstream'/name
        if hashlib.sha256(file.read_bytes()).hexdigest()!=sha:
            raise ValueError(f'DiscoDemo upstream copy changed: {name}')
    return hashlib.sha256((root/'source_receipt.json').read_bytes()).hexdigest()


def make_config(*, seed=0, steps=100000, batch=128, capacity=50000, warmup=512, device='cuda:0'):
    from flash_rl.agents.flashSAC.agent import FlashSACConfig
    return FlashSACConfig(seed=seed,normalize_reward=True,normalized_G_max=5.,
        asymmetric_observation=False,device_type=device,buffer_max_length=capacity,
        buffer_min_length=warmup,buffer_device_type=device,sample_batch_size=batch,
        learning_rate_init=3e-4,learning_rate_peak=3e-4,learning_rate_end=1.5e-4,
        learning_rate_warmup_rate=1e-6,learning_rate_warmup_step=0,
        learning_rate_decay_rate=1.,learning_rate_decay_step=steps,
        actor_num_blocks=2,actor_hidden_dim=128,actor_bc_alpha=0.,actor_noise_zeta_mu=2.,
        actor_noise_zeta_max=16,actor_update_period=2,critic_num_blocks=2,critic_hidden_dim=256,
        critic_num_bins=101,critic_min_v=-5.,critic_max_v=5.,critic_target_update_tau=.01,
        temp_initial_value=.01,temp_target_sigma=.15,temp_target_entropy=0.,gamma=.99,n_step=8,
        use_compile=False,compile_mode='auto',use_amp=False,load_optimizer=True,load_reward_normalizer=True)


def create_agent(cfg, worlds, physical_dim, alpha, *, bank=None, inference=False):
    import torch
    import gymnasium as gym
    from flash_rl.agents.flashSAC.agent import FlashSACAgent
    z_dim=3 if alpha>0 else 0
    obs_space=gym.spaces.Box(-1.,1.,shape=(worlds,physical_dim+z_dim),dtype=np.float32)
    action_space=gym.spaces.Box(-1.,1.,shape=(worlds,8),dtype=np.float32)
    agent=FlashSACAgent(obs_space,action_space,{},cfg)
    agent._normalizer.set_obs_bounds(torch.full((physical_dim+z_dim,),-1.),torch.ones(physical_dim+z_dim))
    gate=None
    if alpha>0 and not inference:
        from flash_rl.agents.utils.metra import MetraRepresentation,SkillGateTable
        metra=MetraRepresentation(6,3,hidden=[256,256],lr=1e-4,device=cfg.device_type)
        gate=SkillGateTable(device=cfg.device_type)
        agent.set_skill_modules(metra,gate,alpha,3,torch.arange(physical_dim-6,physical_dim))
    if bank is not None:
        from flash_rl.rfcl.demo_buffer import DemoBuffer
        from flash_rl.rfcl.mixed_buffer import MixedReplayBuffer
        rows=[];rng=np.random.default_rng(cfg.seed)
        for demo in bank['demos']:
            row={k:v.copy() for k,v in demo['transitions'].items()}
            if z_dim:
                z=np.broadcast_to(rng.normal(size=z_dim).clip(-1,1),(len(row['reward']),z_dim))
                for key in ('observation','next_observation'):row[key]=np.c_[row[key],z].astype('f4')
            rows.append(row)
        demo=DemoBuffer(obs_space,action_space,cfg.sample_batch_size,cfg.device_type,n_step=cfg.n_step,gamma=cfg.gamma)
        transitions={key:torch.from_numpy(np.concatenate([r[key] for r in rows])) for key in rows[0]}
        for key in ('observation','next_observation'):
            transitions[key]=agent._normalizer.normalize_obs(transitions[key].to(cfg.device_type))
        demo.fill_from_transitions(transitions)
        agent._replay_buffer=MixedReplayBuffer(agent._replay_buffer,demo,.5)
    return agent,gate,z_dim


def configuration(cfg):return asdict(cfg)


def resume_decay_steps(current_config, previous, total_steps):
    """Extend the interaction budget without restarting the saved LR schedule."""
    if total_steps<=previous['step']:
        raise ValueError('--steps must exceed the checkpoint completed step count')
    saved=previous['agent_config']
    candidate={**current_config,'learning_rate_decay_step':saved['learning_rate_decay_step']}
    if candidate!=saved:raise ValueError('Resume mismatch: agent_config')
    return saved['learning_rate_decay_step']


def load_policy(checkpoint, *, device='cuda:0'):
    from flash_rl.agents.flashSAC.agent import FlashSACConfig
    receipt=verify_upstream()
    checkpoint=Path(checkpoint)
    metadata=json.loads((checkpoint/'kitchen.json').read_text())
    if metadata['schema']!='kitchen_discodemo_checkpoint_v1':raise ValueError('Unknown checkpoint')
    if metadata['upstream_receipt_sha256']!=receipt:raise ValueError('Checkpoint upstream receipt mismatch')
    cfg=FlashSACConfig(**{**metadata['agent_config'],'device_type':device,'buffer_device_type':device})
    agent,_,z_dim=create_agent(cfg,1,metadata['physical_dim'],metadata['alpha'],inference=True)
    agent.load_actor(str(checkpoint))
    return agent,metadata,z_dim
