# flask: Web framework for HTTP endpoints
# redis: Redis database client
# pika: RabbitMQ client library
# os: Access environment variables
# json: Parse message bodies

from flask import Flask, jsonify, Response, request
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
processed_counter = Counter('orders_processed_total', 'Orders processed from RabbitMQ')
import redis
import pika
import os
import json

app = Flask(__name__)

# Connect to Redis
r = redis.Redis(
    host=os.environ.get("REDIS_HOST", "inventory-db"),
    port=int(os.environ.get("REDIS_PORT", 6379)),
    decode_responses=True
)


# RabbitMQ connection will be created lazily to avoid blocking imports (helpful for tests/CI)
connection = None
channel = None

def connect_broker(max_retries: int = None, retry_base: int = None):
    """Establish a blocking connection to RabbitMQ and return a channel.
    This is intentionally lazy so importing the module does not attempt network calls.
    """
    global connection, channel
    if channel is not None and connection is not None:
        return channel

    import time
    # Make retry/backoff configurable for faster tests and flexible deployments
    max_retries = int(max_retries or os.environ.get("BROKER_MAX_RETRIES", 10))
    retry_base = int(retry_base or os.environ.get("BROKER_RETRY_BASE", 2))
    broker_host = os.environ.get("BROKER_HOST", "rabbitmq")
    broker_port = int(os.environ.get("BROKER_PORT", 5672))

    for attempt in range(max_retries):
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=broker_host, port=broker_port)
            )
            channel = connection.channel()
            channel.queue_declare(queue='order_created')
            return channel
        except Exception as e:
            # don't crash import; surface informative logs and retry
            wait = retry_base * (attempt + 1)
            print(f"[inventory-service] RabbitMQ not ready ({e}), retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError("[inventory-service] Could not connect to RabbitMQ after retries.")

# Callback to handle incoming messages

def callback(ch, method, properties, body):
    order = json.loads(body)
    item = order["item"]
    quantity = int(order["quantity"])

    # Increment Prometheus counter
    processed_counter.inc()

    # Consumer no longer mutates inventory; stock is reserved by `order-service`
    print(f"Processed order notification: {order}")
@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

def start_consumer():
    """Start consuming messages from RabbitMQ. Called when running the service, not on import."""
    ch = connect_broker()
    ch.basic_consume(queue='order_created', on_message_callback=callback, auto_ack=True)
    ch.start_consuming()

@app.route("/inventory/<item>", methods=["GET"])
def get_inventory(item):
    stock = r.get(item)
    if stock is None:
        return jsonify({"item": item, "stock": 0})
    return jsonify({"item": item, "stock": int(stock)})


@app.route("/reserve", methods=["POST"])
def reserve():
    data = request.get_json()
    if not data or "item" not in data or "quantity" not in data:
        return jsonify({"error": "item and quantity required"}), 400
    item = data["item"]
    qty = int(data["quantity"])
    default = int(os.environ.get("DEFAULT_STOCK", 100))

    # Lua script: initialize default stock if missing, check and decrement atomically
    lua = '''
    local key = KEYS[1]
    local qty = tonumber(ARGV[1])
    local default = tonumber(ARGV[2])
    local cur = redis.call('GET', key)
    if not cur then
      cur = default
      redis.call('SET', key, cur)
    else
      cur = tonumber(cur)
    end
    if cur < qty then
      return -1
    end
    return redis.call('DECRBY', key, qty)
    '''

    try:
        res = r.eval(lua, 1, item, qty, default)
    except Exception as e:
        return jsonify({"error": "redis error", "detail": str(e)}), 500

    # res == -1 indicates insufficient stock
    if int(res) == -1:
        cur = r.get(item)
        return jsonify({"success": False, "remaining": int(cur) if cur is not None else default}), 200
    return jsonify({"success": True, "remaining": int(res)}), 200

import threading

def run_flask():
    app.run(host="0.0.0.0", port=5001)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    print("Waiting for orders...")
    start_consumer()
