# NourishNest demo narration

## NourishNest

Meet NourishNest, a household planning application that brings recipes, pantry inventory, groceries, and nutrition into one place. This walkthrough focuses on two things: how AI helped us build and verify the product, and how its meal-planning assistant uses carefully bounded tools.

## One household workspace

The dashboard connects the everyday workflow. A household can manage members, browse recipes, track pantry stock, plan meals, and prepare grocery needs. The screens in this recording use synthetic demonstration data. They show the working application, without exposing personal household information.

## How AI helped us build

For AI-assisted development, we used Codex as a coding collaborator. Our latest task was deliberately specific: improve image relevance and variety without redesigning the interface or changing backend behavior. Codex inspected the code, created a shared image resolver, added focused tests, and used browser automation to inspect the result. We kept the changes reviewable and did not automatically commit or publish them.

## From prompt to working code

Here is the concrete outcome. Images are selected by recipe identity and content, rather than card position. Dal maps to a legume dish, chicken to chicken, and khichdi to rice. Vegetarian and vegan labels exclude meat imagery. This demonstrates a useful AI workflow: give the coding agent a narrow requirement, then verify its implementation against explicit acceptance criteria.

## Verification is part of the build

We used conventional engineering tools to check the AI-assisted changes. The latest verification recorded 141 focused tests and 613 passing tests in the full suite. Ruff checked the code, and browser checks covered two laptop sizes. These are results from this repository's verification run, not a claim that the application is production certified.

## The in-app AI Assistant

Now to the in-app assistant. We ask: plan one vegan dinner for two people within thirty minutes, and show what I need to buy. For a reproducible walkthrough, this run uses the application's local deterministic demo provider. It is not a live language-model response. The same assistant interface also supports an optional local Ollama interpreter.

## Grounded planning results

The assistant makes its interpretation visible before presenting the meal preview. It selects from saved recipes and uses pantry information, rather than inventing meals or ingredients. Nutrition and grocery shortages come from the application's calculation services. The preview does not reserve stock, place orders, or change the household's data.

## The tools behind the assistant

The assistant's tool set is deliberately small. Recommendations ranks stored recipes. Nutrition summary scales and totals their nutrients. Member nutrition optionally compares an adult profile's target. Grocery shortage calculates what remains to buy. The server controls the arguments and permits at most four top-level tool calls. Model output cannot directly write to the database or decide the arithmetic.

## Where the language model fits

When configured with a local model, the implemented Ollama adapter interprets natural language into a structured intent. Pydantic validates that intent, while deterministic Python services remain responsible for calculations and saved-data access. The interface is Streamlit, the API is FastAPI, and storage uses SQLAlchemy. Full RAG and LangGraph multi-agent orchestration remain roadmap items; they are not part of this demonstration.

## The takeaway

NourishNest shows a practical approach to building with AI: use a coding assistant for scoped implementation, constrain the product assistant with validated tools, and verify the result. The goal is a useful household workflow, with clear evidence, visible limitations, and people staying in control.