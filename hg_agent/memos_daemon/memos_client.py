import time
import requests
from .logging_setup import log


# Status codes worth retrying (server hiccups), vs. permanent client errors.
_TRANSIENT_STATUS = {500, 502, 503, 504}


class MemosClient:
    def __init__(self, base_url, token, max_retries: int = 4,
                 backoff_base: float = 0.5, backoff_cap: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
        self.s.headers["Content-Type"] = "application/json"
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

    # ── Resilient request core ────────────────────────────────────────────────
    def _request(self, method: str, url: str, *, label: str,
                 retry: bool = True, **kw):
        """Perform an HTTP request with bounded exponential backoff on TRANSIENT
        failures (connection refused, timeouts, 5xx) — the signature of Memos
        being briefly down or restarting. Permanent failures (4xx) are returned
        immediately without retry. Returns the Response, or None if all attempts
        failed. Never raises for connection problems."""
        attempts = (self.max_retries if retry else 1)
        last_err = None
        for i in range(attempts):
            try:
                r = self.s.request(method, url, **kw)
                if r.status_code in _TRANSIENT_STATUS and i < attempts - 1:
                    self._sleep(i, f"{label} HTTP {r.status_code}")
                    continue
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                if i < attempts - 1:
                    self._sleep(i, f"{label}: {type(e).__name__}")
                    continue
            except requests.RequestException as e:
                log.error(f"{label} error: {e}")
                return None
        log.error(f"{label} failed after {attempts} attempts: {last_err}")
        return None

    def _sleep(self, attempt: int, why: str):
        delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
        log.warning(f"  ⏳ {why} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{self.max_retries})")
        time.sleep(delay)

    def health(self) -> bool:
        """Quick liveness probe used before/around writes."""
        r = self._request("GET", f"{self.base_url}/api/v1/memos",
                          params={"pageSize": 1}, timeout=5,
                          label="health", retry=False)
        return bool(r is not None and r.status_code == 200)

    def ping(self) -> bool:
        try:
            r = self.s.get(f"{self.base_url}/api/v1/memos",
                           params={"pageSize": 1}, timeout=5)
            if r.status_code != 200:
                return False
            try:
                n = len(r.json().get("memos", []))
                if n == 0:
                    log.warning("Memos list endpoint returned 0 memos. If you DO "
                                "have notes, the list endpoint may be filtering "
                                "PRIVATE memos or need a filter param — the safety "
                                "poll and correlation depend on it. Webhook fetches "
                                "of individual notes may still work.")
            except Exception:
                pass
            return True
        except Exception:
            return False

    def get_memo(self, name: str) -> dict | None:
        """Fetch a single memo by its resource name ('memos/xxx')."""
        r = self._request("GET", f"{self.base_url}/api/v1/{name}", timeout=10,
                          label=f"get_memo {name}")
        if r is None:
            return None
        if r.status_code == 404:
            log.warning(f"get_memo {name}: 404 (not found / not visible to this token)")
            return None
        if r.status_code in (401, 403):
            log.error(f"get_memo {name}: {r.status_code} — token lacks access. "
                      f"Check memos_token scope/owner.")
            return None
        try:
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.error(f"get_memo {name} failed: {e}")
            return None

    def iter_changed_since(self, watermark, page_size):
        page_token = ""
        while True:
            params = {"pageSize": page_size}
            if page_token:
                params["pageToken"] = page_token
            try:
                r = self.s.get(f"{self.base_url}/api/v1/memos", params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                log.error(f"list memos failed: {e}")
                return
            memos = data.get("memos", [])
            if not memos:
                return
            for m in memos:
                if watermark and m.get("updateTime", "") <= watermark:
                    return
                yield m
            page_token = data.get("nextPageToken", "")
            if not page_token:
                return

    # Sentinel returned when a PATCH fails with a permanent (non-retryable) error.
    # Callers check `result is PATCH_PERMANENT_ERROR` to decide whether to
    # discard the pending entry rather than keep retrying indefinitely.
    PATCH_PERMANENT_ERROR = object()

    def update_content(self, note_name, new_content) -> str | None | object:
        """PATCH note content. Returns:
          - updateTime string (or "OK") on success
          - None on transient failure (worth retrying later)
          - MemosClient.PATCH_PERMANENT_ERROR on permanent failure (discard pending)
        Permanent failures: 404 (note deleted), 400 with content-too-long or
        other non-mask errors, 401/403 (auth).
        """
        url = f"{self.base_url}/api/v1/{note_name}"
        body = {"content": new_content}
        def _ut(r):
            try:
                return r.json().get("updateTime", "") or "OK"
            except Exception:
                return "OK"
        for mp in ("updateMask", "update_mask"):
            r = self._request("PATCH", url, params={mp: "content"}, json=body,
                              timeout=15, label=f"PATCH {note_name}")
            if r is None:
                return None  # transient outage exhausted retries
            if r.status_code in (200, 204):
                return _ut(r)
            if r.status_code == 400 and "mask" in r.text.lower():
                continue  # wrong mask param name — try the other variant
            # Permanent errors: don't retry
            if r.status_code in (404, 401, 403):
                log.warning(f"PATCH {note_name} {r.status_code}: "
                            f"{r.text[:160]} — permanent, will not retry")
                return MemosClient.PATCH_PERMANENT_ERROR
            if r.status_code == 400:
                log.warning(f"PATCH {note_name} {r.status_code}: "
                            f"{r.text[:160]} — permanent (bad request)")
                return MemosClient.PATCH_PERMANENT_ERROR
            log.warning(f"PATCH {note_name} {r.status_code}: {r.text[:160]}")
            return None  # unknown — treat as transient
        r = self._request("PATCH", url, json=body, timeout=15,
                          label=f"PATCH {note_name}")
        if r is None:
            return None
        if r.status_code in (200, 204):
            return _ut(r)
        if r.status_code in (404, 401, 403, 400):
            log.warning(f"PATCH {note_name} {r.status_code}: "
                        f"{r.text[:160]} — permanent, will not retry")
            return MemosClient.PATCH_PERMANENT_ERROR
        return None

    def iter_all_memos(self, page_size: int = 50):
        """Iterate all visible memos regardless of updateTime (no watermark).
        Used for corpus building, where we want every note, not just changed ones.
        The Memos list API returns newest-first; we yield all pages until done."""
        page_token = ""
        while True:
            params = {"pageSize": page_size}
            if page_token:
                params["pageToken"] = page_token
            try:
                r = self.s.get(f"{self.base_url}/api/v1/memos",
                               params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.error(f"iter_all_memos failed: {e}")
                return
            memos = data.get("memos", [])
            if not memos:
                return
            yield from memos
            page_token = data.get("nextPageToken", "")
            if not page_token:
                return

    def create_memo(self, content, pinned=False) -> dict | None:
        r = self._request("POST", f"{self.base_url}/api/v1/memos",
                          json={"content": content}, timeout=15,
                          label="create memo")
        if r is None:
            return None
        try:
            r.raise_for_status()
            memo = r.json()
            if pinned and memo.get("name"):
                self.update_pinned(memo["name"], True)
            return memo
        except requests.RequestException as e:
            log.error(f"create memo failed: {e}")
            return None

    def update_pinned(self, note_name, pinned: bool):
        url = f"{self.base_url}/api/v1/{note_name}"
        for mp in ("updateMask", "update_mask"):
            try:
                r = self.s.patch(url, params={mp: "pinned"},
                                 json={"pinned": pinned}, timeout=15)
                if r.status_code in (200, 204):
                    return True
                if r.status_code == 400 and "mask" in r.text.lower():
                    continue
                return False
            except requests.RequestException:
                return False
        return False
