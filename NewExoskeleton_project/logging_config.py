"""
Централизованная настройка логирования.
Глушит шумные библиотеки (kafka, httpx, uvicorn access).
"""

import logging

NOISY_LOGGERS = [
    "kafka", "kafka.conn", "kafka.client", "kafka.cluster",
    "kafka.coordinator", "kafka.coordinator.consumer",
    "kafka.coordinator.assignors", "kafka.coordinator.assignors.range",
    "kafka.consumer", "kafka.consumer.fetcher",
    "kafka.consumer.subscription_state",
    "kafka.producer", "kafka.producer.sender",
    "kafka.producer.record_accumulator", "kafka.metrics", "kafka.admin",
    "httpx", "httpcore", "httpcore.connection", "httpcore.http11",
    "uvicorn", "uvicorn.access", "uvicorn.error", "uvicorn.lifespan",
    "fastapi", "asyncio", "multipart",
]


def setup_logging(
    app_level: int = logging.INFO,
    lib_level: int = logging.WARNING,
):
    logging.basicConfig(
        level=app_level,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    )
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(lib_level)