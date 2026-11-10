"""Versioned local HTTP protocol, using bounded JSON/uint8 RGB, never pickle."""
import base64
import http.client
import json
import time
from collections import deque
import numpy as np

SCHEMA = 'kitchen_vla_v1'
MAX_BYTES = 8 * 1024 * 1024


def rgb_encode(image):
    image = np.asarray(image)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError('Expected HWC uint8 RGB')
    return {'shape': list(image.shape), 'data': base64.b64encode(image.tobytes()).decode('ascii')}


def rgb_decode(value):
    shape = value['shape']
    if (len(shape) != 3 or shape[2] != 3 or
            any(type(n) is not int or not 1 <= n <= 2048 for n in shape)):
        raise ValueError('Invalid RGB dimensions')
    raw = base64.b64decode(value['data'], validate=True)
    if len(raw) != int(np.prod(shape)):
        raise ValueError('RGB byte count mismatch')
    return np.frombuffer(raw, dtype=np.uint8).reshape(shape).copy()


def observation_images(request, expected):
    from .images import camera_rig, active_views
    actual = camera_rig(request['cameras'] if 'cameras' in request else request['camera'])
    expected = camera_rig(expected)
    if actual != expected:
        raise ValueError('Online camera rig differs from training')
    encoded = request['images'] if 'images' in request else {'observation.images.camera1': request['image']}
    views = {v['key']: v['camera'] for v in active_views(expected)}
    if set(encoded) != set(views):
        raise ValueError('Enabled RGB views missing or disabled view supplied')
    result = {}
    for key, config in views.items():
        rgb = rgb_decode(encoded[key])
        if rgb.shape != (config['height'], config['width'], 3):
            raise ValueError('RGB dimensions differ from training camera')
        result[key] = rgb
    return result


def action_chunk(value, dim=16):
    result = np.asarray(value, dtype=np.float32)
    if result.ndim != 2 or result.shape[1] != dim or not 1 <= len(result) <= 256 or not np.isfinite(result).all():
        raise ValueError('Invalid finite action chunk')
    return result


class PolicyClient:
    def __init__(self, host='127.0.0.1', port=8001, timeout=120):
        self.host, self.port, self.timeout = host, port, timeout
        self.metadata = None
        self.timings = deque(maxlen=4096)

    def infer(self, observation, *, prefix=None, delay=0, reset=False):
        started = time.perf_counter()
        request = {**observation, 'schema': SCHEMA, 'inference_delay': int(delay),
                   'reset': bool(reset), 'prefix': None if prefix is None else prefix.tolist()}
        body = json.dumps(request, allow_nan=False).encode()
        if len(body) > MAX_BYTES:
            raise ValueError('Observation exceeds protocol limit')
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request('POST', '/infer', body, {'Content-Type': 'application/json'})
            response = conn.getresponse()
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Response exceeds protocol limit')
            reply = json.loads(raw)
            if response.status != 200 or reply.get('schema') != SCHEMA:
                raise RuntimeError('Policy server: ' + str(reply.get('error', response.status)))
            if reply['frame'] != observation['frame']:
                raise ValueError('Response observation frame mismatch')
            metadata = reply.get('policy', {})
            if metadata.get('fps', 25) != 25:
                raise ValueError('Policy frequency differs from25Hz simulation')
            if self.metadata is not None and metadata != self.metadata:
                raise ValueError('Policy identity changed during episode')
            self.metadata = metadata
            actions = action_chunk(reply['actions'])
            model = np.asarray(reply['model_actions'], dtype=np.float32)
            if (model.ndim != 2 or model.shape[0] != len(actions) or not 1 <= model.shape[1] <= 64
                    or not np.isfinite(model).all()):
                raise ValueError('Invalid model-space RTC prefix')
            self.timings.append({'warmup': bool(reset), 'frame': observation['frame'],
                'started_at': started,
                'client_ms': (time.perf_counter()-started)*1000,
                'server_ms': float(reply['inference_seconds'])*1000})
            return actions, model
        finally:
            conn.close()


class ChunkBuffer:
    """Absolute control-frame indices; discard inference actions already expired."""
    def __init__(self):
        self.start = 0
        self.actions = np.empty((0, 16), dtype=np.float32)
        self.model = None

    def remaining(self, now):
        return max(0, self.start + len(self.actions) - now)

    def prefix(self, now):
        if self.model is None or not self.remaining(now):
            return None
        return self.model[max(0, now-self.start):].copy()

    def merge(self, observed_frame, actions, model, now):
        if observed_frame > now:
            raise ValueError('Chunk starts in the future')
        skip = now - observed_frame
        if skip >= len(actions):
            return False
        self.start, self.actions, self.model = now, actions[skip:].copy(), model[skip:].copy()
        return True

    def get(self, now):
        index = now - self.start
        if not 0 <= index < len(self.actions):
            return None
        return self.actions[index].copy()
