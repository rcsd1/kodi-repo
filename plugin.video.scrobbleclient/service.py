"""
Service entry point.

Runs for the life of Kodi. Polls the player while something is playing, flushes
the offline queue when the network comes back, and otherwise stays idle -- the
Fire Stick 4K Max has little headroom to spare.
"""
import time

import xbmc

from resources.lib.scrobbler import Scrobbler
from resources.lib.store import Store, log

TICK_S = 5
FLUSH_INTERVAL_S = 300


def main():
    monitor = xbmc.Monitor()
    store = Store()
    scrobbler = Scrobbler(store)

    log("service started")
    if store.configured:
        health = store.health()
        log("store reachable: {0}".format(bool(health)))
    else:
        log("store not configured -- open addon settings", xbmc.LOGWARNING)

    last_flush = 0.0
    while not monitor.abortRequested():
        try:
            if scrobbler.tracking:
                scrobbler.tick()
            now = time.time()
            if now - last_flush >= FLUSH_INTERVAL_S:
                last_flush = now
                store.reload()
                store.flush()
        except Exception as exc:
            log("service loop error: {0}".format(exc), xbmc.LOGERROR)

        if monitor.waitForAbort(TICK_S):
            break

    # Kodi is closing mid-playback -- do not lose the position.
    try:
        if scrobbler.tracking:
            scrobbler._save(final=True)
    except Exception:
        pass
    log("service stopped")


if __name__ == "__main__":
    main()
