"""Shared request policy for the collectors: identity, pacing, and link checking.

Every collector that talks to the target over plain HTTP uses this module, so the
audit's footprint on a third-party site is set in one place rather than per script:

  * AUDIT_UA  — one identifying User-Agent (override with the AUDIT_UA env var), so a
                site owner can tell audit traffic apart in their logs.
  * Pacer     — at least DELAY seconds between requests to the SAME host (default 0.4).
                Other hosts are not slowed; the target is never hammered.
  * check_links — dedupe, HEAD first (GET to confirm anything that looks broken, since
                many servers mishandle HEAD), a per-invocation cap (QA_MAX_LINKS), and a
                cross-stage cache (LINK_CACHE) so homepage, deep-page and SEO passes never
                re-request the same URL. Links not checked because of the cap are returned
                explicitly, never dropped silently.

Env: AUDIT_UA DELAY(0.4) QA_MAX_LINKS(300) LINK_CACHE(unset = no cross-stage cache)
"""

import json
import os
import time
from urllib.parse import urldefrag, urlparse

DEFAULT_UA = "Mozilla/5.0 (compatible; WebsiteAudit/1.0; +https://github.com/streetshadows/website-audit-tool)"
AUDIT_UA = os.environ.get("AUDIT_UA") or DEFAULT_UA


def delay():
    return float(os.environ.get("DELAY", 0.4))


class Pacer:
    """Enforce a minimum interval between requests to the same host."""

    def __init__(self, interval=None):
        self.interval = delay() if interval is None else interval
        self.last = {}

    def wait(self, url):
        host = urlparse(url).netloc.lower()
        gap = time.monotonic() - self.last.get(host, -1e9)
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self.last[host] = time.monotonic()


PACER = Pacer()  # module-wide, so every request a collector makes shares one pacing state


def _load_cache(path):
    try:
        return json.load(open(path)) if path and os.path.exists(path) else {}
    except Exception:
        return {}


def check_links(urls, status_fn, cap=None, cache_path=None):
    """Check each URL once. status_fn(url, method) -> int HTTP status or "ERR:<reason>".

    Returns (results, not_checked): results maps url -> status (int or "ERR:..."), for the
    URLs checked now or found in the cache; not_checked lists URLs skipped by the cap.
    """
    cap = int(os.environ.get("QA_MAX_LINKS", 300)) if cap is None else cap
    cache_path = os.environ.get("LINK_CACHE") if cache_path is None else cache_path
    cache = _load_cache(cache_path)
    results, not_checked, fetched = {}, [], 0
    for u in sorted({urldefrag(u)[0] for u in urls}):
        if u in cache:
            results[u] = cache[u]
            continue
        if fetched >= cap:
            not_checked.append(u)
            continue
        PACER.wait(u)
        st = status_fn(u, "HEAD")
        if not isinstance(st, int) or st >= 400:
            PACER.wait(u)
            st = status_fn(u, "GET")  # confirm: HEAD is often unsupported or answered differently
        fetched += 1
        results[u] = cache[u] = st
    if cache_path:
        try:
            json.dump(cache, open(cache_path, "w"), indent=1)
        except Exception:
            pass
    return results, not_checked
