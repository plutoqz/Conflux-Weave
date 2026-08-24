"""Compatibility shell for durable paper discovery."""

from conflux_weave.runtime.durable_paper_shared import (
    DURABLE_WORKFLOW_VERSION,
    DurableWorkResult,
)
from conflux_weave.runtime.durable_paper_runtime import DurablePaperDiscoveryRuntime

__all__ = [
    "DURABLE_WORKFLOW_VERSION",
    "DurablePaperDiscoveryRuntime",
    "DurableWorkResult",
]
