from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

@dataclass(frozen=True)
class CommandLogEntry:
    direction: str
    text: str
    timestamp: str

def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")

class CommandLogger:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.entries: list[CommandLogEntry] = []

    def record(self, direction: str, text: str) -> None:
        entry = CommandLogEntry(direction=direction, text=text, timestamp=iso_now())
        self.entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(f"{entry.timestamp}\t{entry.direction}\t{entry.text}\n")
