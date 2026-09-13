"""Inspect raster PDF pages and inventory the finished offline recording kit."""
import hashlib
import json
import re
import socket
from io import BytesIO
from pathlib import Path

from PIL import Image

from build import contact_sheet

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/demo"


def main():
    data = (OUT / "nourishnest-demo-storyboard.pdf").read_bytes()
    # Pillow writes one full-page DCT image per page. Extract the actual embedded
    # page images for visual QA; no vector rendering or optional PDF dependency.
    streams = re.findall(rb"/Subtype /Image\b.*?stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL)
    assert len(streams) == len(re.findall(rb"/Type /Page\b", data)) == 11
    review = OUT / "pdf-review"
    review.mkdir(exist_ok=True)
    for index, stream in enumerate(streams, 1):
        with Image.open(BytesIO(stream)) as page:
            assert page.size == (1920, 1080)
            page.save(review / f"{index:02d}.png")
    contact_sheet(sorted(review.glob("*.png")), OUT / "pdf-contact-sheet.png")
    isolation = json.loads((OUT / "isolation-verification.json").read_text())
    assert isolation["before"] == isolation["after"]
    assert isolation["temporary_database_removed"] and isolation["servers_stopped"]
    for port in [18080, 18580]:
        with socket.socket() as client:
            client.settimeout(1)
            assert client.connect_ex(("127.0.0.1", port)) != 0, "Temporary server still listening"
    attribution = "# Demo asset provenance\n\nThe cover reuses recipes/vegetables.webp. Other existing images appear in real application captures. All exact source URLs, creator names and license statements below are copied from the existing registry; no new media was downloaded. New diagrams, layout, narration and fictional notes are original code-authored material. System fonts are rendered, not bundled.\n\n"
    attribution += (ROOT / "static/assets/ATTRIBUTION.md").read_text(encoding="utf-8")
    (ROOT / "docs/demo/ASSET_ATTRIBUTION.md").write_text(attribution, encoding="utf-8")
    paths = []
    for directory in ["scripts/demo", "docs/demo", "artifacts/demo"]:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != "file-manifest.json")
    manifest = [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size,
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(paths)]
    (OUT / "file-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"pdf_pages": len(streams), "normal_database_unchanged": True,
                      "servers_stopped": True, "temporary_database_removed": True,
                      "inventoried_files": len(manifest), "bundle_bytes": sum(p["bytes"] for p in manifest)}, indent=2))


if __name__ == "__main__":
    main()
