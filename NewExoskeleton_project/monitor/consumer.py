import os
import uuid
import logging

from kafka_bus import EventBus
from .policies import check_operation
from .producer import proceed_to_deliver, block_delivery

MODULE_NAME = os.getenv("MODULE_NAME", "security_link_monitor")
INPUT_TOPIC = os.getenv("SECURITY_GUARD_TOPIC", "exo.link.requests")

logger = logging.getLogger(MODULE_NAME)

bus = EventBus(client_id=f"{MODULE_NAME}-consumer")


def handle_event(payload: dict):
    event_id = str(payload.get("id") or uuid.uuid4())

    logger.info(
        "GUARD CHECK id=%s %s -> %s transport=%s",
        event_id,
        payload.get("source"),
        payload.get("deliver_to"),
        payload.get("transport", "http"),
    )

    allowed, reason = check_operation(event_id, payload)

    if allowed:
        proceed_to_deliver(event_id, payload)
        return

    block_delivery(event_id, payload, reason)


def start_consumer():
    logger.info("%s consumer started, topic=%s", MODULE_NAME, INPUT_TOPIC)
    bus.subscribe(INPUT_TOPIC, handler=handle_event, group_id="security-link-guard")