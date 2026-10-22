"""Identity for the app-facing singer API: verify a gen session token and
derive the stable app device id. The raw token is never stored or logged;
cache keys are sha256(token).

The app device id is an HMAC of the email under the box's secret
(``app_identity_secret``, 32 random bytes persisted by SingStore), so it can't
be derived from an email and passed as ``device_id`` to the web endpoints."""
import hashlib
import hmac
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


def app_device_id(email, secret):
    """32-hex app device id for ``email`` under the box's identity ``secret``."""
    if not secret:
        raise ValueError("app_device_id needs the box's app identity secret")
    key = secret if isinstance(secret, bytes) else bytes.fromhex(secret)
    msg = ("app:" + email.strip().lower()).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


class GenTokenVerifier:
    def __init__(self, gen_client, clock=time.monotonic, *, secret):
        if not secret:
            raise ValueError("GenTokenVerifier needs the box's app identity secret")
        self._gen = gen_client
        self._secret = secret
        self._clock = clock
        self._cache = OrderedDict()  # sha256(token) -> (expires_at, AppUser | None)
        self._lock = threading.Lock()

    def __repr__(self):
        return f"<GenTokenVerifier entries={len(self._cache)}>"

    def device_id_for(self, email):
        """The app device id for ``email`` on this box."""
        return app_device_id(email, self._secret)

    def invalidate(self, token):
        """Forget a cached verdict for ``token`` (gen refused it elsewhere), so
        the next call re-verifies instead of trusting the cache window."""
        key = hashlib.sha256((token or "").encode()).hexdigest()
        with self._lock:
            self._cache.pop(key, None)

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

    def _parse(self, me):
        if not isinstance(me, dict) or not me.get("has_session"):
            return None
        u = me.get("user")
        if not isinstance(u, dict) or u.get("tenant_id"):
            return None  # tenant sessions / admin tokens are not Nomad singers
        email = (u.get("email") or "").strip().lower()
        if not email:
            return None
        name = (u.get("display_name") or "").strip() or None
        return AppUser(email=email, display_name=name, device_id=app_device_id(email, self._secret))

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
