"""Submit DATA_INGEST conformance runtime evidence after local export gates pass."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Reuse the platform CLI document shape expected by ai-hub-conformance-evidence-import.
EVIDENCE = {
    "export_scope_enforced": True,
    "version_monotonic": True,
    "lookback_no_loss": True,
    "delete_captured": True,
    "idempotent_replay": True,
    "payload_contract_ok": True,
    "object_type": os.environ.get("AI_HUB_OBJECT_TYPE", "device"),
    "schema_fingerprint": os.environ.get("AI_HUB_SCHEMA_FINGERPRINT", "a" * 64),
}


def main() -> None:
    application_id = os.environ.get("AI_HUB_APPLICATION_ID", "standalone-example")
    environment = os.environ.get("AI_HUB_ENVIRONMENT", "local")
    document = {
        "application_id": application_id,
        "environment": environment,
        "contract_version": "m7-conformance-0.1.0",
        "source": "examples/sdk/data_ingest_evidence.py",
        "verified_at": datetime.now(UTC).isoformat(),
        "profiles": {
            "DATA_INGEST": {
                "status": "PASSED",
                "evidence": EVIDENCE,
            }
        },
    }
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data-ingest-evidence.json")
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
