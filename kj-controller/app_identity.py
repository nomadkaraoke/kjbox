"""Identity for the app-facing singer API: verify a gen session token and
derive the stable app device id. The raw token is never stored or logged;
cache keys are sha256(token)."""
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from gen_client import GenApiError

logger = logging.getLogger(__name__)

POSITIVE_TTL = 60.0
NEGATIVE_TTL = 10.0
MAX_ENTRIES = 1000
NAME_MAX = 40


class AppAuthError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AppUser:
    email: str
    display_name: str | None
    device_id: str


def app_device_id(email):
    return hashlib.sha256(("app:" + email.strip().lower()).encode()).hexdigest()[:32]


class GenTokenVerifier:
    def __init__(self, gen_client, clock=time.monotonic):
        self._gen = gen_client
        self._clock = clock
        self._cache = OrderedDict()  # sha256(token) -> (expires_at, AppUser | None)
        self._lock = threading.Lock()

    def __repr__(self):
        return f"<GenTokenVerifier entries={len(self._cache)}>"

    def is_cached(self, token):
        """True when ``verify(token)`` would answer from the cache (no gen call)."""
        key = hashlib.sha256((token or "").encode()).hexdigest()
        with self._lock:
            hit = self._cache.get(key)
            return bool(hit and hit[0] > self._clock())

    def verify(self, token):
        key = hashlib.sha256((token or "").encode()).hexdigest()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                self._cache.move_to_end(key)
                if hit[1] is None:
                    raise AppAuthError("unauthenticated")
                return hit[1]
        if not token:
            raise AppAuthError("unauthenticated")
        try:
            me = self._gen.get_me(token)
        except GenApiError as exc:
            if exc.status in (401, 403):
                self._store(key, NEGATIVE_TTL, None)
                raise AppAuthError("unauthenticated") from None
            logger.warning("gen token verification unavailable (status %s)", exc.status)
            raise AppAuthError("gen_unavailable") from None
        user = self._parse(me)
        self._store(key, POSITIVE_TTL if user else NEGATIVE_TTL, user)
        if user is None:
            raise AppAuthError("unauthenticated")
        return user

    @staticmethod
    def _parse(me):
        if not isinstance(me, dict) or not me.get("has_session"):
            return None
        u = me.get("user")
        if not isinstance(u, dict) or u.get("tenant_id"):
            return None  # tenant sessions / admin tokens are not Nomad singers
        email = (u.get("email") or "").strip().lower()
        if not email:
            return None
        name = (u.get("display_name") or "").strip() or None
        return AppUser(email=email, display_name=name, device_id=app_device_id(email))

    def _store(self, key, ttl, user):
        with self._lock:
            self._cache[key] = (self._clock() + ttl, user)
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_ENTRIES:
                self._cache.popitem(last=False)


def suggested_name(user):
    if user.display_name and user.display_name.strip():
        name = user.display_name.strip().split()[0]
    else:
        local = user.email.split("@")[0].replace(".", " ").replace("_", " ")
        name = " ".join(local.split()).title()
    return name[:NAME_MAX]
