import os
import json
import queue
import logging
import threading

import httpx

from kafka_bus import EventBus, TOPIC_EMERGENCY
from .policies import resolve_target_url

MODULE_NAME = os.getenv("MODULE_NAME", "security_link_monitor")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5.0"))

logger = logging.getLogger(MODULE_NAME)

_requests_queue: queue.Queue = queue.Queue()
bus = EventBus(client_id=MODULE_NAME)


def block_delivery(event_id: str, details: dict, reason: str) -> dict:
    logger.error(
        "BLOCKED id=%s %s -> %s transport=%s reason=%s",
        event_id,
        details.get("source"),
        details.get("deliver_to"),
        details.get("transport"),
        reason,
    )

    bus.publish(TOPIC_EMERGENCY, {
        "source": MODULE_NAME,
        "reason": "unauthorized_intermodule_link",
        "event_id": event_id,
        "original_source": details.get("source"),
        "original_target": details.get("deliver_to"),
        "transport": details.get("transport"),
        "deny_reason": reason,
    })

    return {
        "ok": False,
        "blocked": True,
        "event_id": event_id,
        "reason": reason,
    }


def proceed_to_deliver(event_id: str, details: dict):
    _requests_queue.put((event_id, details))


def _deliver_http(event_id: str, details: dict) -> dict:
    target = details["deliver_to"]
    method = str(details.get("method", "POST")).upper()
    path = str(details.get("path", "/"))
    payload = details.get("payload") or {}
    params = details.get("params") or {}
    headers = details.get("headers") or {}

    base_url = resolve_target_url(target)
    if not base_url:
        return {
            "ok": False,
            "event_id": event_id,
            "error": f"unknown_target_module:{target}",
        }

    if not path.startswith("/"):
        path = "/" + path

    url = f"{base_url}{path}"

    safe_headers = {
        "X-Link-Guard": "1",
        "X-Link-Guard-Event-Id": event_id,
        "X-Original-Source": str(details.get("source", "")),
    }
    safe_headers.update(headers)

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            if method in ("GET", "DELETE"):
                resp = c.request(method, url, params=params, headers=safe_headers)
            else:
                resp = c.request(
                    method, url, params=params, json=payload, headers=safe_headers
                )

        try:
            body = resp.json()
        except Exception:
            body = resp.text

        result = {
            "ok": resp.status_code < 400,
            "event_id": event_id,
            "transport": "http",
            "target": target,
            "status_code": resp.status_code,
            "response": body,
        }

        if result["ok"]:
            logger.info(
                "DELIVERED id=%s %s -> %s %s %s [%s]",
                event_id,
                details.get("source"),
                target,
                method,
                path,
                resp.status_code,
            )
        else:
            logger.error(
                "HTTP DELIVERY FAILED id=%s %s -> %s [%s]",
                event_id,
                details.get("source"),
                target,
                resp.status_code,
            )

        return result

    except Exception as e:
        logger.error(
            "HTTP DELIVERY ERROR id=%s %s -> %s error=%s",
            event_id,
            details.get("source"),
            target,
            e,
        )
        return {
            "ok": False,
            "event_id": event_id,
            "transport": "http",
            "target": target,
            "error": str(e),
        }


def _deliver_kafka(event_id: str, details: dict) -> dict:
    topic = str(details.get("topic", "")).strip()
    payload = details.get("payload") or {}

    outgoing = dict(payload)
    outgoing.setdefault("id", event_id)
    outgoing.setdefault("source", details.get("source"))
    outgoing.setdefault("deliver_to", details.get("deliver_to"))

    published = bus.publish(topic, outgoing)

    if published:
        logger.info(
            "KAFKA DELIVERED id=%s %s -> %s topic=%s",
            event_id,
            details.get("source"),
            details.get("deliver_to"),
            topic,
        )
        return {
            "ok": True,
            "event_id": event_id,
            "transport": "kafka",
            "topic": topic,
        }

    logger.error(
        "KAFKA DELIVERY FAILED id=%s %s -> %s topic=%s",
        event_id,
        details.get("source"),
        details.get("deliver_to"),
        topic,
    )
    return {
        "ok": False,
        "event_id": event_id,
        "transport": "kafka",
        "topic": topic,
        "error": "publish_failed",
    }


def deliver_now(event_id: str, details: dict) -> dict:
    transport = str(details.get("transport", "http")).lower()

    if transport == "http":
        return _deliver_http(event_id, details)

    if transport == "kafka":
        return _deliver_kafka(event_id, details)

    return {
        "ok": False,
        "event_id": event_id,
        "error": f"unknown_transport:{transport}",
    }


def producer_job():
    logger.info("%s producer started", MODULE_NAME)

    while True:
        event_id, details = _requests_queue.get()

        try:
            deliver_now(event_id, details)
        except Exception as e:
            logger.exception("producer_job failed for id=%s: %s", event_id, e)


def start_producer():
    thread = threading.Thread(target=producer_job, daemon=True, name="guard-producer")
    thread.start()