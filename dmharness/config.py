from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """Жёсткие лимиты harness: агент не может «зациклиться» или сжечь бюджет."""

    max_steps_per_stage: int = 8      # ходов модели на один этап
    max_retries_per_stage: int = 2    # повторов после провала guardrails
    max_total_llm_calls: int = 60     # всего вызовов LLM за прогон


DEFAULT_MODEL = os.environ.get("DMH_MODEL", "claude-sonnet-5-5")
