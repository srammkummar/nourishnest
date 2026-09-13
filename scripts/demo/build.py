"""Build a captioned 1080p recording kit offline using the installed Pillow."""
import base64
import html
import json
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from story import CAPTURES, SCENES

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/demo"
DOCS = ROOT / "docs/demo"
BG, INK, GREEN, SAGE = "#F6F4ED", "#233C32", "#245642", "#DCE8D1"


def font(size, bold=False):
    return ImageFont.truetype("C:/Windows/Fonts/" + ("segoeuib.ttf" if bold else "segoeui.ttf"), size)


def wrap(draw, text, size, width, bold=False):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if line and draw.textlength(trial, font=font(size, bold)) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


def block(draw, text, xy, width, size=32, fill=INK, bold=False, gap=10):
    x, y = xy
    for line in wrap(draw, text, size, width, bold):
        draw.text((x, y), line, font=font(size, bold), fill=fill)
        y += size + gap
    return y


def canvas(title, subtitle, index):
    im = Image.new("RGB", (1920, 1080), BG)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 24, 1080), fill=GREEN)
    d.text((78, 42), "NOURISHNEST  /  PRODUCT + TECHNICAL DEMO", font=font(23, True), fill=GREEN)
    d.text((78, 90), title, font=font(58, True), fill=INK)
    d.text((80, 176), subtitle, font=font(29), fill=GREEN)
    d.text((80, 1030), "FICTIONAL DATA  •  OFFLINE FAKE PROVIDER  •  NO PURCHASES", font=font(20), fill=GREEN)
    d.text((1710, 1030), f"{index:02d} / 11", font=font(20), fill=GREEN)
    return im


