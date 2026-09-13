# Demo delivery verification

The initial working tree was clean at committed Phase 10C `8ef6c8e`. No production source, API, database schema, application behavior or existing attribution file was changed for this task. Nothing was committed or pushed.

## Delivered format

- Verified MP4: 6:00, 1920 × 1080, 16:9, 30 fps, H.264/yuv420p, 7,404,660 bytes.
- Actual: offline HTML presentation, 71 captioned PNG frames, SRT, 818-word timed narration, eleven-page storyboard PDF, thumbnail, eleven reusable diagrams in both SVG and PNG, real application captures, setup/cleanup/evidence/build/assembly scripts.
- Audio: none. No artificial voice, voice clone, soundtrack or model-generated media. The narration is ready for the owner's voice.
- MP4: **created** at `artifacts/demo/nourishnest-product-technical-demo.mp4` following the user's follow-up request and approval to download a free encoder. `imageio-ffmpeg==0.6.0` was supplied through uv's cache without changing project dependencies. All 71 captioned frames were encoded. FFmpeg decoded the full movie successfully; eleven representative section frames are in `artifacts/demo/video-review`. No audio stream is present. The initial delivery lacked an encoder; this follow-up completes that export.
- The normal application was captured at a 1920 × 1080 browser viewport. The viewport override was reset and the task tab closed afterward.
- The local HTML file could not be opened by the browser tool because its file-URL policy blocked access; no workaround was attempted. Its offline frame references and timing were checked, but interactive playback was not browser-verified. Open it manually using the setup instructions.

## Evidence and limitations

The six-agent preview used nine calls and five distinct vegetarian dinners. The UI proposal contained eight items: brown rice 100 g, chickpeas 300 g, spinach 300 g, tomatoes 500 g, lentils 100 g, tofu 360 g, bell peppers 500 g and broccoli 100 g. The critic passed with warnings. Approval did not change domain data. UI execution changed only grocery lists, items, generation runs and recipe-source lineage. No purchase or pantry consumption occurred.

The separate API replay fixture used a known raw idempotency key, returned the same list and left domain hashes unchanged on replay. This is explicitly distinguished from the UI's privately held key. An initial attempt to reuse the persisted key hash correctly returned 409; it was replaced by the separate fixture. Reports: `proposed-proof.json`, `approved-proof.json`, `completed-proof.json` and `retrieval-evidence.json` under `artifacts/demo`.

The saved-profile selector has an existing UUID JSON-serialization defect. Production code was left unchanged. The demonstrated approval request uses explicit vegetarian and peanut constraints, with that optional selector empty. Household profiles and nutrition are shown separately. This is not authenticated approval, clinical validation, a live LLM demo or a production deployment.

RAG evidence comes from original fictional rice workflow notes. It demonstrates chunks, lexical BM25/reranking, exact excerpts, scope and injection warnings. The document gives no authoritative time/temperature limits. Seed nutrition is illustrative, not verified food composition. The visible three expiring ingredients in the agent preview use its week horizon; the pantry's two expiring-soon lots use its narrower freshness window.

Normal SQLite tables were inspected read-only and compared by row counts and SHA-256 hashes. The final isolated capture/verification run persisted its baseline before starting servers; `isolation-verification.json` proves equality afterward. An earlier cleanup encountered a Windows file lock and exited before persisting its in-memory baseline. Its exact remaining temporary file/directory was removed after the servers stopped. The launcher was hardened with explicit connection disposal, retry cleanup and persisted baselines; the complete approval/replay verification was repeated successfully in a fresh temporary database. No unverifiable first-run hash comparison is claimed.

The final cleanup script stopped its owning API/Streamlit children on ports 18080/18580 and removed the temporary database. The setup process exited successfully. No model, Ollama, live USDA or paid external-service calls were made by the application or recording scripts. The fake-provider configuration and local assets were checked; no packet capture was performed.

## Checks

- `uv run python -m pytest scripts/demo/test_demo.py`: **6 passed**. The direct `uv run pytest` launcher initially reported a trampoline-path error; Python module invocation succeeded.
- `uv run ruff check .`: **passed** after import fixes limited to demo scripts.
- `git diff --check`: **passed**.
- Full suite: not rerun, as requested; production code was unchanged. The narration accurately reports the previous phase's 864 passes/one failure followed by 24 affected passes after correction.
- All 71 frames are 1920 × 1080, captions are present, the continuous cue sequence totals 360 seconds, and all referenced local frame files exist.
- All eleven section layouts and capture contact sheets were visually reviewed. Unsettled profile/planner/proposal captures were replaced. Technical details remain collapsed; no raw UUIDs, private paths or error tracebacks appear in delivered visual frames.
- The storyboard was generated as eleven full-page raster images. The actual embedded PDF page images were extracted to `pdf-review` for inspection, without installing a PDF renderer. No text/layout overflow was detected; the longest narration page has an explicit bounds assertion.

## Files and provenance

`artifacts/demo/file-manifest.json` lists every delivered file under `scripts/demo`, `docs/demo` and `artifacts/demo`, with exact paths, sizes and hashes (excluding itself and Python caches). `ASSET_ATTRIBUTION.md` preserves the exact existing source/creator/license records. No photos or fonts were downloaded; original code-authored diagrams and existing local Pexels imagery were used.

During the task, unrelated concurrent changes appeared in the root technical DOCX/PDF, `_old` copies, `nourishnest_end_to_end_workflow.png` and a Word lock file. They were not modified or reverted by this task and are excluded from its delivery manifest.
