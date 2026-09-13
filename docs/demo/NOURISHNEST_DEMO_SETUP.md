# NourishNest recording kit

The finished MP4 is **`artifacts/demo/nourishnest-product-technical-demo.mp4`**: 6:00, 1920 × 1080, 16:9, 30 fps, H.264, with burned-in captions and no audio. It includes all eleven sections from the captured material. The separate 818-word script is ready for the owner's own voice. The earlier Phase 9 video is a different artifact.

After the follow-up request and download approval, `imageio-ffmpeg==0.6.0` supplied a free encoder in uv's cache. Project dependencies and application code were not changed. The MP4 was fully decoded successfully and representative frames were extracted from every section for review. The file is 7,404,660 bytes.

The successful encoding command was:

```powershell
$env:UV_OFFLINE = '0'
uv run --with imageio-ffmpeg==0.6.0 python scripts/demo/encode.py
```

The encoder refuses to overwrite an existing movie. Its download is needed only if the package is not already cached. The following slideshow and manual recording instructions remain available as alternatives.

## View and record without rebuilding

Open `artifacts/demo/presentation.html` in a browser. It uses only local PNGs, with no server or network requirement. Enter full screen, click **Play six-minute demo**, and move the pointer away from the upper-right control. Space pauses/resumes. Playback stops at 6:00; click Replay to restart. All narration is also burned into the PNG frames and supplied as `captions.srt`. The player contains no audio.

If OBS is already installed, use a 1920 × 1080 base/output canvas at 30 fps, add a Window Capture of the fullscreen browser, disable desktop/microphone audio for the silent version, start recording, then start playback. Trim the lead-in and stop at six minutes. Record to MKV and use OBS's Remux Recordings to create the requested MP4 filename. Inspect the export before sharing. To narrate, enable only your own microphone and follow the timed script; no artificial voice or music is supplied. Nothing is installed automatically.

PowerPoint alternative, if already installed: create a 16:9 deck, insert the 71 `frames/*.png` images in filename order as full-slide pictures, and disable click advance. Set each slide's automatic advance to its corresponding `duration` in `timeline.json`, with no transition effect. Use Record Slide Show for your own voice if desired. Export Create a Video at Full HD using recorded timings. The FFmpeg route below is more precise for fractional timings. Do not substitute the eleven storyboard pages for the full caption sequence.

## Exact MP4 assembly after FFmpeg is available

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/demo/assemble.ps1
```

The script refuses to overwrite an existing movie and does not install FFmpeg. Its exact encoder command, run from `artifacts/demo`, is:

```powershell
ffmpeg -n -f concat -safe 0 -i frames.ffconcat -t 360 -vf "fps=30,format=yuv420p" -c:v libx264 -crf 18 -movflags +faststart -an nourishnest-product-technical-demo.mp4
ffprobe -v error -show_entries "format=duration,size:stream=codec_name,width,height,r_frame_rate" -of json nourishnest-product-technical-demo.mp4
```

Expect 360 seconds, 1920 × 1080 and 30/1 frame rate; `-an` intentionally means no audio. Inspect one frame from every section plus the final frame, and listen to any later narrated export. Captions are burned in. Add `captions.srt` as a separately selectable subtitle track only if desired; it is not needed to read the silent version.

## Reproduce the fictional application session

Prerequisite: the repository's existing uv environment. Run from the repository root:

```powershell
$env:UV_OFFLINE = '1'
uv run python scripts/demo/setup.py
```

The launcher hashes/counts the normal SQLite tables read-only, creates an owned system temporary directory, migrates only its new SQLite database, seeds Greenwood Family twice to check repeatability, and runs API `127.0.0.1:18080` plus Streamlit `127.0.0.1:18580`. Both providers are fake. Usage statistics are disabled. API/UI logs are under `build/product-demo` and should not appear in the recording. Do not run another application instance against the normal database during verification. Ports must be free.

Data: Maya and Daniel; vegetarian preferences and peanut allergies; Refrigerator, Freezer and Dry Pantry; ten manually defined foods, eleven dated lots, one tofu low-stock rule, seven original recipes. Dates are relative to setup day. Food nutrition is deliberately illustrative; zero fiber/sugar/sodium values are placeholders, not verified facts. Protein interest is a narrative aspiration, not an implemented high-protein hard constraint. The rice document is original fictional workflow content, explicitly not authoritative safety guidance.

Capture the interface at a 1920 × 1080 viewport. Keep Technical details collapsed. Use the shot list and capture filename order. Refresh Dashboard/Pantry in this disposable dataset, calculate Daniel's Nutrition, and add two portions of Lentil and tomato soup to Monday Dinner in Meal Planner.

For AI Assistant, enable the human-approved workflow. **Leave the optional saved-profile selector empty:** the existing client fails to serialize selected UUID values. Enter the full request from the script with explicit vegetarian/peanut constraints. Capture the five-dinner preview and critic warnings. Name the proposal `Greenwood weeknight groceries`; capture its exact eight quantities. Before approval run:

```powershell
uv run python scripts/demo/evidence.py
uv run python scripts/demo/proof.py proposed
```

Review and check the confirmation, then Approve. Before Create grocery list:

```powershell
uv run python scripts/demo/proof.py approved
```

Execute, capture completion and Grocery Lists, then run:

```powershell
uv run python scripts/demo/proof.py completed
```

This last command validates the UI write, then creates **a separate verification proposal** with a known raw retry key and proves same-key replay. It creates one additional temporary test list after screenshots. Persisted keys are hashes, so they cannot substitute for the UI's raw retry key. The initial incorrect hashed-key retry returned 409 without mutation; the corrected separate-fixture check passed. Run stages once per fresh setup.

The raw captures are already supplied; no browser automation dependency is needed to replay them. To rebuild original diagrams, frames, captions and PDF:

```powershell
uv run python scripts/demo/build.py
uv run pytest scripts/demo/test_demo.py
uv run ruff check .
git diff --check
```

Pillow is already installed; the builder uses system Segoe UI fonts without bundling font files. SVG diagrams are editable. The PDF is a raster storyboard with eleven source page PNGs, allowing visual review without installing ReportLab or a PDF renderer.

## Stop and remove temporary data

Press Enter in the setup terminal, or from a second terminal run `uv run python scripts/demo/cleanup.py`. The owning launcher stops only its own children, removes its temporary directory and writes `artifacts/demo/isolation-verification.json` after comparing normal table counts and hashes. Confirm all three flags are true: normal_database_unchanged, temporary_database_removed, servers_stopped. Do not kill unrelated servers or delete broadly matched temporary folders. A hard-killed launcher cannot promise cleanup; inspect its exact owned path before manual recovery.

All captures used loopback application services and existing local assets. No paid service, model generation, Ollama or live USDA call was made. No packet-level network audit was performed. Original diagrams, captions and text need no third-party service.

## Asset provenance

Existing application images and branding appear in real screenshots. Their exact photo sources, creators and Pexels license are recorded in `static/assets/ATTRIBUTION.md`; that file remains unchanged. Images are serving inspiration, not photos of the fictional family's recipes. New diagrams, layout, text and rice notes are original code-authored demo material. No photos, fonts or other media were downloaded for this kit.
