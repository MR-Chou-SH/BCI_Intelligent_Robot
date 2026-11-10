"""SmolVLA model adapter. Import ONLY inside the isolated learning environment."""
import json
from pathlib import Path


def load(checkpoint, device, *, rtc=False, horizon=10):
    import torch
    from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.rtc.configuration_rtc import RTCConfig
    from lerobot.configs import PreTrainedConfig
    config = PreTrainedConfig.from_pretrained(checkpoint)
    if not isinstance(config, SmolVLAConfig):
        raise ValueError('Expected a SmolVLA checkpoint')
    config.device = device
    if config.adapt_to_pi_aloha or config.use_delta_joint_actions_aloha:
        raise ValueError('Kitchen expects absolute joint targets, not ALOHA transforms')
    if config.output_features['action'].shape != (16,):
        raise ValueError('Use a Kitchen-finetuned 16D checkpoint, not an unadapted base policy')
    from .images import camera_rig, active_views
    contract = json.loads((Path(checkpoint)/'kitchen_policy.json').read_text())
    rig = camera_rig(contract.get('camera_rig', contract['camera']))
    expected = {v['key']: (3, v['camera']['height'], v['camera']['width']) for v in active_views(rig)}
    if {k: tuple(v.shape) for k, v in config.image_features.items()} != expected:
        raise ValueError('Checkpoint visual features differ from its enabled camera rig')
    config.rtc_config = RTCConfig(enabled=True, execution_horizon=horizon) if rtc else None
    policy = SmolVLAPolicy.from_pretrained(checkpoint, config=config).to(device).eval()
    pre, post = make_pre_post_processors(config, pretrained_path=checkpoint,
        preprocessor_overrides={'device_processor': {'device': device}})
    return policy, pre, post


class SmolBackend:
    def __init__(self, checkpoint, device='cpu', rtc=False, horizon=10):
        self.policy, self.pre, self.post = load(checkpoint, device, rtc=rtc, horizon=horizon)
        self.device, self.rtc, self.horizon = device, rtc, horizon
        contract = Path(checkpoint) / 'kitchen_policy.json'
        if not contract.is_file():
            raise ValueError('Missing checkpoint kitchen_policy.json camera/action contract')
        self.contract = json.loads(contract.read_text())
        from integration.kitchen.data.files import digest
        self.metadata = {**self.contract, 'backend': 'smolvla', 'rtc_enabled': rtc,
                         'prediction_horizon': self.policy.config.chunk_size,
                         'rtc_execution_horizon': horizon, 'denoising_steps': self.policy.config.num_steps,
                         'checkpoint': str(Path(checkpoint).resolve()),
                         'model_sha256': digest(Path(checkpoint)/'model.safetensors')}

    def infer(self, request):
        import numpy as np
        import torch
        from .protocol import observation_images
        images = observation_images(request, self.contract.get('camera_rig', self.contract['camera']))
        state = np.asarray(request['state'], dtype=np.float32)
        if state.shape != (16,) or not np.isfinite(state).all():
            raise ValueError('Invalid proprioception')
        if request.get('reset'):
            self.policy.reset()
        frame = {'observation.state': torch.from_numpy(state), 'task': request['prompt']}
        for key, rgb in images.items():
            frame[key] = torch.from_numpy(rgb.transpose(2, 0, 1).copy()).float()/255.
        batch = self.pre(frame)
        prefix = request.get('prefix')
        kwargs = {}
        if self.rtc and prefix is not None:
            prefix = torch.as_tensor(prefix, dtype=torch.float32, device=self.device)
            if (prefix.ndim != 2 or prefix.shape[1] != 16 or not 1 <= len(prefix) <= 256
                    or not torch.isfinite(prefix).all()):
                raise ValueError('Invalid normalized model-space prefix')
            # SmolVLA pads actions to max_action_dim inside the denoiser.
            delay = max(0, int(request['inference_delay']))
            extent = min(len(prefix), self.policy.config.chunk_size)
            prefix = torch.nn.functional.pad(prefix[:extent], (0, self.policy.config.max_action_dim-16))
            kwargs = {'prev_chunk_left_over': prefix,
                      'inference_delay': min(delay, extent),
                      'execution_horizon': min(extent, max(self.horizon, delay+1))}
        # RTC uses autograd for guidance inside denoise_step; do not use inference_mode.
        with torch.no_grad():
            original = self.policy.predict_action_chunk(batch, **kwargs)
            processed = self.post(original.clone())
        return processed[0].detach().cpu().numpy(), original[0].detach().cpu().numpy()
