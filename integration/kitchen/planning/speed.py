"""Planner timing profiles; physics, controller limits and task gates are unchanged."""
PROFILES=('standard','faster')


def speed_factor(profile,contract=None):
    if profile not in PROFILES:raise ValueError('Unknown motion profile: '+str(profile))
    if profile=='standard':return 1.
    phase=(contract or {}).get('phase')
    if phase in ('acquire','release','device'):return 1.
    return 1.25 if phase=='carry' else 1.4
