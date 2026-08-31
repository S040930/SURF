"""r22 overflow-recovery experiment primitives.

r22 is intentionally separate from the frozen r21 protocol.  The package
contains the transport/final validation split and the single-shot compression
recovery path used by future r22 executors.
"""

PROTOCOL_ID = "r22-codex-cli-overflow-recovery-2026-08-v1"
COMPRESSION_VERSION = "r22-compression-v1"

__all__ = ["COMPRESSION_VERSION", "PROTOCOL_ID"]
