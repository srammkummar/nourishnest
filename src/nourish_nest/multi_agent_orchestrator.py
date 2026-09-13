"""Bounded application-controlled supervisor; no framework, recursion or model calls."""

import asyncio
from time import perf_counter_ns

from sqlalchemy.orm import sessionmaker

from nourish_nest.assistant_provider import AssistantError
from nourish_nest.config import get_settings
from nourish_nest.models import utc_now
from nourish_nest.multi_agent_agents import SPECIALISTS, SupervisorAgent
from nourish_nest.multi_agent_contracts import (
    AGENT_ORDER,
    AgentInput,
    AgentResult,
    RunState,
    ScopeInput,
    SelectionInput,
    Status,
    Warning,
)
from nourish_nest.multi_agent_provider import RuleBasedMultiAgentProvider, guard
from nourish_nest.multi_agent_schemas import MultiAgentResponse, TraceSummary
from nourish_nest.multi_agent_tools import ToolRegistry, WorkflowError
from nourish_nest.multi_agent_trace import TraceRecorder, elapsed
from nourish_nest.services import NotFoundError


class MultiAgentOrchestrator:
    def __init__(self, engine, settings=None, provider=None):
        self.engine = engine
        self.settings = settings or get_settings()
        self.provider = provider or RuleBasedMultiAgentProvider()
        self.sessions = sessionmaker(bind=engine, expire_on_commit=False)

    async def preview(self, household_id, request, request_id):
        state = RunState(request_id=request_id, household_id=household_id,
                         member_ids=sorted(set(request.member_ids)), original_request=request.message,
                         tool_call_limit=self.settings.multi_agent_tool_limit)
        trace = TraceRecorder()
        state.execution_plan.selected_agents = ["supervisor"]
        if not request.include_knowledge:
            state.knowledge.answer = "Knowledge retrieval was not requested."
        tools = ToolRegistry(self.engine, state, trace, self.settings.multi_agent_step_timeout_seconds)
        supervisor_trace = trace.start("supervisor")
        started = perf_counter_ns()
        owned = False
        try:
            async with asyncio.timeout(self.settings.multi_agent_timeout_seconds):
                validated = await tools.call("supervisor", "validate_scope", ScopeInput(
                    household_id=household_id, member_ids=state.member_ids))
                owned = True
                state.transition(Status.INTERPRETING)
                refusal = guard(request.message + " " + (request.knowledge_question or ""))
                if refusal:
                    state.refusal_reason = refusal
                    state.failure_code = "assistant_refused"
                    state.transition(Status.REFUSED)
                else:
                    # Exactly one provider interpretation; never consult get_chat_provider/Ollama.
                    state.interpreted_intent = await self.provider.interpret(request.message)
                    from nourish_nest.multi_agent_contracts import Intent
                    state.interpreted_intent = Intent.model_validate(state.interpreted_intent.model_dump())
                    state.interpreted_intent.knowledge_question = request.knowledge_question
                    state.constraints = state.interpreted_intent.model_copy(deep=True)
                    if state.interpreted_intent.action == "refuse":
                        state.refusal_reason = "The request cannot be supported safely."
                        state.failure_code = "assistant_refused"
                        state.transition(Status.REFUSED)
                    elif state.interpreted_intent.action != "plan" or any(m.age < 18 for m in validated.members):
                        state.clarification_questions = [
                            "Specify 1–7 meals, a meal slot, servings, and supported constraints; select only saved adults for target comparisons."]
                        state.failure_code = "clarification_required"
                        state.transition(Status.CLARIFY)
                    else:
                        await self._execute(state, request, tools, trace)
        except TimeoutError:
            self._fail(state, "workflow_timeout")
        except NotFoundError:
            # No trace FK or provider invocation for inaccessible household/member scopes.
            raise
        except WorkflowError as exc:
            self._fail(state, exc.code)
        except asyncio.CancelledError:
            self._fail(state, "workflow_timeout")
            raise
        except Exception:  # noqa: BLE001 - isolate provider failures without private payloads
            self._fail(state, "orchestration_failed")
        finally:
            state.completed_at = utc_now()
            supervisor_trace.finish("failed" if state.status == Status.FAILED else "completed", state.failure_code)
            if owned:
                state.agent_results.append(AgentResult(
                    agent="supervisor", status=supervisor_trace.status,
                    allowed_tools=tools.allowed("supervisor"), started_at=state.started_at,
                    completed_at=state.completed_at, duration_ms=elapsed(started),
                    failure_code=state.failure_code))
                state.agent_results.sort(key=lambda r: AGENT_ORDER.index(r.agent))
                state.warnings = sorted({(w.code, w.message, w.agent): w for w in state.warnings}.values(),
                                        key=lambda w: (w.code, w.agent or "", w.message))
                try:
                    trace.persist(self.sessions, state, elapsed(started))
                except Exception:  # noqa: BLE001 - never return an unaudited success
                    raise AssistantError("orchestration_failed", "Execution trace could not be saved.", 500) from None
        return MultiAgentResponse(
            run_id=state.run_id, request_id=state.request_id, household_id=household_id,
            status=state.status, interpretation=state.interpreted_intent,
            execution_plan=state.execution_plan, meal_plan=state.meal_plan,
            nutrition_summary=state.nutrition_results, pantry_summary=state.pantry_evidence,
            grocery_shortages=state.grocery_shortages, knowledge=state.knowledge,
            agent_results=state.agent_results, warnings=state.warnings,
            clarifications=state.clarification_questions, refusal_reason=state.refusal_reason,
            failure_code=state.failure_code,
            decision_summary=self._decisions(state), calculation_versions=state.calculation_versions,
            trace_summary=TraceSummary(agents_executed=len(state.agent_results),
                tool_calls=state.tool_call_count, duration_ms=elapsed(started), partial=state.partial,
                steps=[r.public() for r in trace.ordered()], transitions=state.transitions))

    @staticmethod
    def _fail(state, code):
        if state.status not in {Status.FAILED, Status.COMPLETED, Status.REFUSED, Status.CLARIFY}:
            state.transition(Status.FAILED)
        state.failure_code = code
        state.meal_plan = []
        state.nutrition_results = None
        state.grocery_shortages = None
        state.warnings.append(Warning(code=code, message="The workflow stopped; no domain changes occurred."))

    async def _agent(self, name, data, state, tools, trace):
        record = trace.start(name)
        started = perf_counter_ns()
        result = None
        try:
            async with asyncio.timeout(self.settings.multi_agent_step_timeout_seconds):
                field, output = await SPECIALISTS[name].execute(data.model_copy(deep=True), tools)
            record.finish("completed")
            result = AgentResult(agent=name, status="completed", allowed_tools=tools.allowed(name),
                                 started_at=record.started_at, completed_at=record.completed_at,
                                 duration_ms=elapsed(started), **{field: output})
            if name == "knowledge":
                result.warnings = output.warnings
                record.output_summary["warning_codes"] = [w.code for w in output.warnings]
            return result
        except asyncio.CancelledError:
            record.finish("cancelled", "agent_timeout")
            raise
        except Exception as error:  # noqa: BLE001 - specialist failures become safe typed outcomes
            code = ("household_isolation_failed" if isinstance(error, NotFoundError) else
                    "agent_timeout" if isinstance(error, TimeoutError) else
                    getattr(error, "code", "orchestration_failed"))
            record.finish("failed", code)
            result = AgentResult(agent=name, status="failed", allowed_tools=tools.allowed(name),
                                 started_at=record.started_at, completed_at=record.completed_at,
                                 duration_ms=elapsed(started), failure_code=code)
            if name == "knowledge" and code in {"agent_timeout", "orchestration_failed"}:
                state.partial = True
                state.warnings.extend([
                    Warning(code="partial_agent_failure", message="Optional knowledge retrieval failed.", agent=name),
                    Warning(code="knowledge_unavailable", message="No approved knowledge evidence is available.", agent=name)])
                return result
            raise WorkflowError(code) from None
        finally:
            if result is None:
                result = AgentResult(agent=name, status=record.status, allowed_tools=tools.allowed(name),
                    started_at=record.started_at, completed_at=record.completed_at or utc_now(),
                    duration_ms=elapsed(started), failure_code=record.failure_code)
            state.agent_results.append(result)

    async def _stage(self, names, data, state, tools, trace):
        jobs = {}
        try:
            async with asyncio.TaskGroup() as group:
                for name in names:
                    jobs[name] = group.create_task(self._agent(name, data, state, tools, trace))
        except ExceptionGroup as errors:
            error = errors.exceptions[0]
            while isinstance(error, ExceptionGroup):
                error = error.exceptions[0]
            raise WorkflowError(getattr(error, "code", "orchestration_failed")) from None
        return [jobs[name].result() for name in names]

    async def _execute(self, state, request, tools, trace):
        supervisor = SupervisorAgent()
        data = AgentInput(household_id=state.household_id, member_ids=state.member_ids,
                          intent=state.interpreted_intent, include_knowledge=request.include_knowledge)
        state.transition(Status.PLANNING)
        state.execution_plan = supervisor.plan(data, self.settings.multi_agent_agent_limit)
        state.transition(Status.RUNNING)
        first = await self._stage(state.execution_plan.parallel, data, state, tools, trace)
        for result in first:
            state.warnings.extend(result.warnings)
            if result.pantry:
                state.pantry_evidence = result.pantry
            if result.recipes:
                state.candidate_recipes = result.recipes.candidates
            if result.knowledge:
                state.knowledge = result.knowledge
        state.meal_plan = supervisor.select(data.intent, state.candidate_recipes)
        if not state.meal_plan:
            state.clarification_questions = ["Not enough distinct eligible recipes; request fewer meals or add matching recipes."]
            state.failure_code = "clarification_required"
            state.transition(Status.CLARIFY)
            return
        selected_ids = {meal.recipe_id for day in state.meal_plan for meal in day.meals}
        selected = SelectionInput(**data.model_dump(), meals=state.meal_plan,
                                  candidates=[c for c in state.candidate_recipes if c.recipe_id in selected_ids])
        second = await self._stage(state.execution_plan.dependent, selected, state, tools, trace)
        for result in second:
            if result.nutrition:
                state.nutrition_results = result.nutrition
                if result.nutrition.summary.weekly.warnings:
                    state.warnings.append(Warning(code="nutrition_value_unavailable",
                        message="Some selected-meal nutrition values are unavailable.", agent="nutrition"))
            if result.grocery:
                state.grocery_shortages = result.grocery
        state.transition(Status.VALIDATING)
        await tools.call("supervisor", "validate_selection", selected)
        state.warnings.extend([
            Warning(code="stale_read_warning", message="Independent read snapshots may differ; pantry quantities are not reserved."),
            Warning(code="informational_nutrition", message="Selected-meal totals are informational, not medical advice or a complete diet."),
            Warning(code="stored_allergen_data", message="Hard exclusions use stored allergen records; verify ingredient labels and missing data.")])
        state.transition(Status.COMPLETED)

    @staticmethod
    def _decisions(state):
        if state.status != Status.COMPLETED:
            return []
        selected = {m.recipe_id for d in state.meal_plan for m in d.meals}
        return [f"{r.recipe_name}: pantry coverage {r.coverage_percentage:.1f}%; "
                f"{len(r.expiring_ingredients)} expiring ingredients; meets requested structured filters."
                + (" Nutrition value unavailable." if r.nutrition_per_serving.calories is None else "")
                for r in state.candidate_recipes if r.recipe_id in selected]
