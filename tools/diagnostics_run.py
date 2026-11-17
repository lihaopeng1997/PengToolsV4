"""Run one read-only「内网排障」pass with the existing Agent runner.

No new orchestration: this composes ``AgentRunner`` + ``TextCalls`` (the same
protocol bridge the log analysis uses) with ``ReadOnlyDiagnostics`` so the tool
protocol and the visible-answer separation stay identical.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from tools.agent_core.budget import BudgetLimits
from tools.agent_core.contracts import CancellationToken, RunContext
from tools.agent_core.runner import AgentRunner
from tools.intranet_scenario import (
    IntranetScenario,
    apply_result_contract,
    resolve_model_boundary,
    system_prompt,
)
from tools.log_analysis.runtime import TextCalls
from tools.log_materials import MaterialReader
from tools.outbound_authorization import check as check_outbound
from tools.readonly_diagnostics import ReadOnlyDiagnostics


@dataclass(slots=True)
class DiagnosticsOutcome:
    result: object
    text: str
    missing_sections: list[str]
    boundary: object
    run_id: str = ''
    materials: tuple = ()
    tool_names: tuple[str, ...] = ()
    tool_results: tuple = ()

    @property
    def ok(self) -> bool:
        return getattr(self.result, 'status', None) is not None and str(
            getattr(getattr(self.result, 'status', None), 'value', '')).lower() == 'completed'

    def boundary_label(self, zh: bool = True) -> str:
        return self.boundary.label(zh)


def _tool_names(result) -> tuple[str, ...]:
    """Names of the tools the runner actually invoked, from its event stream."""
    names: list[str] = []
    for event in getattr(result, 'events', ()) or ():
        kind = getattr(event, 'event_type', None)
        kind = str(getattr(kind, 'value', kind) or '').lower()
        if kind.endswith('tool_started'):
            names.append(str((getattr(event, 'payload', None) or {}).get('name') or ''))
    return tuple(name for name in names if name)


def build_toolkit(scenario: IntranetScenario) -> ReadOnlyDiagnostics:
    return ReadOnlyDiagnostics(scenario.code_root_resolved, allowed_tools=scenario.allowed_tools)


def run_diagnostics(scenario: IntranetScenario, question: str, *, adapter,
                    run_id: str | None = None,
                    cancellation: CancellationToken | None = None) -> DiagnosticsOutcome:
    """Execute one bounded, read-only diagnostics turn.

    ``adapter`` is a real host adapter (or a test double). This function never
    constructs a model connection itself and never performs network I/O.

    Before the adapter is used, an endpoint that is not provably intranet must
    carry an outbound grant for *this* run id, model target and material set.
    The check happens here, not in the caller, so a UI that forgets to ask
    cannot send anything.
    """
    errors = scenario.validate()
    if errors:
        raise ValueError('；'.join(errors))

    run_id = str(run_id or uuid.uuid4().hex)
    boundary = resolve_model_boundary(scenario.model_config_id)
    root = scenario.code_root_resolved
    if not boundary.is_intranet:
        # The grant binds the model target, the material set, the normalized
        # code root and the tool allowlist: any of them changing needs a new
        # approval before a single byte leaves the machine.
        check_outbound(run_id, model_config_id=scenario.model_config_id,
                       host=boundary.host, materials=scenario.log_materials,
                       code_root=root, allowed_tools=scenario.allowed_tools)

    toolkit = build_toolkit(scenario)
    reader = MaterialReader(scenario.log_materials, root)
    contents = reader.read_all()
    token = cancellation or CancellationToken()

    context = RunContext(
        run_id,
        scenario.system_name,
        scenario.model_config_id,
        domain='intranet-diagnostics',
        scope={
            'run_id': run_id,
            'code_root': os.path.basename(root),
            'materials': [item.label for item in contents],
            'read_only': True,
        },
    )
    runner = AgentRunner(
        TextCalls(adapter),
        toolkit,
        system_prompt=system_prompt(scenario),
        cancellation=token,
        budget_limits=BudgetLimits(
            max_model_turns=scenario.max_rounds,
            max_tool_calls=scenario.max_tool_calls,
            max_duration_s=scenario.max_seconds,
            max_input_chars=scenario.max_input_chars,
            max_output_tokens=8192,
        ),
    )

    messages = [{
        'role': 'user',
        'content': f'{question}\n\n{reader.render_for_prompt(contents)}',
    }]
    result = runner.run(context, messages=messages)
    text, missing = apply_result_contract(getattr(result, 'final_text', '') or '')
    return DiagnosticsOutcome(
        result=result,
        text=text,
        missing_sections=missing,
        boundary=boundary,
        run_id=run_id,
        materials=tuple(item.headline() for item in contents),
        tool_names=_tool_names(result),
        tool_results=tuple(getattr(result, 'tool_results', ()) or ()),
    )
