"""
NemoHeadUnit-Wireless — Bus Broker
Central IPC message broker using ZeroMQ XPUB/XSUB pattern.

Must be started before any other process:
    python bus_broker.py

Sockets:
    XSUB  ipc:///tmp/nemobus.pub  — receives from publishers
    XPUB  ipc:///tmp/nemobus.sub  — forwards to subscribers

The broker is intentionally dumb: it routes by topic prefix only,
never deserialises frame [1] (header) or frame [2] (payload).
"""

import signal
import sys
import logging
import zmq

BROKER_PUB_ADDR = "ipc:///tmp/nemobus.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus.sub"

# High-water marks — large enough to absorb H.264 IDR burst (~400 KB each)
HWM = 200

logging.basicConfig(
    level=logging.INFO,
    format="[bus_broker] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bus_broker")


def run():
    context = zmq.Context()

    # Publishers connect here and send frames
    xsub = context.socket(zmq.XSUB)
    xsub.setsockopt(zmq.RCVHWM, HWM)
    xsub.bind(BROKER_PUB_ADDR)

    # Subscribers connect here and receive frames
    xpub = context.socket(zmq.XPUB)
    xpub.setsockopt(zmq.SNDHWM, HWM)
    xpub.bind(BROKER_SUB_ADDR)

    log.info(f"XSUB listening on {BROKER_PUB_ADDR}")
    log.info(f"XPUB listening on {BROKER_SUB_ADDR}")
    log.info("Broker ready — forwarding messages (zmq.proxy)")

    def _shutdown(signum, frame):
        log.info("Shutdown signal received, closing broker...")
        xsub.close()
        xpub.close()
        context.term()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Blocking native C proxy — zero Python overhead per message
    zmq.proxy(xsub, xpub)


if __name__ == "__main__":
    run()
