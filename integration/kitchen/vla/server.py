"""Inference-only service; contains no MuJoCo/hardware imports."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from .protocol import SCHEMA, MAX_BYTES, action_chunk


def serve(backend, host='127.0.0.1', port=8001):
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if self.path != '/infer' or not 0 < length <= MAX_BYTES:
                    raise ValueError('Invalid request path/size')
                request = json.loads(self.rfile.read(length))
                if request['schema'] != SCHEMA or type(request['frame']) is not int or request['frame'] < 0:
                    raise ValueError('Invalid protocol/frame')
                started = time.perf_counter()
                with lock:
                    actions, original = backend.infer(request)
                actions = action_chunk(actions)
                reply = {'schema': SCHEMA, 'frame': request['frame'], 'actions': actions.tolist(),
                         'model_actions': original.tolist(), 'inference_seconds': time.perf_counter()-started,
                         'policy': getattr(backend, 'metadata', {})}
                status = 200
            except Exception as error:
                reply, status = {'schema': SCHEMA, 'error': str(error)}, 400
            body = json.dumps(reply, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(json.dumps({'schema': SCHEMA, 'host': host, 'port': server.server_port, 'ready': True}), flush=True)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--rtc', action='store_true')
    parser.add_argument('--execution-horizon', type=int, default=10)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.execution_horizon <= 50:
        parser.error('--execution-horizon must be1..50')
    from .smolvla import SmolBackend
    import torch
    torch.manual_seed(args.seed)
    backend = SmolBackend(args.checkpoint, args.device, args.rtc, args.execution_horizon)
    backend.metadata.update(inference_seed=args.seed, device=args.device)
    server = serve(backend, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
