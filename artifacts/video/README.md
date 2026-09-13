# NourishNest: AI-assisted build and product walkthrough

Open **nourishnest-ai-demo.mp4** in a video player. It is a narrated, captioned
1080p walkthrough under five minutes, assembled from actual application screen
captures and original explanatory cards. It is an edited walkthrough, not an
uninterrupted desktop recording or a live model-generation recording.

The video emphasizes the confirmed Codex-assisted development workflow, using
Phase 9A.1 as a concrete example, and explains the in-app assistant's bounded
tools. No other development assistant is claimed without evidence.

## Included files

- `nourishnest-ai-demo.mp4`: H.264 video with AAC narration and burned-in captions.
- `nourishnest-ai-demo.srt`: separate editable captions, approximately aligned
  by spoken-word proportions within each narrated scene.
- `narration.md`: complete spoken script.
- `storyboard.json`: editable scene text and narration.
- `manifest.json`: scene start times and durations.
- `poster.jpg`: video preview image.

## Accuracy and demonstration mode

The application capture used an isolated SQLite database, a fictional Willow
House household, and synthetic stock and member data. FastAPI used loopback
port 8096, Streamlit used 8596, and the assistant used `APP_AI_PROVIDER=fake`.
The fake provider is explicitly labelled in both the application and narration.
No Ollama inference, cloud LLM, or paid video service was invoked.

The actual preview request was:

> Plan 1 vegan dinner for 2 people within 30 minutes, prioritize expiring pantry items, and show what I need to buy.

It selected Dal Tadka, reported 81% pantry coverage, and calculated shortages of
140 g cooked dal and 20 g tomato. The visible tool trace confirmed completion of
`recommendations`, `nutrition_summary`, and `grocery_shortage`. The fourth
available tool, `member_nutrition`, was explained as optional and not claimed
as executed in this request.

The implemented local Ollama adapter is explained, not demonstrated as running.
Full RAG and LangGraph multi-agent orchestration are identified as roadmap items.
The test counts are the preceding Phase 9A.1 verification results, not new test
runs performed while producing this video.

## Evidence and credits

- [Phase 9A.1 implementation and verification](../../docs/PHASE9A1_IMAGE_QA.md)
- [Assistant implementation](../../src/nourish_nest/assistant_services.py)
- [Bounded tool and intent contracts](../../src/nourish_nest/assistant_contracts.py)
- [Optional local Ollama adapter](../../src/nourish_nest/ollama_provider.py)
- [Project architecture](../../docs/ARCHITECTURE.md)
- [Photo sources, creators, and Pexels License](../../static/assets/ATTRIBUTION.md)

Photography visible in this video includes the family kitchen by Vanessa
Loring, vegetable bowl by Anna Tarazevich, and lentil meal by I Own My Food Art.
These are illustrative photographs and imply no endorsement. NourishNest's
brand and assistant illustration are original project assets. Explanatory
cards are original video artwork. No music or third-party video footage is used.

Narration was generated offline using the installed Microsoft Zira Desktop
Windows voice; it does not imitate the project owner's voice. Encoding used
FFmpeg through imageio-ffmpeg. No new application dependency was added to
`pyproject.toml` or the lockfile. Existing uncommitted application changes were
preserved; no commit, push, publication, or external upload was performed.
