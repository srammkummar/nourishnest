"""Human decisions and deterministic execution; no agent may import a write tool."""

from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.approval_contracts import (
    ExecutionResponse,
    ProposalResponse,
    canonical,
    payload_hash,
)
from nourish_nest.approval_critic import CriticAgent, aware, load_source
from nourish_nest.assistant_provider import AssistantError
from nourish_nest.grocery_generation_services import GroceryGenerationService
from nourish_nest.grocery_schemas import GroceryListCreate
from nourish_nest.grocery_services import GroceryService
from nourish_nest.grocery_shortage_schemas import GroceryShortageResponse
from nourish_nest.models import (
    AgentActionExecution,
    AgentActionProposal,
    AgentApprovalEvent,
    Household,
    utc_now,
)
from nourish_nest.services import NotFoundError

TRANSITIONS = {"proposed": {"approved", "rejected", "cancelled", "expired"},
               "approved": {"executing", "cancelled", "expired"},
               "executing": {"completed", "failed"}}


def fail(code, message=None):
    raise AssistantError(code, message or code.replace("_", " ").capitalize(), 409)


@contextmanager
def transaction(session):
    """Entry requires a caller-owned idle session; no implicit commit of unrelated work."""
    if session.in_transaction():
        raise RuntimeError("Approval writes require an idle session")
    try:
        if session.get_bind().dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        else:
            session.begin()
        yield
        session.commit()
    except (StaleDataError, IntegrityError, OperationalError):
        session.rollback()
        fail("approval_concurrency_conflict", "Another request changed approval data; refresh and retry the same request.")
    except Exception:
        session.rollback()
        raise


def proposal_query(home, identifier):
    return select(AgentActionProposal).where(AgentActionProposal.household_id == home,
        AgentActionProposal.id == identifier).with_for_update().execution_options(populate_existing=True)


def get_proposal(session, home, identifier):
    proposal = session.scalar(proposal_query(home, identifier))
    if proposal is None:
        raise NotFoundError("Proposal not found")
    return proposal


def event(session, proposal, previous, request_id, expected, key=None):
    session.add(AgentApprovalEvent(proposal_id=proposal.id, household_id=proposal.household_id,
        event_type=proposal.status, previous_status=previous, new_status=proposal.status,
        expected_version=expected, resulting_version=proposal.version,
        request_id=payload_hash(request_id), idempotency_key=key,
        safe_metadata_json={"payload_hash": proposal.payload_hash, "warning_count": proposal.warning_count}))


def transition(session, proposal, target, request_id, key=None):
    previous, expected = proposal.status, proposal.version
    if target not in TRANSITIONS.get(previous, set()):
        fail("invalid_approval_transition")
    proposal.status, proposal.version = target, expected + 1
    proposal.updated_at = utc_now()
    field = {"approved": "approved_at", "rejected": "rejected_at", "cancelled": "cancelled_at",
             "executing": "execution_started_at", "completed": "execution_completed_at",
             "failed": "execution_completed_at"}.get(target)
    if field:
        setattr(proposal, field, utc_now())
    event(session, proposal, previous, request_id, expected, key)
    session.flush()


def verify(proposal, expected_hash):
    if proposal.action_type != "create_grocery_list":
        fail("prohibited_action")
    if payload_hash(proposal.canonical_payload_json) != proposal.payload_hash or expected_hash != proposal.payload_hash:
        fail("proposal_hash_mismatch")


def version(proposal, expected):
    if proposal.version != expected:
        fail("stale_proposal_version")


def executable_review(session, proposal):
    result = CriticAgent().review(session, proposal.household_id, proposal.agent_run_id)
    if result.decision == "block":
        fail("critic_blocked", "Critic blocked this action: " + ", ".join(result.blocking_issues))
    _, source = load_source(session, proposal.household_id, proposal.agent_run_id)
    if source.snapshot_hash != proposal.canonical_payload_json["source_snapshot_hash"]:
        fail("changed_source_run")
    # Rebuild from trusted evidence, so editing JSON plus its hash cannot change execution data.
    original = proposal.canonical_payload_json
    rebuilt = make_payload(source, original["list_name"], original["created_at"], original["expires_at"])
    if payload_hash(rebuilt) != proposal.payload_hash:
        fail("proposal_hash_mismatch")


def make_payload(source, name, created, expires):
    data = source.snapshot_json
    grocery = GroceryShortageResponse.model_validate(data["grocery"])
    positive = sorted((r for r in grocery.requirements if r.shortage_quantity > 0),
                      key=lambda r: (r.food_id, r.canonical_unit))
    rows = []
    for r in positive:
        row = canonical(r)
        row.pop("pantry_lots")
        row["sources"] = sorted(row["sources"], key=lambda s: (s["recipe_id"], s["ingredient_id"]))
        rows.append(row)
    return canonical({"action": "create_grocery_list", "household_id": source.household_id,
        "source_run_id": source.run_id, "source_snapshot_hash": source.snapshot_hash,
        "list_name": " ".join(name.split()), "recipes": sorted(data["meals"], key=lambda r: r["recipe_id"]),
        "shortages": rows, "calculation_versions": data["versions"], "warnings": data["warnings"],
        "calculation_as_of": grocery.calculation_as_of, "created_at": created, "expires_at": expires})


