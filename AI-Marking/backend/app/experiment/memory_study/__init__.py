"""Independent SAF memory-framework study protocols."""

# Keep the original identifier stable so existing V3 projects remain
# interpretable. New projects use the explicitly versioned V3 revision.
PROTOCOL_ID = "saf-memory-framework-v3"
V3_R2_PROTOCOL_ID = "saf-memory-framework-v3-r2"
DEFAULT_PROTOCOL_ID = V3_R2_PROTOCOL_ID
SUPPORTED_PROTOCOL_IDS = (PROTOCOL_ID, V3_R2_PROTOCOL_ID)

__all__ = [
    "DEFAULT_PROTOCOL_ID",
    "PROTOCOL_ID",
    "SUPPORTED_PROTOCOL_IDS",
    "V3_R2_PROTOCOL_ID",
]
