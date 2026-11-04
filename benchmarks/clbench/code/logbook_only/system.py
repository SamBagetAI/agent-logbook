"""The Logbook as the only memory across instances.

Within an instance the system keeps its context as ICL does: its prompts, answers and feedback for that
instance. When an instance completes, the context is cleared; the Logbook keeps its record, and its text is
in front of the next prompt. Nothing of an earlier instance reaches the model except through the Logbook.
The harness's reset() (the stateless baseline) clears the Logbook too, as in logbook_icl.
"""

from __future__ import annotations

from typing import Any

from ...interface import Observation, Query
from ...registry import register_system
from ..logbook_icl.system import LogbookICLSystem

@register_system("logbook_only")
class LogbookOnlySystem(LogbookICLSystem):
    """logbook_icl with the context cleared at every instance end; the Logbook stays."""

    @property
    def name(self) -> str:  # the parent's default name, unless the run names the system itself
        return "logbook_only" if super().name == "logbook_icl" else super().name

    def observe(self, observation: Observation, next_query: Query | None = None) -> None:
        super().observe(observation, next_query)  # the Logbook will read this feedback at the next step
        if getattr(observation, "instance_complete", True):
            self.messages = []
            self._provider_client.reset()

    def get_run_artifacts(self) -> dict[str, Any]:
        artifacts = super().get_run_artifacts()
        artifacts["artifact_type"] = "logbook_only"
        return artifacts