def diagram(scene, index):
    _, slug, title, subtitle, points, _ = scene
    im = canvas(title, subtitle, index)
    d = ImageDraw.Draw(im)
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">',
           f'<rect width="1920" height="1080" fill="{BG}"/>',
           f'<text x="80" y="120" font-family="Segoe UI,sans-serif" font-size="58" fill="{INK}">{html.escape(title)}</text>',
           f'<text x="80" y="200" font-family="Segoe UI,sans-serif" font-size="29" fill="{GREEN}">{html.escape(subtitle)}</text>']
    cols = 3 if slug == "capabilities" else 1
    height = 190 if cols == 3 else min(140, 520 // len(points))
    width = 556 if cols == 3 else 1760
    for n, point in enumerate(points):
        x, y = 80 + (n % cols) * 590, 270 + (n // cols) * (height + 20)
        d.rounded_rectangle((x, y, x + width, y + height), radius=22, fill=SAGE)
        d.text((x + 25, y + 24), f"{n+1:02d}", font=font(26, True), fill=GREEN)
        block(d, point, (x + 88, y + 24), width - 120, 33, bold=True)
        svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="22" fill="{SAGE}"/>')
        for j, line in enumerate(wrap(d, point, 33, width - 120, True)):
            svg.append(f'<text x="{x+35}" y="{y+55+j*43}" font-family="Segoe UI,sans-serif" font-size="33" fill="{INK}">{html.escape(line)}</text>')
    if slug in {"architecture", "agents", "rag", "approval"}:
        im, svg = flow_diagram(title, subtitle, slug, index)
    if slug == "title":
        im = Image.new("RGB", (1920,1080), BG)
        d = ImageDraw.Draw(im)
        d.rectangle((0,0,24,1080), fill=GREEN)
        photo_path = ROOT / "static/assets/recipes/vegetables.webp"
        photo = ImageOps.fit(Image.open(photo_path).convert("RGB"), (680,610))
        im.paste(photo, (1160,230))
        cover = [(90,110,25,"PRODUCT + TECHNICAL DEMONSTRATION"), (85,290,104,"NourishNest"),
                 (90,450,44,"An Agentic AI Household"),(90,510,44,"Nutrition Platform"),
                 (90,635,31,"From pantry awareness to"),(90,680,31,"human-approved meal planning"),
                 (90,805,25,"LOCAL RAG  •  SPECIALIST AGENTS  •  HUMAN CONTROL")]
        svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080"><rect width="1920" height="1080" fill="{BG}"/>',
               '<defs><clipPath id="photo"><rect x="1160" y="230" width="680" height="610"/></clipPath></defs>',
               '<image x="1160" y="230" width="680" height="610" preserveAspectRatio="xMidYMid slice" clip-path="url(#photo)" href="data:image/webp;base64,'+base64.b64encode(photo_path.read_bytes()).decode()+'"/>']
        for x,y,size,line in cover:
            d.text((x,y),line,font=font(size,True),fill=INK)
            svg.append(f'<text x="{x}" y="{y+size}" font-family="Segoe UI,sans-serif" font-size="{size}" fill="{INK}">{html.escape(line)}</text>')
    svg.append('</svg>')
    (DOCS / f"assets/{slug}.svg").write_text("\n".join(svg), encoding="utf-8")
    im.save(DOCS / f"assets/{slug}.png")
    return im


def flow_diagram(title, subtitle, slug, index):
    im = canvas(title, subtitle, index)
    d = ImageDraw.Draw(im)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="{BG}"/>',
           f'<text x="80" y="135" font-family="Segoe UI,sans-serif" font-size="58" fill="{INK}">{html.escape(title)}</text>',
           f'<text x="80" y="208" font-family="Segoe UI,sans-serif" font-size="29" fill="{GREEN}">{html.escape(subtitle)}</text>']
    def box(x, y, w, h, text):
        d.rounded_rectangle((x, y, x+w, y+h), radius=20, fill=SAGE)
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{SAGE}"/>')
        for j, line in enumerate(wrap(d, text, 31, w-46, True)):
            d.text((x+23, y+19+j*40), line, font=font(31, True), fill=INK)
            svg.append(f'<text x="{x+23}" y="{y+50+j*40}" font-family="Segoe UI,sans-serif" font-size="31" fill="{INK}">{html.escape(line)}</text>')
    def arrow(x1, y1, x2, y2):
        d.line((x1,y1,x2,y2), fill=GREEN, width=5)
        head = [(x2,y2),(x2-10,y2-15),(x2+10,y2-15)] if y2>y1 else [(x2,y2),(x2-15,y2-10),(x2-15,y2+10)]
        d.polygon(head, fill=GREEN)
        svg.append(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{GREEN}" stroke-width="5"/><polygon points="'+" ".join(f"{x},{y}" for x,y in head)+f'" fill="{GREEN}"/>')
    if slug == "architecture":
        for i, text in enumerate(["Streamlit · Household experience", "FastAPI · Typed request contracts", "Application services · Business rules", "Deterministic engines · Calculations", "SQLAlchemy + Alembic · Persistence"]):
            box(80,270+i*120,890,86,text)
            if i<4:
                arrow(525,358+i*120,525,388+i*120)
        box(1110,270,720,130,"Agent coordination: supervisor + five specialists")
        box(1110,465,720,130,"Knowledge: chunks → BM25 → rerank → citations")
        box(1110,660,720,170,"Structured USDA adapter; optional Ollama. Both unused in this offline demo.")
    elif slug == "agents":
        box(600,260,720,92,"Supervisor · Typed intent + plan")
        for x, name in [(80,"Pantry specialist"),(700,"Recipe specialist"),(1320,"Knowledge specialist")]:
            arrow(960,358,x+260,432)
            box(x,440,520,105,name)
        box(300,635,590,110,"Nutrition specialist · Calculated totals")
        box(1030,635,590,110,"Grocery specialist · Consolidated shortages")
        arrow(960,555,595,620)
        arrow(905,690,1010,690)
        box(80,810,1760,72,"Typed shared evidence • Safe parallel reads • 9 observed calls / 12 maximum")
    elif slug == "rag":
        boxes = [(80,300,"Original local documents"),(700,300,"Hash + deterministic chunks"),(1320,300,"Household-scoped BM25"),
                 (80,600,"Deterministic reranking"),(700,600,"Exact excerpts + citations"),(1320,600,"Warning-aware evidence")]
        for x,y,text in boxes:
            box(x,y,520,140,text)
        for y in [370,670]:
            arrow(615,y,682,y)
            arrow(1235,y,1302,y)
        arrow(1580,455,340,580)
        box(80,810,1760,72,"Lexical retrieval, not embeddings • Document instructions are never executed")
    else:
        for x,text in [(80,"1. Critic review"),(535,"2. Hashed proposal"),(990,"3. Human approval"),(1445,"4. Execute")]:
            box(x,320,395,130,text)
            if x<1400:
                arrow(x+402,385,x+447,385)
        box(80,560,820,150,"Review exact quantities. Approval alone leaves domain data unchanged.")
        box(1020,560,820,150,"Transaction + version checks. Same retry key returns the same result.")
        box(80,805,1760,76,"Audit links the decision and controlled write • No purchasing or pantry consumption")
    return im, svg


def contact_sheet(paths, destination):
    sheet = Image.new("RGB", (1600, ((len(paths)+3)//4)*250), BG)
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(paths):
        x, y = (i % 4)*400, (i//4)*250
        thumb = ImageOps.contain(Image.open(path), (390, 220))
        sheet.paste(thumb, (x, y))
        draw.text((x+5, y+222), path.stem, font=font(16), fill=INK)
    sheet.save(destination)


def stamp(seconds, srt=False):
    ms = round(seconds * 1000)
    if srt:
        return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"
    return f"{ms//60000}:{ms//1000%60:02d}"


def main():
    for directory in [OUT / "frames", OUT / "storyboard-pages", DOCS / "assets"]:
        directory.mkdir(parents=True, exist_ok=True)
    evidence_cards()
    timeline, pages, srt, script, shotlist = [], [], [], [], []
    elapsed = 0.0
    for index, scene in enumerate(SCENES, 1):
        duration, slug, title, _subtitle, _, narration = scene
        base = diagram(scene, index)
        if index == 1:
            base.save(OUT / "nourishnest-demo-thumbnail.png")
        names = CAPTURES.get(slug, [])
        for name in names:
            assert (OUT / f"captures/{name}.png").is_file(), f"Missing real capture: {name}"
        # Sentence pairs keep cues readable at roughly 130 words per minute.
        sentences = re.split(r"(?<=[.!?])\s+", narration)
        cues = []
        for sentence in sentences:
            words = sentence.split()
            while words:
                cues.append(" ".join(words[:22]))
                words = words[22:]
        total = sum(len(c.split()) for c in cues)
        start = elapsed
        for n, cue in enumerate(cues):
            seconds = duration * len(cue.split()) / total
            frame = base.copy()
            if names and n >= 2:
                name = names[min(len(names)-1, (n-2) * len(names) // max(1, len(cues)-2))]
                capture = Image.open(OUT / f"captures/{name}.png").convert("RGB")
                frame = ImageOps.pad(capture, (1920, 1080), color=BG)
                label = ImageDraw.Draw(frame)
                label.rectangle((0, 0, 1920, 48), fill=BG)
                label.text((35, 8), title + "  /  " + ("RECORDED EVIDENCE" if name.endswith("evidence") else "ACTUAL APPLICATION"), font=font(23, True), fill=GREEN)
            draw = ImageDraw.Draw(frame)
            draw.rounded_rectangle((65, 918, 1855, 1020), radius=14, fill=INK)
            lines = wrap(draw, cue, 32, 1700)
            assert len(lines) <= 2
            for j, line in enumerate(lines):
                draw.text(((1920-draw.textlength(line, font=font(32)))/2, 930+j*39), line, font=font(32), fill="white")
            filename = f"frames/{len(timeline)+1:03d}.png"
            frame.save(OUT / filename)
            timeline.append({"file": filename, "start": round(elapsed, 6), "duration": round(seconds, 6), "section": slug, "caption": cue})
            srt.append(f"{len(timeline)}\n{stamp(elapsed, True)} --> {stamp(elapsed+seconds, True)}\n{cue}\n")
            elapsed += seconds
        script.append(f"## {stamp(start)}–{stamp(elapsed)} | {title}\n\n{narration}\n")
        shotlist.append(f"| {stamp(start)}–{stamp(elapsed)} | {title} | {', '.join(names) if names else slug+' diagram'} |")
        page = Image.new("RGB", (1920, 1080), BG)
        d = ImageDraw.Draw(page)
        d.text((70, 45), f"{index:02d}  {stamp(start)}–{stamp(elapsed)}  {title}", font=font(42, True), fill=INK)
        page.paste(base.resize((1000, 562)), (60, 150))
        end_y = block(d, narration, (1110, 155), 730, 28, gap=9)
        assert end_y < 1000, f"Storyboard narration overflows: {slug}"
        block(d, "VISUAL: " + (", ".join(names) if names else "Original reusable diagram") + ". Captions are burned into every timeline frame. Narration is supplied for your own voice; no audio generated.", (70, 775), 990, 27)
        d.text((70, 1020), "NourishNest • Six-minute recording storyboard • Fictional data", font=font(22), fill=GREEN)
        page.save(OUT / f"storyboard-pages/{index:02d}.png")
        pages.append(page)
    assert abs(elapsed - 360) < .001
    pages[0].save(OUT / "nourishnest-demo-storyboard.pdf", save_all=True, append_images=pages[1:], resolution=144, title="NourishNest demonstration storyboard")
    (OUT / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    concat = []
    for cue in timeline:
        concat.extend([f"file '{cue['file']}'", f"duration {cue['duration']:.6f}"])
    concat.append(f"file '{timeline[-1]['file']}'")
    (OUT / "frames.ffconcat").write_text("ffconcat version 1.0\n"+"\n".join(concat)+"\n", encoding="utf-8")
    words = sum(len(s[-1].split()) for s in SCENES)
    (DOCS / "NOURISHNEST_DEMO_SCRIPT.md").write_text(f"# NourishNest narration\n\n360 seconds; {words} words. Read in your own voice. The supplied slideshow is silent and fully captioned.\n\n"+"\n".join(script), encoding="utf-8")
    (DOCS / "NOURISHNEST_DEMO_SHOT_LIST.md").write_text("# Timed shot list\n\n1920 × 1080, six minutes. Fixed PNG sequence is the deterministic recording source; use `timeline.json` for exact cue timing.\n\n| Time | Section | Real captures / visual |\n|---|---|---|\n"+"\n".join(shotlist), encoding="utf-8")
    player = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>NourishNest - Six-minute demo</title>
<style>html,body{margin:0;background:#233C32;height:100%;overflow:hidden}img{width:100vw;height:100vh;object-fit:contain}button{position:fixed;top:12px;right:18px;padding:12px;font:18px sans-serif}button.running{opacity:0}button:hover{opacity:1}</style>
<img id="slide" alt="NourishNest captioned product and technical demonstration"><button id="play">Play six-minute demo</button>
<script>const cues=TIMELINE;let start=0,paused=0,playing=false;const image=document.getElementById('slide'),button=document.getElementById('play');image.src=cues[0].file;
function toggle(){if(playing){paused=(performance.now()-start)/1000;playing=false;button.className='';button.textContent='Resume';}else{start=performance.now()-paused*1000;playing=true;button.className='running';tick();}}
function tick(){if(!playing)return;let t=(performance.now()-start)/1000;let cue=cues.find(c=>t>=c.start&&t<c.start+c.duration)||cues[cues.length-1];if(!image.src.endsWith(cue.file))image.src=cue.file;if(t>=360){playing=false;paused=0;button.className='';button.textContent='Replay';return;}requestAnimationFrame(tick)}button.onclick=toggle;document.onkeydown=e=>{if(e.code==='Space'){e.preventDefault();toggle();}};</script></html>'''
    (OUT / "presentation.html").write_text(player.replace("TIMELINE", json.dumps(timeline)), encoding="utf-8")
    manifest = {"duration_seconds": 360, "resolution": [1920,1080], "target_fps": 30, "audio": False,
                "burned_captions": True, "narration_words": words, "frames": len(timeline), "mp4_created": False,
                "ffmpeg_available": shutil.which("ffmpeg") is not None, "method": "Pillow slides + actual CUA browser captures; offline HTML player"}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    contact_sheet(sorted((OUT / "captures").glob("*.png")), OUT / "capture-contact-sheet.png")
    contact_sheet([OUT / row["file"] for row in timeline if row is next(r for r in timeline if r["section"] == row["section"])], OUT / "section-contact-sheet.png")
    contact_sheet(sorted((OUT / "storyboard-pages").glob("*.png")), OUT / "storyboard-contact-sheet.png")
    print(json.dumps(manifest, indent=2))


def evidence_cards():
    retrieval = json.loads((OUT / "retrieval-evidence.json").read_text())
    im = canvas("Recorded API evidence", "Cooked rice query • Original fictional household note", 7)
    d = ImageDraw.Draw(im)
    source = retrieval["rice_retrieval"]["results"][1]
    y = block(d, source["document_title"] + " / Storage workflow", (90, 265), 1740, 39, bold=True)
    y = block(d, '“' + source["excerpt"] + '”', (90, y+40), 1710, 34, gap=15)
    block(d, "Separate injection exercise: " + retrieval["injection_retrieval"]["warnings"][0]["message"], (90, y+65), 1710, 33, bold=True)
    im.save(OUT / "captures/rag-evidence.png")
    proof = json.loads((OUT / "completed-proof.json").read_text())
    im = canvas("Recorded execution evidence", "UI execution + separate API replay verification", 9)
    d = ImageDraw.Draw(im)
    y = 265
    for point in ["Six agents • Nine tools • Fake rule-based provider", "Approved proposal: domain tables unchanged",
                  "UI execution: one grocery list, eight items, zero purchases", "Separate same-key API retry: same list, no repeated domain write",
                  "Only grocery tables changed; pantry quantities unchanged", "UI proposal hash: " + proof["proposal_hash"]]:
        y = block(d, point, (90, y), 1720, 30, bold=True) + 42
    im.save(OUT / "captures/execution-evidence.png")


if __name__ == "__main__":
    main()
