from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

def safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.strip())
    return cleaned.strip("_") or "capture"

def new_package_dir(base: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / f"{stamp}_{safe_label(label)}"
