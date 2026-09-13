# Knowledge retrieval v1 evaluation

Passed: True; cases: 36

| Metric | Measured | Required | Cases |
|---|---:|---:|---:|
| citation_correctness | 1.0000 | 1 | 36 |
| deterministic_repeat_accuracy | 1.0000 | 1 | 36 |
| hit_at_1 | 1.0000 | 0.85 | 29 |
| hit_at_3 | 1.0000 | 0.95 | 29 |
| hit_at_5 | 1.0000 | 0.95 | 29 |
| household_isolation_accuracy | 1.0000 | 1 | 36 |
| mean_reciprocal_rank | 1.0000 | 0.90 | 29 |
| no_answer_accuracy | 1.0000 | 1 | 7 |
| source_filter_accuracy | 1.0000 | 1 | 6 |

Original fictional fixtures; no medical authority or semantic-search claim.
Citation accuracy is measured per response (all returned citations must be exact).
Empty responses pass citation/isolation checks vacuously; no-answer is scored separately.

## Per-case results

| Case | First relevant rank | Extra checks |
|---|---:|---|
| 01-direct-rice | 1 | True |
| 02-storage | 1 | True |
| 03-paraphrase-rice | 1 | True |
| 04-heading | 1 | True |
| 05-pantry | 1 | True |
| 06-paraphrase-pantry | 1 | True |
| 07-heading-pantry | 1 | True |
| 08-nutrition | 1 | True |
| 09-paraphrase-nutrition | 1 | True |
| 10-lentils | 1 | True |
| 11-paraphrase-recipe | 1 | True |
| 12-own-a | 1 | True |
| 13-own-b | 1 | True |
| 14-isolation-a | 0 | True |
| 15-isolation-b | 0 | True |
| 16-global-a | 1 | True |
| 17-global-b | 1 | True |
| 18-source-safety | 1 | True |
| 19-source-excluded | 0 | True |
| 20-source-nutrition | 1 | True |
| 21-source-recipe | 1 | True |
| 22-source-docs | 1 | True |
| 23-no-answer | 0 | True |
| 24-stopwords | 0 | True |
| 25-punctuation | 0 | True |
| 26-injection | 1 | True |
| 27-injection-query | 1 | True |
| 28-conflict | 1 | True |
| 29-tie | 1 | True |
| 30-deduplicate | 1 | True |
| 31-freezer | 1 | True |
| 32-heading-freezer | 1 | True |
| 33-grocery | 1 | True |
| 34-paraphrase-grocery | 1 | True |
| 35-multiple-filters | 1 | True |
| 36-unrelated | 0 | True |
