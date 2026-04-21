"""
NemoHeadUnit-Wireless v2 — Bus Broker
Central IPC message broker using ZeroMQ XPUB/XSUB pattern.

Started automatically by main.py — do not run manually in production.

Sockets:
    XSUB  ipc:///tmp/nemobus_v2.pub  — receives from publishers
    XPUB  ipc:///tmp/nemobus_v2.sub  — forwards to subscribers

The broker is intentionally dumb: routes by topic prefix only,
never deserialises frame [1] (header) or frame [2] (payload).
"""

import signal
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

    # Use a capture socket to allow graceful proxy interruption.
    # Sending any message to the capture socket unblocks zmq.proxy_steerable.
    control = context.socket(zmq.PUB)
    control.bind("inproc://broker-control")

    def _shutdown(signum, frame):
        log.info("Shutdown signal received, closing broker...")
        # Terminate the blocking proxy cleanly via a TERMINATE command
        control.send(b"TERMINATE")

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # proxy_steerable blocks until control socket sends TERMINATE
    try:
        zmq.proxy_steerable(xsub, xpub, None, control)
    except zmq.ZMQError:
        pass
    finally:
        log.info("Broker shutting down...")
        control.close()
        xsub.close()
        xpub.close()
        context.term()
        log.info("Broker stopped.")


if __name__ == "__main__":
    run()
