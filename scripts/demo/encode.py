"""Encode and validate the existing captioned timeline; no application calls."""
import json
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/demo"


def main():
    encoder = imageio_ffmpeg.get_ffmpeg_exe()
    movie = OUT / "nourishnest-product-technical-demo.mp4"
    subprocess.run([encoder, "-hide_banner", "-loglevel", "error", "-n", "-f", "concat", "-safe", "0",
                    "-i", "frames.ffconcat", "-t", "360", "-vf", "fps=30,format=yuv420p",
                    "-c:v", "libx264", "-threads", "4", "-preset", "medium", "-crf", "18",
                    "-movflags", "+faststart", "-an", movie.name], cwd=OUT, check=True)
    inspection = subprocess.run([encoder, "-hide_banner", "-i", str(movie), "-f", "null", "-"],
                                capture_output=True, text=True, check=True)
    details = inspection.stderr
    assert "1920x1080" in details and "30 fps" in details
    assert re.search(r"Duration: 00:06:00\.00", details)
    assert "Audio:" not in details
    review = OUT / "video-review"
    review.mkdir(exist_ok=True)
    timeline = json.loads((OUT / "timeline.json").read_text())
    sections = {}
    for cue in timeline:
        sections.setdefault(cue["section"], cue["start"] + 1)
    for section, timestamp in sections.items():
        subprocess.run([encoder, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(timestamp),
                        "-i", str(movie), "-frames:v", "1", str(review / f"{section}.png")], check=True)
    (review / "decode-log.txt").write_text(details, encoding="utf-8")
    manifest = json.loads((OUT / "manifest.json").read_text())
    manifest.update(mp4_created=True, ffmpeg_available=True, video_file=movie.name,
                    video_bytes=movie.stat().st_size, duration_seconds=360, fps=30,
                    codec="H.264", pixel_format="yuv420p", audio=False,
                    method="FFmpeg encoding of 71 captured/illustrated captioned frames",
                    full_decode_passed=True, representative_frames=len(sections))
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
