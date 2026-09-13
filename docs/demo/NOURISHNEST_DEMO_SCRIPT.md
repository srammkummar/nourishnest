# NourishNest narration

360 seconds; 818 words. Read in your own voice. The supplied slideshow is silent and fully captioned.

## 0:00–0:20 | NourishNest

Welcome to NourishNest, an agentic AI household nutrition platform. It connects everyday food decisions with explainable software: what is available, what could become dinner, and what needs buying. This demonstration follows a fictional family from pantry awareness to human-approved meal planning.

## 0:20–0:50 | One household, connected workflows

Nine capabilities work together. Household profiles capture preferences and allergies. Nutrition provides estimates, recipes organize ingredients, and the pantry tracks stock and freshness. Planning brings meals into a week, while grocery preparation identifies shortages. Local knowledge retrieval adds cited context. Specialist agents coordinate these services, and human approval controls the final action. The result is a connected workflow with clear explanations and visible boundaries.

## 0:50–1:20 | How we built it

We built NourishNest in layers. Streamlit presents the experience, FastAPI exposes typed contracts, and application services own business operations. Deterministic engines calculate nutrition, convert units, scale recipes, allocate pantry quantities, and generate grocery needs. SQLAlchemy and Alembic manage persistence and schema evolution. Above those services sit bounded orchestration and local retrieval. This recording uses a rule-based fake provider, not a generative language model. Optional Ollama and live USDA access are unused.

## 1:20–1:50 | Meet the Greenwood Family

Meet Maya and Daniel in the fictional Greenwood Family. Their saved profiles include vegetarian preferences and peanut allergies. The dashboard connects stock, upcoming meals, and shopping progress. Nutrition estimates come from saved measurements and the versioned calculator, rather than generated prose. Their interest in protein is a planning aspiration, not a promise of nutritional adequacy. All demonstration food values are illustrative, and the application keeps individual dietary suitability subject to human review.

## 1:50–2:25 | Start with what is already home

The recipe collection includes a curry, rice dishes, soup, salad, a tofu stir-fry, and an oat breakfast. Images provide varied serving inspiration, while saved ingredients drive calculations. The pantry has ten foods across refrigerator, freezer, and dry storage. Spinach has two separately dated lots, so quantity alone does not tell the whole story. Freshness signals highlight items worth checking soon, and a tofu threshold flags low stock. First-expiring-first-out logic uses dated inventory when calculating availability; browsing never silently consumes food.

## 2:25–2:55 | Turn inspiration into a plan

In Meal Planner, recommendations explain pantry coverage and missing ingredients. We place two portions of lentil and tomato soup into Monday dinner. Changing servings changes the required ingredient quantities and calculated nutrition. The weekly board is session-local, so it is distinct from durable grocery records. Preparing grocery needs combines planned portions and subtracts usable pantry availability. These are point-in-time estimates: a plan does not reserve stock, consume ingredients, or mark anything purchased.

## 2:55–3:30 | Grounded knowledge, visible evidence

For local knowledge, we ask how cooked rice should be stored safely. An original fictional household note is ingested deterministically, hashed, and split into chunks. BM25 retrieves lexical candidates, reranking orders the evidence, and citations preserve the exact excerpt and source heading. The note deliberately supplies no authoritative storage limits. A separate injection exercise produces an untrusted-instructions warning. Retrieval respects household scope and never executes document instructions. This foundation uses lexical retrieval, not embeddings, and neither RAG nor USDA structured food data replaces deterministic nutrition math.

## 3:30–4:25 | A bounded team of specialists

Now the central agentic workflow: create five vegetarian dinners for two adults, prioritize food expiring this week, stay under thirty-five minutes, avoid peanuts, and show grocery shortages. The supervisor translates supported wording into typed intent and an execution plan. Pantry, recipe, and knowledge specialists gather independent evidence with safe read concurrency. Nutrition calculates selected-meal totals, and the grocery specialist consolidates shortages. Six agents operate within explicit tool permissions and a twelve-call ceiling. Typed shared state carries evidence between stages, and the trace records which agent used which tool. Unsupported requests can require clarification or refusal. The output is a reviewable preview, with constraints and warnings visible. It has not created a shopping list, reserved pantry stock, or made a purchase.

## 4:25–5:10 | The human controls the write

The critic checks the preview against constraints and current deterministic calculations, surfacing what remains unverifiable. A proposal freezes the exact list and quantities under a payload hash. The person reviews that payload and explicitly approves it. Approval alone does not create the list: execution is a separate action. Optimistic concurrency rejects stale versions, and transaction boundaries protect against partial writes. Replaying the same execution key returns the same completed result instead of creating another list. The audit trail links the preview, decision, and controlled execution. This local foundation does not authenticate the approving person's identity, and pantry stock and purchase status remain unchanged.

## 5:10–5:40 | Quality is evidence, not a slogan

Quality evidence spans retrieval, orchestration, and controlled execution. Recorded evaluations cover thirty-six RAG cases, fifty-four multi-agent cases, and sixty-four approval cases. The last full suite recorded eight hundred sixty-four passes and one failure; that check was corrected, and twenty-four affected tests then passed. These are fixture results, not a blanket production guarantee. Request identifiers, typed contracts, rollback tests, migrations, and concurrency checks support the foundation. SQLite is demonstrated locally; PostgreSQL remains a deployment target.

## 5:40–6:00 | Grounded. Explainable. Human-approved.

NourishNest demonstrates how agentic AI can safely coordinate household nutrition workflows while deterministic services retain control of calculations and writes. It brings household context, grounded evidence, and accountable action into one experience. Grounded. Explainable. Human-approved.
