"""Сервис тактильной обратной связи. Автономный."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from fastapi import FastAPI


class TactilePattern(str, Enum):
    CONTACT_SOLE = "contact_sole"
    WARNING = "warning"
    CUSTOM = "custom"


@dataclass
class TactileModule:
    max_intensity: float = 0.85
    last_output: str | None = None
    history: list[str] = field(default_factory=list)

    def emit(
        self,
        pattern: TactilePattern,
        intensity: float,
        *,
        source_trusted: bool,
    ) -> str | None:
        if not source_trusted:
            self._remember("Отказ: источник тактильного сигнала не доверен")
            return None
        clamped = max(0.0, min(float(intensity), self.max_intensity))
        if clamped != intensity:
            self._remember(f"Интенсивность ограничена: {intensity} → {clamped}")
        msg = f"{pattern.value}, интенсивность={clamped:.2f}"
        self.last_output = msg
        self._remember(f"Пациенту: {msg}")
        return msg

    def snapshot(self) -> dict:
        return {
            "service": "tactile",
            "last_output": self.last_output,
            "history_tail": self.history[-8:],
        }

    def _remember(self, line: str) -> None:
        self.history.append(line)


_mod = TactileModule()
app = FastAPI(title="Tactile Service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "tactile"}


@app.get("/status")
def status() -> dict:
    return _mod.snapshot()


@app.post("/emit")
def emit(body: dict) -> dict:
    pattern = TactilePattern(body.get("pattern", "contact_sole"))
    msg = _mod.emit(
        pattern,
        float(body.get("intensity", 0.5)),
        source_trusted=bool(body.get("source_trusted", False)),
    )
    return {"ok": True, "message": msg, "state": _mod.snapshot()}
