"""Описание этапов конвейера: цель, разрешённые инструменты, обязательные разделы, валидаторы."""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import guardrails as g


@dataclass(frozen=True)
class Stage:
    name: str
    title: str
    goal: str
    tools: tuple[str, ...]
    sections: tuple[str, ...]
    extra_validators: tuple = ()

    @property
    def validators(self):
        return (g.require_sections(*self.sections), g.no_pii, *self.extra_validators)


STAGES: tuple[Stage, ...] = (
    Stage("research", "Research потребности и источников",
          "Собрать паспорта источников и оценить нагрузку. Числа брать только из инструментов.",
          ("catalog_lookup", "estimate_load"),
          ("## Источники", "## Оценка нагрузки", "## Риски и допущения")),
    Stage("cjm", "CJM пользователей фреймворка",
          "Построить карту пути пользователей и метрики CSI для фичи.",
          ("list_personas",),
          ("## Персоны", "## Карта пути (CJM)", "## Метрики CSI")),
    Stage("architecture", "HLD: техническая архитектура",
          "Спроектировать архитектуру, выбрать технологии, описать trade-offs и отказы. Нужна Mermaid-диаграмма.",
          ("select_engine", "mermaid_flow"),
          ("## Контекст и цели", "## Компоненты", "## Диаграмма",
           "## Выбор технологий и trade-offs", "## Отказоустойчивость"),
          (g.require_mermaid,)),
    Stage("requirements", "ФТ/НФТ",
          "Сформулировать функциональные и измеримые нефункциональные требования с ID (ФТ-NN, НФТ-NN).",
          ("suggest_dq_rules",),
          ("## Функциональные требования (ФТ)", "## Нефункциональные требования (НФТ)"),
          (g.has_functional_requirements, g.measurable_nfr)),
    Stage("tz", "Техническое задание",
          "Собрать ТЗ: цель, трассировка ко ВСЕМ ФТ/НФТ, критерии приёмки, тесты, демо, документация.",
          (),
          ("## 1. Цель и границы", "## 2. Требования", "## 3. Критерии приёмки",
           "## 4. План тестирования и демо", "## 5. Пользовательская документация"),
          (g.traceability,)),
)

# какие артефакты предыдущих этапов видит каждый этап (контекст ограничен намеренно)
INPUTS = {
    "research": (), "cjm": ("research",), "architecture": ("research", "cjm"),
    "requirements": ("architecture", "cjm"), "tz": ("research", "architecture", "requirements"),
}

PROTOCOL = """Протокол ответа (строго):
- Вызов инструмента: <tool>{"name": "...", "args": {...}}</tool> — один вызов за ход, без другого текста.
- Результат придёт сообщением <observation name="...">json</observation>.
- Итоговый документ: <final>markdown</final>.
Правила: язык русский; числа — только из запроса и observation, ничего не выдумывать; все НФТ измеримы;
при GUARDRAIL_FEEDBACK исправь перечисленные нарушения и верни документ целиком."""


def system_prompt(stage: Stage, tool_specs: list[dict]) -> str:
    return (f"Ты — Senior System Analyst в команде фреймворка разработки витрин данных. [stage={stage.name}]\n"
            f"Этап: {stage.title}. Цель: {stage.goal}\n\n{PROTOCOL}\n\n"
            f"Доступные инструменты: {json.dumps(tool_specs, ensure_ascii=False)}\n"
            f"Обязательные разделы (точные заголовки): {', '.join(stage.sections)}")
