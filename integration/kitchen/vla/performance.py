"""Bounded in-memory timing/motion summaries, no images or dense state log."""
from collections import defaultdict
import numpy as np

JOINTS = [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14]


def statistics(values):
    x = np.asarray(values, dtype=float)
    if not x.size:
        return {'count': 0}
    return {'count': int(x.size), 'mean': float(x.mean()), 'p50': float(np.percentile(x, 50)),
            'p95': float(np.percentile(x, 95)), 'max': float(x.max())}


class PerformanceAudit:
    def __init__(self):
        self.series = defaultdict(list)
        self.last_apply = self.last_velocity = self.last_measured = None
        self.reversals = self.frames = self.boundaries = 0
        self.quiet_run = 0
        self.quiet_bouts = []

    def add(self, key, value):
        self.series[key].append(float(value))

    def replacement(self, old, new):
        if old is not None:
            difference = np.asarray(new)-np.asarray(old)
            self.add('replacement_disagreement_joint_rad', np.abs(difference[JOINTS]).max())
            self.add('replacement_disagreement_opening_ratio', np.abs(difference[[7, 15]]).max())

    def step(self, applied_at, elapsed, previous, accepted, measured_velocity, boundary):
        self.frames += 1
        self.boundaries += int(boundary)
        self.add('execution_ms', elapsed*1000)
        if self.last_apply is not None:
            self.add('apply_interval_ms', (applied_at-self.last_apply)*1000)
        self.last_apply = applied_at
        velocity = (np.asarray(accepted)[JOINTS]-np.asarray(previous)[JOINTS])/.04
        measured = np.asarray(measured_velocity)
        self.add('command_joint_speed_rad_s', np.abs(velocity).max())
        self.add('active_measured_joint_speed_rad_s', np.abs(measured).max())
        if self.last_velocity is not None:
            acceleration = np.abs(velocity-self.last_velocity).max()/.04
            self.add('command_joint_accel_rad_s2', acceleration)
            self.add('boundary_command_accel_rad_s2' if boundary else 'interior_command_accel_rad_s2', acceleration)
            self.reversals += int(np.sum((velocity*self.last_velocity < 0) &
                                        (np.abs(velocity) > .02) & (np.abs(self.last_velocity) > .02)))
        if self.last_measured is not None:
            self.add('active_measured_accel_rad_s2', np.abs(measured-self.last_measured).max()/.04)
        self.last_velocity, self.last_measured = velocity, measured.copy()
        if np.abs(measured).max() < .01:
            self.quiet_run += 1
        else:
            if self.quiet_run >= 5:
                self.quiet_bouts.append(self.quiet_run*.04)
            self.quiet_run = 0

    def report(self, client):
        metrics = {k: statistics(v) for k, v in self.series.items()}
        requests = list(client.timings)
        for name in ('client_ms', 'server_ms'):
            metrics['steady_'+name] = statistics([r[name] for r in requests if not r['warmup']])
        steady = [r for r in requests if not r['warmup']]
        wall_intervals = np.diff([r['started_at'] for r in steady if 'started_at' in r])
        sim_intervals = np.diff([r['frame']/25 for r in steady if 'frame' in r])
        metrics['request_interval_wall_ms'] = statistics(wall_intervals*1000)
        metrics['request_interval_sim_s'] = statistics(sim_intervals)
        bouts = self.quiet_bouts + ([self.quiet_run*.04] if self.quiet_run >= 5 else [])
        return {'schema': 'kitchen_vla_performance_v1', 'metrics': metrics,
                'warmup_requests': [r for r in requests if r['warmup']],
                'completed_requests': len(requests), 'frames': self.frames, 'chunk_boundaries': self.boundaries,
                'request_rate_wall_hz': float(1/wall_intervals.mean()) if len(wall_intervals) and wall_intervals.mean()>0 else None,
                'request_rate_sim_hz': float(1/sim_intervals.mean()) if len(sim_intervals) and sim_intervals.mean()>0 else None,
                'joint_direction_reversals_above_002_rad_s': self.reversals,
                'active_arm_near_static': {'speed_threshold_rad_s': .01, 'minimum_bout_s': .2,
                    'bouts': len(bouts), 'total_seconds': sum(bouts), 'longest_seconds': max(bouts, default=0.)},
                'scope': 'server time includes RGB decoding, preprocessing, model inference and CPU output synchronization; '
                         'client time also includes JSON, local HTTP and response validation; '
                         'apply interval is wall-clock, command/velocity derivatives use25Hz simulated time; '
                         'measured speed/accel concern the task active arm; completed requests can include one unused final request'}
