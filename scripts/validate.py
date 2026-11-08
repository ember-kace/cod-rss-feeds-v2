"""Independently validate the complete publication artifact."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codrss.render import validate_public

if __name__ == "__main__":
    config = json.loads((ROOT / "config/feeds.json").read_text(encoding="utf-8"))
    print(json.dumps(validate_public(ROOT / "public", config), ensure_ascii=False, indent=2))
