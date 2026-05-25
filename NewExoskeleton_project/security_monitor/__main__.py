"""
Точка входа монитора безопасности.

Запуск:
    python -m security_monitor              # обычный режим
    python -m security_monitor --reset      # сброс offset в начало

Переменные окружения:
    MODULE_NAME             — имя топика (по умолчанию: security_monitor)
    KAFKA_BOOTSTRAP_SERVERS — адрес Kafka (по умолчанию: kafka:9092)
"""
import argparse
import multiprocessing
import os
import time

from .consumer import start_consumer
from .producer import start_producer


def parse_args():
    parser = argparse.ArgumentParser(description="Security Monitor")
    parser.add_argument(
        '--reset',
        action='store_true',
        help='Сбросить offset в начало топика',
    )
    return parser.parse_args()


def main():
    args = parse_args()

    bootstrap = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
    module = os.getenv('MODULE_NAME', 'security_monitor')

    consumer_config = {
        'bootstrap.servers': bootstrap,
        'group.id': f'{module}-group',
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': True,
    }

    producer_config = {
        'bootstrap.servers': bootstrap,
        'client.id': f'{module}-producer',
        'acks': 'all',
    }

    requests_queue: multiprocessing.Queue = multiprocessing.Queue()

    start_producer(args, producer_config, requests_queue)
    start_consumer(args, consumer_config)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print(f'{module} stopped by user')


if __name__ == '__main__':
    main()
