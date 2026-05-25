"""
Producer монитора безопасности.

После проверки политики consumer кладёт событие в очередь, отсюда
оно публикуется в Kafka-топик с именем deliver_to (имя целевого
модуля совпадает с именем топика).
"""
import os
import json
import threading
import multiprocessing

from confluent_kafka import Producer


_requests_queue: multiprocessing.Queue = None
MODULE_NAME = os.getenv('MODULE_NAME', 'security_monitor')


def proceed_to_deliver(event_id, details):
    """Вызывается consumer'ом после успешной проверки политики."""
    details = dict(details)
    details.setdefault('id', event_id)
    _requests_queue.put(details)


def producer_job(_, config, requests_queue: multiprocessing.Queue):
    producer = Producer(config)

    def delivery_callback(err, msg):
        if err:
            print(f'[error] Message failed delivery: {err}')
        else:
            print(f"[info] delivered to '{msg.topic()}' "
                  f"partition={msg.partition()} offset={msg.offset()}")

    while True:
        event_details = requests_queue.get()
        print(f"[info] forwarding event: {event_details}")

        topic = event_details['deliver_to']
        producer.produce(
            topic,
            json.dumps(event_details),
            str(event_details['id']),
            callback=delivery_callback,
        )
        producer.poll(10000)
        producer.flush()


def start_producer(args, config, requests_queue):
    print(f'{MODULE_NAME}_producer started')

    global _requests_queue
    _requests_queue = requests_queue

    threading.Thread(
        target=lambda: producer_job(args, config, requests_queue)
    ).start()
