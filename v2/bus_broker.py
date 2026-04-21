"""
NemoHeadUnit-Wireless v2 — Bus Broker
Central IPC message broker using ZeroMQ XPUB/XSUB pattern.

Started automatically by main.py — do not run manually in production.

Sockets:
    XSUB  ipc:///tmp/nemobus_v2.pub  — receives from publishers
    XPUB  ipc:///tmp/nemobus_v2.sub  — forwards to subscribers

Shutdown strategy:
    zmq.proxy() is blocking and lives in a daemon thread.
    SIGINT/SIGTERM set a stop_event that closes the context from the main
    thread, which unblocks the proxy thread with a ZMQError — clean exit.
"""

import signal
import threading
import logging
import zmq

BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus_v2.sub"

HWM = 200

logging.basicConfig(
    level=logging.INFO,
    format="[bus_broker] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bus_broker")


def run():
    stop_event = threading.Event()
    context = zmq.Context()

    xsub = context.socket(zmq.XSUB)
    xsub.setsockopt(zmq.RCVHWM, HWM)
    xsub.bind(BROKER_PUB_ADDR)

    xpub = context.socket(zmq.XPUB)
    xpub.setsockopt(zmq.SNDHWM, HWM)
    xpub.bind(BROKER_SUB_ADDR)

    log.info(f"XSUB listening on {BROKER_PUB_ADDR}")
    log.info(f"XPUB listening on {BROKER_SUB_ADDR}")
    log.info("Broker ready — forwarding messages (zmq.proxy)")

    def _proxy_thread():
        try:
            zmq.proxy(xsub, xpub)
        except zmq.ZMQError:
            pass  # expected when context is terminated

    t = threading.Thread(target=_proxy_thread, daemon=True)
    t.start()

    def _shutdown(signum, frame):
        log.info("Shutdown signal received, closing broker...")
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Block main thread until stop is requested
    stop_event.wait()

    log.info("Broker shutting down...")
    xsub.close()
    xpub.close()
    context.term()   # unblocks the proxy thread
    t.join(timeout=2)
    log.info("Broker stopped.")


if __name__ == "__main__":
    run()
