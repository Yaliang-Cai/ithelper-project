"""Durable single-event worker; a separate thread renews leases during network I/O."""
import threading
import uuid


class Worker:
    def __init__(self, domain, agent, *, lease_seconds=150, heartbeat_seconds=None, poll_seconds=.25):
        self.domain, self.agent = domain, agent
        self.worker_id = 'web-' + uuid.uuid4().hex
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds or lease_seconds / 3
        if not 0 < self.heartbeat_seconds < lease_seconds:
            raise ValueError('Heartbeat must be faster than the lease')
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self.last_error = None
        self.recovery_events = {}

    def process_once(self):
        # Also serialize manual dispatch against this instance's background loop.
        if not self._lock.acquire(blocking=False):
            return False
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat = None
        event = None
        try:
            event = self.domain.claim_event(self.worker_id, self.lease_seconds)
            if event is None:
                return False

            def renew():
                while not heartbeat_stop.wait(self.heartbeat_seconds):
                    try:
                        self.domain.renew_event(event['id'], self.worker_id, self.lease_seconds)
                    except Exception:
                        lease_lost.set()
                        self.last_error = 'LEASE_RENEWAL_FAILED'
                        self.recovery_events[event['id']] = self.last_error
                        return

            heartbeat = threading.Thread(target=renew, name='helpdesk-lease', daemon=True)
            heartbeat.start()
            result = self.agent.handle(event)
            # Stop/join renewal before the terminal transition to avoid a false
            # lease failure from a heartbeat racing finish_event.
            heartbeat_stop.set()
            heartbeat.join()
            if not lease_lost.is_set():
                status = 'completed' if result['status'] == 'completed' else 'failed'
                self.domain.finish_event(event['id'], self.worker_id, status)
                self.recovery_events.pop(event['id'], None)
                self.last_error = next(iter(self.recovery_events.values()), None)
            return True
        except Exception:
            # Unknown infrastructure failures may follow committed actions or
            # finalization. Leave the durable lease for restart recovery; Agent
            # reconciles receipts before making any new model request/effect.
            self.last_error = 'WORKER_RECOVERY_PENDING'
            if event:
                self.recovery_events[event['id']] = self.last_error
            return False
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join()
            self._lock.release()

    def _loop(self):
        while not self._stop.is_set():
            self.process_once()
            self._stop.wait(self.poll_seconds)

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='helpdesk-worker', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            # A live call retains its heartbeat until it finishes. Shutdown does
            # not prematurely revoke its lease or run a concurrent replacement.
            self._thread.join(timeout=2)

    @property
    def alive(self):
        return self._thread is not None and self._thread.is_alive()
