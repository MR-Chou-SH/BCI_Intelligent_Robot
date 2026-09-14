"""Load the explicit Unity virtual-block TargetId → M9 logical-ID table."""

import json
from pathlib import Path

from integration.m9_robot_adapter import (
    InvalidTargetMappingError,
    validate_logical_block_id,
)


DEFAULT_VIRTUAL_BLOCK_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "m7_unity6000"
    / "Assets"
    / "Resources"
    / "BCI"
    / "M9"
    / "virtual_blocks.json"
)


def load_virtual_block_target_mapping(catalog_path=None):
    """Return exact configured IDs; never infer mapping from labels or names."""
    path = Path(catalog_path) if catalog_path is not None else DEFAULT_VIRTUAL_BLOCK_CATALOG
    try:
        with path.open("r", encoding="utf-8") as source:
            catalog = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidTargetMappingError(
            "could not load explicit virtual-block mapping: {}".format(error)
        ) from error

    if not isinstance(catalog, dict) or catalog.get("schemaVersion") != 1:
        raise InvalidTargetMappingError("virtual-block mapping schemaVersion must be 1")
    blocks = catalog.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise InvalidTargetMappingError("virtual-block mapping must contain block records")

    mapping = {}
    logical_ids = set()
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("sourceKind") != "virtual_block":
            raise InvalidTargetMappingError(
                "block[{}] must explicitly declare sourceKind=virtual_block".format(index)
            )
        target_id = block.get("targetId")
        logical_block_id = block.get("logicalBlockId")
        if (
            not isinstance(target_id, str)
            or not target_id
            or target_id != target_id.strip()
            or any(ord(character) < 32 for character in target_id)
        ):
            raise InvalidTargetMappingError(
                "block[{}].targetId must be a non-empty exact identifier".format(index)
            )
        validate_logical_block_id(logical_block_id)
        if target_id in mapping:
            raise InvalidTargetMappingError("duplicate virtual TargetId: {!r}".format(target_id))
        if logical_block_id in logical_ids:
            raise InvalidTargetMappingError(
                "duplicate virtual logicalBlockId: {!r}".format(logical_block_id)
            )
        mapping[target_id] = logical_block_id
        logical_ids.add(logical_block_id)

    return mapping
