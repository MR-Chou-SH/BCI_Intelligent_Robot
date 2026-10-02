"""Compatibility import for the reusable semantic intelligence core.

M27 benchmark artifacts remain frozen; implementation now lives in
integration.semantic_intelligence so M28 and M29 use the same scene/planner
contracts.
"""
from integration.semantic_intelligence import *  # noqa: F401,F403
