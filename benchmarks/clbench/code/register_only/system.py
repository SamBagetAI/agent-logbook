"""The Register as the only memory across instances.

Within an instance the system keeps its context as ICL does: its prompts, answers and feedback for that
instance. When an instance completes, the context is cleared; the Register keeps its record, and its text is
in front of the next prompt. Nothing of an earlier instance reaches the model except through the Register.
The harness's reset() (the stateless baseline) clears the Register too, as in register_icl.
"""

from __future__ import annotations

from typing import Any

from ...interface import Observation, Query
from ...registry import register_system
from ..register_icl.system import RegisterICLSystem

@register_system("register_only")
class RegisterOnlySystem(RegisterICLSystem):
    """register_icl with the context cleared at every instance end; the Register stays."""

    @property
    def name(self) -> str:  # the parent's default name, unless the run names the system itself
        return "register_only" if super().name == "register_icl" else super().name

    def observe(self, observation: Observation, next_query: Query | None = None) -> None:
        super().observe(observation, next_query)  # the Register will read this feedback at the next step
        if getattr(observation, "instance_complete", True):
            self.messages = []
            self._provider_client.reset()

    def get_run_artifacts(self) -> dict[str, Any]:
        artifacts = super().get_run_artifacts()
        artifacts["artifact_type"] = "register_only"
        return artifacts
