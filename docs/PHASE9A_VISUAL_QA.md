# Phase 9A visual verification

Verified with synthetic Maple House / Willow House fixtures, FastAPI on 8099,
Streamlit on 8599, a disposable SQLite database, and `APP_AI_PROVIDER=fake`.
No production data or paid/external AI provider was used.

## Results

- Baseline: clean working tree, 537 tests passed, Ruff passed.
- Focused presentation regression run: 161 passed.
- Final full suite: 549 passed; one pantry AppTest exceeded its existing
  20-second deadline while browser testing was running. The unchanged failing
  case passed in isolation (2.88 seconds). The full suite was not repeated.
- Ruff import ordering was corrected; the final Ruff and `git diff --check`
  passed. Existing dependency deprecation warnings remain.
- All eight pages were visually inspected at 1366×768 and 1024×768.
  Dashboard was additionally inspected at 768×768: content width equaled
  viewport width, images loaded, and no visible UUIDs appeared.
- Exercised household switching, named member selection, adult nutrition
  calculation, household/system recipe browsing, pantry location cards,
  grocery progress, deterministic recommendations, and a fake assistant
  request: “Plan 1 vegan dinner for 2 people”. The assistant returned a meal
  preview with truthful local-mode disclosure and retained warnings.
- No observed overlapping controls or clipped metric values. Sidebar content
  scrolls vertically at laptop heights; collapsed navigation is used at 768px.
  Wide inventory tables retain Streamlit's horizontal scrolling.
- The original inline SVG wordmark did not render through Streamlit's HTML
  sanitization. Local SVG image sources corrected this; the wordmark and
  illustrations have explicit accessible image labels.

## Read-only check

After ordinary initial page visits, all ten seeded pantry lots were still
active, including an overdue lot. Explicitly clicking Refresh pantry marked
that lot expired using the existing API behavior. Thereafter the whole-database
SQL-dump SHA-256 stayed identical through final browsing and previews:
`a2bd60a22c5ebd1f3794ae7d1fdf41fc24360171bd6c021954ac3730586ac9e4`.
No purchase, grocery generation, or persistent meal-plan write was submitted.

## Limits

- Photos are shared serving inspiration, not recipe-specific images. Five
  freely downloadable Pexels photos were downloaded, cropped and bundled as
  WebP; source/license links are in `static/assets/ATTRIBUTION.md`. Four SVGs
  are original project artwork. No paid image calls occurred.
- Pantry/dashboard stock totals require an explicit freshness refresh because
  existing pantry read endpoints can persist expiry status. No backend rule
  was changed. Cached snapshots can become stale when another session writes.
- Recipe cards show at most twelve matches, with all recipes available through
  the existing selector. Per-recipe nutrition reads are cached by version.
- Weekly plans remain session-local. The visual weekly shortage count reflects
  the recommendation snapshot; use the existing grocery shortage preview for
  the final combined planned quantities.
- Native Streamlit tables, long forms and vertical sidebar scrolling remain;
  this is not a replacement frontend. Not every destructive workflow was
  exercised in the browser; existing mocked functional tests cover them.
- README contains screenshot placeholders rather than committed screenshots.
