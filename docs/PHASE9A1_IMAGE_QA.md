# Phase 9A.1 image mapping and review

## Scope and initial inspection

Started with a clean `git status --short`. No backend, API, database, seed,
navigation, form, confirmation, or CSS behavior was changed. No commit or push.

The previous cuisine-only resolver supplied the same tomato-bowl image to all
five system recipes because their cuisine is `Indian-inspired`. Exact matches
for Indian/Asian/Japanese/Thai instead shared one raw-ingredients photograph.
This affected recipe cards, recipe details, Dashboard recommendations, Meal
Planner recommendations, and AI Assistant recommendation previews. Dashboard
and Household shared the family-kitchen hero. The Dashboard empty state also
used the tomato photograph. Weekly plan entries, seeded food rows, pantry lots,
storage location cards, grocery items, member profiles, and nutrition results
do not have per-item photos; none were added.

Pantry and Grocery already used distinct relevant photos. Nutrition and AI
Assistant already used separate original planning illustrations, retained here.

## Central registry

`src/nourish_nest/ui_assets.py` owns the local registry and deterministic
`recipe_image`, `page_hero_image`, and `food_category_image` functions.
UI callers now pass full recipe/recommendation records instead of just cuisine.
The resolver accepts records, dictionaries, or names, normalizes case and
punctuation, and never selects by position or ID.

Matching order: explicit normalized identity, recipe-name keywords, available
ingredient content, cuisine, dietary category, generic vegetable meal.
Vegan, vegetarian, plant-based, meatless and tofu/tempeh labels exclude chicken
and seafood at every tier. Chicken content excludes seafood. All cuisine and
generic fallbacks are plant-based. Missing assets resolve to the generic meal;
if that file is also missing, an accessible text panel replaces the image.

| Recipe/category | Local asset under `static/assets` |
|---|---|
| Rice, biryani, pulao, khichdi | `recipes/rice.webp` |
| Pasta, spaghetti, penne | `recipes/pasta.webp` |
| Soup, stew, broth | `recipes/soup.webp` |
| Breakfast, oats, porridge | `recipes/breakfast.webp` |
| Chicken | `recipes/chicken.webp` |
| Fish, salmon, seafood | `recipes/seafood.webp` |
| Curry, palak, masala | `recipes/curry.webp` |
| Beans, lentils, dal, chickpeas | `recipes/legumes.webp` |
| Vegetable/vegan/vegetarian meal; unknown recipe | `recipes/vegetables.webp` |
| Salad, slaw | `recipes/salad.webp` |

Explicit seeded mappings: Dal Tadka and Chana Masala use legumes; Palak Paneer
uses curry; Chicken Rice Bowl uses chicken; Vegetable Khichdi uses rice.
Related legume recipes intentionally share an image. These are serving
inspirations, not exact photographs of the saved recipes. Unrecognized cuisines
and recipes still use the safe vegetable meal. Unknown raw food categories
also receive category serving inspiration rather than an invented exact photo.

| Page | Hero purpose / asset |
|---|---|
| Dashboard | Balanced vegetable meal, `recipes/vegetables.webp` |
| Recipes | Plated vegan pasta, `recipes/pasta.webp` |
| Meal Planner | Weekly portioned meals, `hero/meal-prep.webp` |
| Pantry | Organized shelves, `pantry/shelves.webp` |
| Grocery Lists | Fresh shopping basket, `grocery/basket.webp` |
| Nutrition | Plate and nutrition notebook, `nutrition/workspace.svg` |
| Household | Family preparing food, `hero/kitchen.webp` |
| AI Assistant | Calm meal-planning notebook, `assistant/planning.svg` |

## Assets and licensing

The ten recipe files above and `hero/meal-prep.webp` are the eleven downloaded
additions. They were cropped to 800 × 500 and optimized as WebP. Additions total
461,742 bytes; the entire WebP library totals 737,440 bytes (about 720 KiB),
with a 1 MB regression budget. Existing unused assets were preserved.

Exact source pages, original download URLs, verified creator names and the
Pexels License are recorded in [ATTRIBUTION.md](../static/assets/ATTRIBUTION.md).
The Pexels License permits free application use and modification subject to
its restrictions; it is not a public-domain or Creative Commons claim.
There are no runtime remote image requests. Every registry entry has alt text.
No paid service, image-generation call, or external AI provider call occurred.
The automated tests use their existing fake/mocked providers.

## Browser review

Used a disposable SQLite database with one synthetic household, all five
development recipes, and synthetic pantry stock. FastAPI listened only on
127.0.0.1:8097; Streamlit used 127.0.0.1:8597. AI was disabled. The normal
database and the user's existing servers were not used.

Inspected all eight pages at **1366 × 768** and **1024 × 768**. Checked distinct
recipe categories in Recipes and Dashboard recommendations, relevant page
heroes, readable text, and retained layouts. Observed local recipe images
loaded successfully and compact images remained 155 px tall with `object-fit:
cover`; existing hero limits and responsive CSS were preserved. Technical
details stayed collapsed. Pantry and Nutrition initially displayed their
existing refresh/no-member states; no profile or inventory mutations were
submitted in the browser. Dashboard meal ideas used the existing deterministic
recommendation endpoint. Existing interaction tests cover recipe selection,
editing, planner state and dashboard navigation; destructive flows were not
repeated manually.

Some initial screenshots captured a previous browser frame and were discarded.
Final review captures:

- [Recipes, 1366](phase9a1/recipes-1366.png)
- [Dashboard, 1366](phase9a1/dashboard-1366.png)
- [Dashboard recommendations, 1366](phase9a1/dashboard-recommendations-1366.png)
- [Recipes, 1024](phase9a1/recipes-1024.png)
- [Dashboard, 1024](phase9a1/dashboard-1024.png)

Temporary servers were stopped, the browser viewport override reset, and the
temporary browser tab, database, source downloads, scripts and logs removed.

## Verification

- Focused image and UI regression tests: **141 passed**.
- Single full-suite run (`uv run python -m pytest`): **613 passed**, 57 existing
  dependency deprecation warnings, 598.06 seconds.
- `uv run ruff check .`: passed after removing a redundant import alias.
- `git diff --check`: passed.
- The pytest executable launcher failed before the first focused collection;
  running the same installed pytest through `python -m pytest` resolved it.
  The initial focused run also identified the new module missing from the
  UI import-boundary allowlist; that test now checks the registry too.
