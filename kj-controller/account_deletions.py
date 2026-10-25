"""Account-deletion poller: erase local links for gen accounts that were deleted.

When someone deletes their Nomad account, gen queues a deletion for this box.
The box (often offline, a home PC) polls ``GET /api/kjbox/account-deletions``,
erases that person's rows (``SingStore.forget_gen_account``), and only then
acks, which is what removes the entry from gen's pending list. Re-processing
is harmless: the erase is idempotent.

There is no ``since`` cursor: gen lists only pending entries, so every poll
fetches them all and an entry that failed to erase or ack is simply seen again.
An unreachable gen is an expected condition on an offline box: logged at WARNING, retried on
the next tick, never raised.
"""

import logging
import threading

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 600


class AccountDeletionPoller:
    def __init__(self, gen_client, sing_store, interval=DEFAULT_INTERVAL_S):
        self.gen_client = gen_client
        self.sing_store = sing_store
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def poll_once(self):
        """One pass. Returns the number of deletions erased; never raises."""
        try:
            return self._poll()
        except Exception as exc:  # noqa: BLE001 - background thread must survive
            logger.warning("Account-deletion poll failed: %s", exc)
            return 0

    def _poll(self):
        store = self.sing_store
        try:
            deletions = self.gen_client.list_account_deletions()
        except Exception as exc:  # gen down / offline / not deployed yet
            logger.warning("Account-deletion poll: gen unreachable (%s)", exc)
            return 0
        done = 0
        for item in deletions:
            deletion_id = item.get("id")
            email_hash = item.get("email_hash") or deletion_id
            if not deletion_id or not email_hash:
                continue
            try:
                counts = store.forget_gen_account(email_hash)
            except Exception as exc:
                logger.warning("Account deletion %s: local erase failed, will retry (%s)",
                               str(deletion_id)[:12], exc)
                continue  # not acked, so gen lists it again
            done += 1
            logger.info("Account deletion %s erased: %s", str(deletion_id)[:12],
                        {k: v for k, v in counts.items() if v})
            try:
                self.gen_client.ack_account_deletion(deletion_id)
            except Exception as exc:
                logger.warning("Account deletion %s: ack failed, gen will resend (%s)",
                               str(deletion_id)[:12], exc)
        return done

    def start(self):
        """Poll once now, then every ``interval`` seconds, on a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="account-deletions")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)
