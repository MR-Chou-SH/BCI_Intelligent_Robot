"""Append-only M5.3 EEG packet and stimulus-to-sample association tools."""

from .models import EegPacketMetadata, EpochAssociationRecord
from .timeline import EegPacketTimeline
from .association import SampleTimeMapper
from .gate import PostSyncAssociationGate
from .runtime import AssociationCoordinator

__all__ = ["EegPacketMetadata", "EpochAssociationRecord", "EegPacketTimeline", "SampleTimeMapper", "PostSyncAssociationGate", "AssociationCoordinator"]
