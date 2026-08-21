"""Runtime-generation identities used to fence long-lived MCP sidecars.

The API process creates one opaque generation at import time.  Terminals copy
that value into the MCP sidecar environment at launch, allowing a surviving
sidecar to notice that the API/runtime which owns future handoffs was replaced.
"""

import uuid

RUNTIME_GENERATION_ENV = "CAO_RUNTIME_GENERATION"
RUNTIME_GENERATION_HEADER = "X-CAO-Runtime-Generation"

# Deliberately process-local.  A service restart creates a new generation even
# when the deployment did not change the package version.
ACTIVE_RUNTIME_GENERATION = uuid.uuid4().hex