class ApprovalProposalService:
    def __init__(self, session):
        self.session = session

    def create(self, home, run_id, request, request_id):
        with transaction(self.session):
            critic = CriticAgent().review(self.session, home, run_id)
            if critic.decision == "block":
                fail("critic_blocked", "Critic blocked this action: " + ", ".join(critic.blocking_issues))
            _, source = load_source(self.session, home, run_id)
            now = utc_now()
            expires = min(now + timedelta(minutes=request.expires_in_minutes),
                          aware(source.created_at) + timedelta(minutes=60))
            payload = make_payload(source, request.list_name, now, expires)
            proposal = AgentActionProposal(household_id=home, agent_run_id=run_id, title=request.title,
                canonical_payload_json=payload, payload_hash=payload_hash(payload),
                critic_result_json=critic.model_dump(), warning_count=len(critic.warnings), expires_at=expires)
            self.session.add(proposal)
            self.session.flush()
            event(self.session, proposal, None, request_id, 0)
            response = ProposalResponse.model_validate(proposal)
        return response

    def get(self, home, identifier):
        proposal = self.session.scalar(select(AgentActionProposal).where(
            AgentActionProposal.id == identifier, AgentActionProposal.household_id == home))
        if proposal is None:
            raise NotFoundError("Proposal not found")
        return ProposalResponse.model_validate(proposal)

    def list(self, home):
        if self.session.get(Household, home) is None:
            raise NotFoundError("Household not found")
        return [ProposalResponse.model_validate(p) for p in self.session.scalars(
            select(AgentActionProposal).where(AgentActionProposal.household_id == home)
            .order_by(AgentActionProposal.created_at.desc(), AgentActionProposal.id))]


class HumanApprovalService:
    def __init__(self, session):
        self.session = session

    def decide(self, home, identifier, decision, request, request_id):
        expired = False
        with transaction(self.session):
            proposal = get_proposal(self.session, home, identifier)
            version(proposal, request.expected_version)
            if decision not in {"approved", "rejected", "cancelled"}:
                fail("invalid_approval_transition")
            if decision not in TRANSITIONS.get(proposal.status, set()):
                fail("invalid_approval_transition")
            if proposal.status in {"proposed", "approved"} and aware(proposal.expires_at) <= utc_now():
                transition(self.session, proposal, "expired", request_id)
                expired = True
            else:
                if decision == "approved":
                    if getattr(request, "confirmation", None) is not True:
                        fail("confirmation_required")
                    verify(proposal, request.payload_hash)
                    executable_review(self.session, proposal)
                transition(self.session, proposal, decision, request_id)
            response = ProposalResponse.model_validate(proposal)
        if expired:
            fail("proposal_expired")
        return response


class ControlledExecutionService:
    def __init__(self, session):
        self.session = session

    def execute(self, home, identifier, request, request_id):
        expired = False
        key = payload_hash(request.idempotency_key)
        fingerprint = payload_hash({"proposal_id": identifier, "request": request})
        with transaction(self.session):
            proposal = get_proposal(self.session, home, identifier)
            existing = self.session.scalar(select(AgentActionExecution).where(
                AgentActionExecution.household_id == home,
                or_(AgentActionExecution.idempotency_key == key, AgentActionExecution.proposal_id == identifier)))
            if existing:
                if existing.request_hash != fingerprint or existing.proposal_id != identifier:
                    fail("idempotency_conflict")
                verify(proposal, request.payload_hash)
                return ExecutionResponse.model_validate(existing.result_summary_json).model_copy(update={"replayed": True})
            version(proposal, request.expected_version)
            if proposal.status != "approved":
                fail("invalid_approval_transition")
            verify(proposal, request.payload_hash)
            if aware(proposal.expires_at) <= utc_now():
                transition(self.session, proposal, "expired", request_id)
                expired = True
            else:
                executable_review(self.session, proposal)
                transition(self.session, proposal, "executing", request_id, key)
                execution = AgentActionExecution(proposal_id=identifier, household_id=home,
                    payload_hash=proposal.payload_hash, request_hash=fingerprint, idempotency_key=key, status="executing")
                self.session.add(execution)
                self.session.flush()
                payload = proposal.canonical_payload_json
                try:
                    # Roll back all domain writes before recording a durable, non-retriable failure.
                    with self.session.begin_nested():
                        listing = GroceryService(self.session, commit=False).create_list(
                            home, GroceryListCreate(name=payload["list_name"]))
                        preview = GroceryShortageResponse(household_id=home,
                            recipes=[{"recipe_id": r["recipe_id"], "desired_servings": r["desired_servings"]}
                                     for r in payload["recipes"]],
                            requirements=[{**r, "pantry_lots": []} for r in payload["shortages"]],
                            warnings=[], calculation_as_of=payload["calculation_as_of"])
                        generation = GroceryGenerationService(self.session).persist_shortages(
                            home, listing.id, preview, key, proposal.payload_hash)
                        response = ExecutionResponse(proposal_id=identifier, status="completed",
                            grocery_list_id=listing.id, list_name=listing.name,
                            item_count=len(payload["shortages"]), generation_run_id=generation.id)
                except Exception:  # noqa: BLE001 - no private SQL/exception text enters audit or response
                    response = ExecutionResponse(proposal_id=identifier, status="failed",
                        list_name=payload["list_name"], failure_code="grocery_execution_failed")
                execution.status = response.status
                execution.grocery_list_id = response.grocery_list_id
                execution.failure_code = response.failure_code
                execution.completed_at = utc_now()
                execution.result_summary_json = response.model_dump(mode="json")
                proposal.result_reference_id = response.grocery_list_id
                proposal.failure_code = response.failure_code
                transition(self.session, proposal, response.status, request_id, key)
        if expired:
            fail("proposal_expired")
        return response
