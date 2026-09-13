"""Ask the owning demo launcher to stop its servers and remove its temporary DB."""
from pathlib import Path

path = Path(__file__).resolve().parents[2] / "build/product-demo/stop.request"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("stop\n", encoding="utf-8")
print("Cleanup requested. The owning setup process performs teardown and verifies normal data.")
