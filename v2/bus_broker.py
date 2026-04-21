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
import sys
import threading
import zmq
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shared.logger import get_logger  # noqa: E402

BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus_v2.sub"

HWM = 200

log = get_logger("bus_broker")


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
            pass

    t = threading.Thread(target=_proxy_thread, daemon=True)
    t.start()

    def _shutdown(signum, frame):
        log.info("Shutdown signal received, closing broker...")
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    stop_event.wait()

    log.info("Broker shutting down...")
    xsub.close()
    xpub.close()
    context.term()
    t.join(timeout=2)
    log.info("Broker stopped.")


if __name__ == "__main__":
    run()
