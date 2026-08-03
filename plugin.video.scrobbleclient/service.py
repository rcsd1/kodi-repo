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

# Anything watched on another device reaches this one only through the store,
# so Kodi's own database needs topping up or nothing shows a marker here.
KODI_SYNC_INTERVAL_S = 600


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
    last_kodi_sync = 0.0
    while not monitor.abortRequested():
        try:
            if scrobbler.tracking:
                scrobbler.tick()
            now = time.time()
            if now - last_flush >= FLUSH_INTERVAL_S:
                last_flush = now
                store.reload()
                store.flush()

            if (now - last_kodi_sync >= KODI_SYNC_INTERVAL_S
                    and not scrobbler.tracking):
                last_kodi_sync = now
                try:
                    from resources.lib import kodisync
                    if kodisync.auto_enabled():
                        kodisync.sync_recent()
                except Exception as exc:
                    log("kodi sync failed: {0}".format(exc), xbmc.LOGWARNING)

                # Library items are the only ones whose watched flags
                # persist, so this is the pass that actually shows up. It also
                # writes files for shows started since the last pass, so a new
                # show needs no intervention.
                try:
                    from resources.lib import library
                    result = library.maintain()
                    if result.get("built") or result.get("marked"):
                        log("library: {0}".format(result))
                except Exception as exc:
                    log("library maintenance failed: {0}".format(exc),
                        xbmc.LOGWARNING)
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
