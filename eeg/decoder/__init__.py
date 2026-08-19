"""Reusable offline SSVEP dataset and decoder components for M6."""

from .config import DecoderConfig
from .pipeline import run_pipeline

__all__ = ["DecoderConfig", "run_pipeline"]
