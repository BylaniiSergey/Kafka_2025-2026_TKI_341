"""
Шлюз управления: не содержит логики модулей, только HTTP-вызовы к микросервисам.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Any
import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

def _url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")

@dataclass
class Settings:
    stop: str = field(default_factory=lambda: _url("STOP_SERVICE_URL", "http://stop:8000"))
    carriage: str = field(default_factory=lambda: _url("CARRIAGE_SERVICE_URL", "http://carriage:8000"))
    tactile: str = field(default_factory=lambda: _url("TACTILE_SERVICE_URL", "http://tactile:8000"))
    temperature: str = field(default_factory=lambda: _url("TEMPERATURE_SERVICE_URL", "http://temperature:8000"))
    heating: str = field(default_factory=lambda: _url("HEATING_SERVICE_URL", "http://heating:8000"))
    cooling: str = field(default_factory=lambda: _url("COOLING_SERVICE_URL", "http://cooling:8000"))

settings = Settings()

@dataclass
class GatewayState:
    """Лёгкое состояние сеанса (остальное — у сервисов)."""

    session_active: bool = False
    system_state: str = "off"
    trusted_sources: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"patient", "doctor_tablet", "rehab_center", "operator", "monitoring"}
        )
    )

state = GatewayState()
app = FastAPI(title="Exoskeleton Control Gateway", version="1.0.0")

def _client() -> httpx.Client:
    return httpx.Client(timeout=10.0)

def _stop_snapshot(c: httpx.Client) -> dict[str, Any]:
    r = c.get(f"{settings.stop}/status")
    r.raise_for_status()
    return r.json()

def _aggregate_telemetry(c: httpx.Client) -> dict[str, Any]:
    parts = {}
    for key, base in [
        ("stop", settings.stop),
        ("carriage", settings.carriage),
        ("tactile", settings.tactile),
        ("temperature", settings.temperature),
        ("heating", settings.heating),
        ("cooling", settings.cooling),
    ]:
        r = c.get(f"{base}/status")
        r.raise_for_status()
        parts[key] = r.json()
    return {
        "gateway": {
            "session_active": state.session_active,
            "system_state": state.system_state,
        },
        "services": parts,
    }

def _apply_climate(c: httpx.Client, body_temp_c: float, air_temp_c: float) -> str:
    r = c.post(f"{settings.temperature}/sensors", json={"body_temp_c": body_temp_c, "air_temp_c": air_temp_c})
    r.raise_for_status()
    r2 = c.post(f"{settings.temperature}/decide")
    r2.raise_for_status()
    mode = r2.json()["climate_mode"]
    if mode == "heating":
        c.post(f"{settings.cooling}/off")
        c.post(f"{settings.heating}/level", json={"level": 0.55})
    elif mode == "cooling":
        c.post(f"{settings.heating}/off")
        c.post(f"{settings.cooling}/speed", json={"speed": 0.65})
    else:
        c.post(f"{settings.heating}/off")
        c.post(f"{settings.cooling}/off")
    return mode

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "control_gateway"}

@app.get("/telemetry")
def telemetry() -> dict[str, Any]:
    with _client() as c:
        return _aggregate_telemetry(c)

@app.post("/commands")
def commands(body: dict[str, Any] = Body(...)) -> JSONResponse:
    cid = body.get("correlation_id")
    action = body.get("action")
    if not action:
        return JSONResponse({"ok": False, "error": "missing_action", "correlation_id": cid}, 422)
    try:
        with _client() as c:
            result = _dispatch(c, action, body)
        ok = result.get("ok", True)
        result["correlation_id"] = cid
        code = 200 if ok else 422
        return JSONResponse(result, status_code=code)
    except httpx.HTTPError as e:
        return JSONResponse(
            {"ok": False, "correlation_id": cid, "error": f"upstream:{e!s}"},
            status_code=502,
        )

def _dispatch(c: httpx.Client, action: str, body: dict[str, Any]) -> dict[str, Any]:
    src = str(body.get("source", ""))

    if action == "initialize":
        c.post(f"{settings.stop}/smooth-stop")
        c.post(f"{settings.heating}/off")
        c.post(f"{settings.cooling}/off")
        state.session_active = False
        state.system_state = "ready"
        return {"ok": True, "result": {"initialized": True}, "snapshot": _aggregate_telemetry(c)}

    if action == "start_session":
        if src not in state.trusted_sources:
            c.post(f"{settings.stop}/emergency-stop", json={"reason": "unauthorized_command"})
            state.session_active = False
            state.system_state = "emergency"
            return {"ok": False, "snapshot": _aggregate_telemetry(c)}
        snap = _stop_snapshot(c)
        if snap.get("stopped"):
            return {"ok": False, "snapshot": _aggregate_telemetry(c)}
        c.post(f"{settings.stop}/allow-movement")
        state.session_active = True
        state.system_state = "session_active"
        return {"ok": True, "snapshot": _aggregate_telemetry(c)}

    if action == "end_session":
        if src not in state.trusted_sources:
            return {"ok": False, "error": "untrusted_source", "snapshot": _aggregate_telemetry(c)}
        c.post(f"{settings.stop}/smooth-stop")
        state.session_active = False
        state.system_state = "ready"
        return {"ok": True, "snapshot": _aggregate_telemetry(c)}

    if action == "emergency_stop":
        if src == "patient":
            reason = "patient_emergency"
        elif src == "monitoring":
            reason = "monitoring_obstacle"
        else:
            reason = "doctor_emergency"
        c.post(f"{settings.stop}/emergency-stop", json={"reason": reason})
        state.session_active = False
        state.system_state = "emergency"
        out = {"ok": True, "snapshot": _aggregate_telemetry(c), "event": {"type": "emergency_stop", "source": src}}
        return out

    if action == "monitoring_stop":
        c.post(f"{settings.stop}/emergency-stop", json={"reason": "monitoring_obstacle"})
        state.session_active = False
        state.system_state = "emergency"
        return {
            "ok": True,
            "snapshot": _aggregate_telemetry(c),
            "event": {"type": "monitoring_stop"},
        }

    if action == "reset_emergency":
        if src not in ("doctor_tablet", "rehab_center", "operator"):
            return {"ok": False, "error": "forbidden_source", "snapshot": _aggregate_telemetry(c)}
        r = c.post(f"{settings.stop}/reset-emergency", json={"authorized": True})
        r.raise_for_status()
        ok = r.json().get("ok", False)
        if ok:
            state.system_state = "stopped"
        return {"ok": ok, "snapshot": _aggregate_telemetry(c)}

    if action == "open_carriage":
        if src not in state.trusted_sources and not bool(body.get("emergency")):
            return {"ok": False, "error": "untrusted_source", "snapshot": _aggregate_telemetry(c)}
        st = _stop_snapshot(c)
        drives_stopped = not st.get("drives_enabled", False)
        r = c.post(
            f"{settings.carriage}/open",
            json={"drives_stopped": drives_stopped, "emergency": bool(body.get("emergency", False))},
        )
        r.raise_for_status()
        return {"ok": r.json().get("ok", False), "snapshot": _aggregate_telemetry(c)}

    if action == "close_carriage":
        if src not in state.trusted_sources:
            return {"ok": False, "error": "untrusted_source", "snapshot": _aggregate_telemetry(c)}
        r = c.post(f"{settings.carriage}/close")
        r.raise_for_status()
        return {"ok": r.json().get("ok", False), "snapshot": _aggregate_telemetry(c)}

    if action == "update_climate":
        mode = _apply_climate(c, float(body["body_temp_c"]), float(body["air_temp_c"]))
        return {"ok": True, "result": {"climate_mode": mode}, "snapshot": _aggregate_telemetry(c)}

    if action == "tactile_contact":
        st = _stop_snapshot(c)
        trusted = bool(body.get("monitoring_ok", False)) and state.session_active and not st.get("stopped", False)
        r = c.post(
            f"{settings.tactile}/emit",
            json={
                "pattern": "contact_sole",
                "intensity": float(body.get("intensity", 0.5)),
                "source_trusted": trusted,
            },
        )
        r.raise_for_status()
        return {"ok": True, "result": {"tactile": r.json().get("message")}, "snapshot": _aggregate_telemetry(c)}

    if action in ("telemetry", "snapshot"):
        return {"ok": True, "snapshot": _aggregate_telemetry(c)}

    return {"ok": False, "error": f"unknown_action:{action}", "snapshot": _aggregate_telemetry(c)}