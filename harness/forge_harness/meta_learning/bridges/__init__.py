"""
Bridge modules for connecting to external services.

Bridges provide graceful degradation: they return empty signals when
the target service is unavailable.
"""

from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge
from forge_harness.meta_learning.bridges.tech_diligence import TechDiligenceBridge

__all__ = [
    "CodeAtlasBridge",
    "TechDiligenceBridge",
]
