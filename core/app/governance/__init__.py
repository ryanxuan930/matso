"""參數治理（WP-B4）——簽證、凍結、篡改偵測。"""

from app.governance.seal import (
    active_seal,
    build_seal_payload,
    compute_seal_hash,
    seal_for,
)

__all__ = ["active_seal", "build_seal_payload", "compute_seal_hash", "seal_for"]
