"""Azure Cost Management (FinOps) queries for the subscription and single resources.

Uses the Cost Management Query API (`CostManagementClient.query.usage`), authenticated
with the same service-principal credential as the rest of the app. Query bodies are
built as plain dicts using the REST wire field names (e.g. "timePeriod", not
"time_period") - the SDK's generated models accept raw mappings directly, and dicts
keep the query-shape logic in one readable place instead of spread across several
nested model constructors.

Every subscription-wide "which resources cost what" question (Cost Analysis, the
per-resource Resource Summary/AI panel, docgen's cost collector) is answered from a
single query grouped by the ResourceId dimension (get_cost_by_resource) - never one
Cost Management call per resource. Only two inherently per-resource questions remain
dedicated per-resource queries: get_cost_breakdown (meter-category breakdown for one
resource) and get_daily_cost_trend (one resource's own daily trend) - both are only
ever invoked for a single, already-selected resource (an investigation, or Resource
Workspace's Cost tab), never looped across every discovered resource.

Application-wide caching. `ResourceService` (and therefore this class) is instantiated
once *per Streamlit session* (`st.session_state.resource_service`, see app.py) - a
per-instance cache would only dedupe calls within one browser session, while every
session in this process shares the same underlying Cost Management quota. The cache,
in-flight tracking, and refresh-cooldown state below are therefore module-level
globals, not instance attributes: every `AzureCostManagement` object created in this
process (i.e. every session) reads and writes the same dict/lock, so the very first
session to ask about a given date range fetches it live and every other session -
concurrent or later - reuses that same result for up to `_CACHE_TTL_SECONDS`.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from azure.core.exceptions import HttpResponseError
from azure.core.rest import HttpRequest
from azure.mgmt.costmanagement import CostManagementClient

from .auth import AzureAuth
from utils.timing import log_timing

logger = logging.getLogger(__name__)

_COST_AGGREGATION = {"totalCost": {"name": "Cost", "function": "Sum"}}
_RESOURCE_ID_GROUPING = [{"type": "Dimension", "name": "ResourceId"}]

# Cost Management throttles per-subscription ("entity") query quota aggressively (observed
# QueriesPer10Sec:11 on this account). Two independent defenses against that:
#  1. The application-wide cache/single-flight below means the whole app fires at most one
#     Cost Management call per distinct (time period[, granularity]) combination, ever -
#     regardless of resource count, page, or how many sessions/threads ask concurrently.
#  2. Retry policy here is deliberately conservative: few retries, and backing off by whatever
#     Azure's own Retry-After header says (falling back to a small, capped exponential backoff
#     only when no header is present) - never a tight retry loop.
#
# Only 1 retry (not 2): a failed fetch is no longer cached (see _cached_fetch's docstring), so a
# second attempt inside the *same* call buys nothing that the next call - the next Streamlit
# rerun, or the user clicking Refresh again - doesn't already get for free. Keeping the retry
# count low instead caps how long any single page load can block waiting on Azure's own
# server-mandated backoff before returning control to the user.
_MAX_429_RETRIES = 1
_DEFAULT_RETRY_AFTER_SECONDS = 5.0
_MAX_RETRY_AFTER_SECONDS = 20.0
_RETRY_AFTER_HEADERS = (
    "Retry-After",
    "retry-after",
    "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after",
)

# Defensive cap on how many `nextLink` pages to follow for a single query. A subscription-wide
# query grouped by ResourceId and aggregated over one time period returns at most one row per
# billed resource, which stays well under Cost Management's per-page row limit for any
# subscription size this app targets - this only guards against an unexpectedly long link
# chain, it is not expected to engage in normal use.
_MAX_PAGINATION_PAGES = 20

# How long a fetched dataset stays valid before the next request re-queries Azure for real.
_CACHE_TTL = timedelta(hours=24)

# A failed/throttled fetch is cached too, but only for this long - long enough that a burst of
# Streamlit reruns during an active throttle doesn't each fire their own live call (which would
# just prolong the throttle), short enough that the very next normal page load after that window
# retries live on its own, with no need for the user to notice or manually hit Refresh. Cannot
# reuse `_CACHE_TTL`: a 24h negative cache is what previously made an ordinary throttling blip
# look like a day-long outage.
_NEGATIVE_CACHE_TTL = timedelta(seconds=45)

# Minimum time between two effective "Refresh Cost Data" actions, application-wide - protects
# the same shared Cost Management quota the cache protects, so a user (or several users)
# repeatedly clicking Refresh can't each force a fresh call.
_REFRESH_COOLDOWN_SECONDS = 30.0

# How long a caller will wait for another thread's already-in-flight fetch of the same key
# before giving up and just fetching itself (self-healing if the original fetch somehow never
# reaches its `finally` - see _cached_fetch).
_INFLIGHT_WAIT_TIMEOUT_SECONDS = 60.0

# Minimum spacing enforced between any two live Cost Management HTTP calls, process-wide,
# regardless of which date range/granularity they're for. The single-flight cache above only
# dedupes callers asking about the *same* key - it does nothing when several different keys
# (e.g. "Month to date" cost-by-resource, its trend chart, and a "Last 7 days" custom range on
# another tab) are cold and requested around the same moment, which lets several distinct live
# queries burst out together and blow through the account's tight per-10-second quota (observed
# QueriesPer10Sec:11) before the retry/backoff logic even gets a chance to react. Spacing every
# call this far apart keeps sustained throughput comfortably under that limit (~0.8 req/s here
# vs. the ~1.1 req/s the quota allows) no matter how many different ranges are requested at once.
_MIN_REQUEST_INTERVAL_SECONDS = 1.2


class _RateLimiter:
    """Process-wide pacing for live Cost Management calls - see _MIN_REQUEST_INTERVAL_SECONDS.

    Each caller reserves the next available slot (spaced `min_interval` apart) under a short
    lock, then sleeps *outside* the lock for its own turn - so callers queue in arrival order
    without blocking each other while one of them is asleep.
    """

    def __init__(self, min_interval_seconds: float):
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._next_slot_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot_at = max(now, self._next_slot_at)
            self._next_slot_at = slot_at + self._min_interval
        sleep_for = slot_at - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)


_rate_limiter = _RateLimiter(_MIN_REQUEST_INTERVAL_SECONDS)

# --- Application-wide (process-wide) cache/single-flight/cooldown state -------------------
# See the module docstring for why these are module globals rather than instance attributes.
_resource_cost_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_resource_cost_inflight: Dict[Tuple[str, str], threading.Event] = {}
_trend_cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
_trend_inflight: Dict[Tuple[str, str, str], threading.Event] = {}
_cache_lock = threading.Lock()
_refresh_state: Dict[str, Optional[datetime]] = {"last_refresh_at": None}


def _resource_type_from_id(resource_id: str) -> str:
    """Best-effort `provider/type` extraction from an ARM resource ID, e.g. 'microsoft.web/sites'."""
    parts = (resource_id or "").strip("/").split("/")
    try:
        providers_index = [p.lower() for p in parts].index("providers")
        provider = parts[providers_index + 1]
        resource_type = parts[providers_index + 2]
        return f"{provider}/{resource_type}".lower()
    except (ValueError, IndexError):
        return "unknown"


def _resource_group_from_id(resource_id: str) -> str:
    """Best-effort resource-group extraction from an ARM resource ID."""
    parts = (resource_id or "").strip("/").split("/")
    try:
        rg_index = [p.lower() for p in parts].index("resourcegroups")
        return parts[rg_index + 1]
    except (ValueError, IndexError):
        return "Unknown"


def _resource_name_from_id(resource_id: str) -> str:
    return (resource_id or "").rstrip("/").split("/")[-1] or "Unknown"


def _column_name(column: Any) -> Optional[str]:
    return column.get("name") if isinstance(column, dict) else getattr(column, "name", None)


def _format_usage_date(value: Any) -> Optional[str]:
    """Cost Management returns UsageDate as an integer like 20260107 (YYYYMMDD)."""
    if value is None:
        return None
    text = str(int(value)) if isinstance(value, (int, float)) else str(value)
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else text


def _month_to_date_period() -> Dict[str, str]:
    """Explicit [first-of-month, today] range, as a substitute for the "MonthToDate"
    shorthand timeframe - on this subscription's billing type that shorthand is accepted
    but silently returns all-zero rows, while the identical window passed as an explicit
    "Custom" timePeriod returns real data. See get_resource_cost's docstring/history."""
    today = datetime.utcnow().date()
    return {"from": today.replace(day=1).isoformat(), "to": today.isoformat()}


def _last_month_period() -> Dict[str, str]:
    """Explicit [first, last] day of the previous calendar month, as a substitute for the
    "TheLastMonth" shorthand timeframe - on this subscription's billing type that shorthand
    is rejected outright ("timeframe TheLastMonth is currently not supported")."""
    first_of_this_month = datetime.utcnow().date().replace(day=1)
    last_day_of_prev_month = first_of_this_month - timedelta(days=1)
    first_day_of_prev_month = last_day_of_prev_month.replace(day=1)
    return {"from": first_day_of_prev_month.isoformat(), "to": last_day_of_prev_month.isoformat()}


def _days_in_period(time_period: Dict[str, str]) -> int:
    start = date.fromisoformat(time_period["from"])
    end = date.fromisoformat(time_period["to"])
    return (end - start).days + 1


def _cached_fetch(
    cache: Dict[Any, Dict[str, Any]],
    inflight: Dict[Any, threading.Event],
    key: Any,
    fetch_fn,
) -> Any:
    """Application-wide cache + single-flight fetch, shared by get_cost_by_resource and
    get_cost_trend. Whichever thread/session first asks about `key` becomes the "owner" and
    actually calls Azure; every other concurrent caller for the *same* key just waits on that
    owner's result instead of firing its own request - this is what turns "N simultaneous
    callers" into exactly one live Cost Management call. A cache clear (see refresh()) is what
    makes the next call, from whoever asks first, become the new owner.

    `fetch_fn` returns `(data, succeeded)`. `succeeded` must be False whenever the live query
    itself failed (429 throttling, network error, ...) - a transient failure comes back as the
    same empty/all-zero shape as a genuine "no cost this period" result. Both are cached, but a
    failure only for `_NEGATIVE_CACHE_TTL` (seconds), a success for the full `_CACHE_TTL`
    (hours): caching a failure for the full 24h TTL previously turned an ordinary throttling blip
    (this account's Cost Management quota is aggressively rate-limited - see _MAX_429_RETRIES)
    into an apparent day-long outage that only the cooldown-gated "Refresh Cost Data" button
    could clear; not caching it at all, tried next, meant every Streamlit rerun during a real
    throttle window fired its own fresh live call, adding more load right when the quota needed
    to recover. The short negative TTL splits the difference - the next *normal* page load after
    that brief window retries live and typically succeeds, with no manual refresh needed.
    """
    while True:
        with _cache_lock:
            entry = cache.get(key)
            if entry is not None:
                ttl = _CACHE_TTL if entry["succeeded"] else _NEGATIVE_CACHE_TTL
                if (datetime.utcnow() - entry["fetched_at"]) < ttl:
                    return entry["data"]

            event = inflight.get(key)
            if event is None:
                event = threading.Event()
                inflight[key] = event
                is_owner = True
            else:
                is_owner = False

        if is_owner:
            try:
                data, succeeded = fetch_fn()
                with _cache_lock:
                    cache[key] = {"data": data, "fetched_at": datetime.utcnow(), "succeeded": succeeded}
            finally:
                with _cache_lock:
                    inflight.pop(key, None)
                event.set()
            return data

        if not event.wait(timeout=_INFLIGHT_WAIT_TIMEOUT_SECONDS):
            logger.warning(
                "Timed out waiting for an in-flight Cost Management fetch (key=%s) - retrying.", key
            )
        # Loop back: normally the owner has now populated the cache and we return it above;
        # in the rare case it didn't (timeout, or the owner's fetch left nothing cached), we
        # simply re-enter the race and may become the owner ourselves rather than hang forever.


class AzureCostManagement:
    """Fetches live Azure Cost Management data for the subscription and single resources."""

    def __init__(self, azure_auth: Optional[AzureAuth] = None):
        self.azure_auth = azure_auth or AzureAuth()
        self._client: Optional[CostManagementClient] = None
        self.last_error: Optional[str] = None

    def _get_client(self) -> CostManagementClient:
        if self._client is None:
            credential = self.azure_auth.get_credential()
            self._client = CostManagementClient(credential)
        return self._client

    def _subscription_scope(self) -> str:
        return f"/subscriptions/{self.azure_auth.subscription_id}"

    @staticmethod
    def _build_query(
        timeframe: str,
        resource_id: Optional[str] = None,
        granularity: Optional[str] = None,
        grouping: Optional[List[Dict[str, str]]] = None,
        time_period: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        dataset: Dict[str, Any] = {"aggregation": _COST_AGGREGATION}
        if granularity:
            dataset["granularity"] = granularity
        if grouping:
            dataset["grouping"] = grouping
        if resource_id:
            dataset["filter"] = {"dimensions": {"name": "ResourceId", "operator": "In", "values": [resource_id]}}

        query: Dict[str, Any] = {"type": "ActualCost", "timeframe": timeframe, "dataset": dataset}
        if time_period:
            query["timePeriod"] = time_period
        return query

    @staticmethod
    def _retry_after_seconds(exc: HttpResponseError, attempt: int) -> float:
        """Prefer whatever Azure itself says to wait; only fall back to a small, capped
        exponential backoff (never a tight/aggressive retry) when no header is present."""
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        for header_name in _RETRY_AFTER_HEADERS:
            value = headers.get(header_name)
            if value is None:
                continue
            try:
                return min(float(value), _MAX_RETRY_AFTER_SECONDS)
            except (TypeError, ValueError):
                continue
        return min(_DEFAULT_RETRY_AFTER_SECONDS * (2 ** attempt), _MAX_RETRY_AFTER_SECONDS)

    def _run_query(
        self,
        scope: str,
        query: Dict[str, Any],
        label: str = "AzureCostManagement query",
        caller: str = "unknown",
    ) -> Optional[Any]:
        time_period = query.get("timePeriod") or {}
        # Every live Cost Management call, whichever method it comes from, is logged exactly
        # once here with scope/date-range/caller so duplicate calls (the same range asked for
        # twice) are visible in the logs rather than silently invisible.
        logger.info(
            "Cost Management API call: label=%s caller=%s scope=%s from=%s to=%s",
            label, caller, scope, time_period.get("from", "-"), time_period.get("to", "-"),
        )
        for attempt in range(_MAX_429_RETRIES + 1):
            try:
                _rate_limiter.wait()
                with log_timing(logger, label):
                    result = self._get_client().query.usage(scope, query)
                self.last_error = None
                self._follow_pagination(result, label)
                return result
            except HttpResponseError as exc:
                if exc.status_code == 429 and attempt < _MAX_429_RETRIES:
                    wait_seconds = self._retry_after_seconds(exc, attempt)
                    logger.warning(
                        "%s throttled (429), retrying in %.1fs (attempt %d/%d)",
                        label, wait_seconds, attempt + 1, _MAX_429_RETRIES,
                    )
                    time.sleep(wait_seconds)
                    continue
                logger.error("Azure Cost Management query failed: %s", exc)
                self.last_error = str(exc)
                return None
            except Exception as exc:
                logger.error("Azure Cost Management query failed: %s", exc)
                self.last_error = str(exc)
                return None
        return None

    def _follow_pagination(self, result: Optional[Any], label: str) -> None:
        """Merge any additional `nextLink` pages into `result.rows` in place. The Query API
        paginates large result sets via a `nextLink` URL rather than a request parameter the
        SDK's `query.usage()` call can honor itself, so continuation pages have to be fetched as
        raw follow-up requests (`CostManagementClient.send_request`) - the previous
        implementation never did this and would have silently returned only page 1 for any
        result that exceeded the per-page row limit."""
        if result is None or not getattr(result, "rows", None):
            return
        pages = 0
        next_link = getattr(result, "next_link", None)
        while next_link and pages < _MAX_PAGINATION_PAGES:
            pages += 1
            try:
                _rate_limiter.wait()
                response = self._get_client().send_request(HttpRequest("GET", next_link))
                response.raise_for_status()
                body = response.json()
                page_properties = body.get("properties", body)
                page_rows = page_properties.get("rows") or []
                if not page_rows:
                    break
                result.rows.extend(page_rows)
                next_link = page_properties.get("nextLink")
            except Exception as exc:
                logger.warning("%s: failed to follow Cost Management pagination link: %s", label, exc)
                break

    @staticmethod
    def _rows_as_dicts(result: Optional[Any]) -> List[Dict[str, Any]]:
        """Single source of truth for turning a raw Cost Management QueryResult into plain
        row dicts - including normalizing Azure's actual column names to the names every
        caller in this module already reads.

        _COST_AGGREGATION requests the aggregated cost column under the key "totalCost", but
        Azure's Query API names the returned column after the aggregation's inner "name" field
        instead ("Cost", per _COST_AGGREGATION) - not the outer key we used to label it in the
        request. Confirmed against the real API: a raw response for a fully-closed, real-spend
        month came back as {"Cost": 2006755.06, "Currency": "INR"}, never {"totalCost": ...}.
        Every consumer here (_fetch_with_period_fallback, _fetch_cost_by_resource and its
        `available` check, _fetch_cost_trend, get_daily_cost_trend, get_cost_breakdown) reads
        row.get("totalCost", ...), so without this rename every real cost value was silently
        read as absent and defaulted to 0.0 - not a throttling or "not finalized yet" issue,
        a field-name mismatch. Renaming once here (rather than at each of those call sites)
        keeps the existing "totalCost" contract intact everywhere downstream.
        """
        if not result or not getattr(result, "rows", None):
            return []
        columns = [_column_name(col) for col in (result.columns or [])]
        rows = [dict(zip(columns, row)) for row in result.rows]
        for row in rows:
            if "totalCost" not in row and "Cost" in row:
                row["totalCost"] = row.pop("Cost")
        return rows

    def _fetch_with_period_fallback(
        self,
        scope: str,
        resource_id: Optional[str] = None,
        grouping: Optional[List[Dict[str, str]]] = None,
        label: str = "AzureCostManagement query",
        caller: str = "unknown",
    ) -> Tuple[List[Dict[str, Any]], str, Dict[str, str]]:
        """Try the current month-to-date window first; fall back to last month if it has no
        real cost in it (not erroring - `self.last_error` stays None).

        On this subscription's billing type, Cost Management doesn't expose actual-cost data
        for the still-open current period at all until it's finalized/reconciled - but it
        doesn't do that by omitting rows. It returns the full row set (one per type/resource
        group/whatever the query grouped by) with every totalCost explicitly 0.0. An
        empty-rows check alone misses this entirely, which is why the first version of this
        fallback still reported "current_month" with a $0 total instead of catching it.
        Falls back whenever nothing in the row set has a nonzero cost, not just when the row
        set itself is empty.

        Returns (rows, period_label, time_period_used) where period_label is
        "current_month", "last_month", or "unavailable" (neither period has real cost).

        Only used by get_cost_breakdown now (a per-resource, meter-category-grouped query) -
        every subscription-wide, ResourceId-grouped question uses
        _cost_by_resource_with_fallback instead, which shares the get_cost_by_resource cache.
        This one is NOT cached - it's only ever fired for a single, already-selected resource.
        """
        for period_label, time_period in (("current_month", _month_to_date_period()), ("last_month", _last_month_period())):
            rows = self._rows_as_dicts(self._run_query(
                scope,
                self._build_query("Custom", resource_id=resource_id, grouping=grouping, time_period=time_period),
                label=f"{label}[{period_label}]",
                caller=caller,
            ))
            if rows and any(row.get("totalCost", 0.0) for row in rows):
                return rows, period_label, time_period
        return [], "unavailable", _month_to_date_period()

    # ------------------------------------------------------------------
    # Subscription-wide, application-wide-cached cost dataset - the single source every
    # FinOps view (Cost Analysis, the per-resource Resource Summary/AI panel, docgen's cost
    # collector) reads from, instead of each issuing its own resource-filtered Cost Management
    # call. See _cached_fetch for the caching/single-flight mechanics.
    # ------------------------------------------------------------------

    def get_cost_by_resource(self, time_period: Dict[str, str], caller: str = "unknown") -> Dict[str, Any]:
        """Actual cost for every resource in the subscription for `time_period`, from a single
        Cost Management query grouped by the ResourceId dimension - never one call per
        resource. Cached application-wide (every session in this process shares the result) for
        up to 24h; any number of simultaneous callers asking about the same period collapse
        into exactly one live call. Call refresh() to force the next call to re-fetch.

        Returns {"time_period", "rows" (list of {resource_id, resource_name, resource_group,
        resource_type, cost, currency}), "by_resource_id" (same rows, keyed by lowercased
        resource id, for O(1) lookup), "currency", "total_cost", "available"}.
        """
        key = (time_period["from"], time_period["to"])
        return _cached_fetch(
            _resource_cost_cache, _resource_cost_inflight, key,
            lambda: self._fetch_cost_by_resource_for_cache(time_period, caller=caller),
        )

    def _fetch_cost_by_resource_for_cache(
        self, time_period: Dict[str, str], caller: str = "unknown"
    ) -> Tuple[Dict[str, Any], bool]:
        """Wraps _fetch_cost_by_resource with the (data, succeeded) contract _cached_fetch
        expects - succeeded is False whenever the underlying query actually failed (429/network
        error - `_run_query` sets `self.last_error` in that case, clears it on success), so a
        transient throttle only sticks around in the cache for _NEGATIVE_CACHE_TTL, not the full
        24h a genuine result gets."""
        data = self._fetch_cost_by_resource(time_period, caller=caller)
        return data, self.last_error is None

    def _fetch_cost_by_resource(self, time_period: Dict[str, str], caller: str = "unknown") -> Dict[str, Any]:
        scope = self._subscription_scope()
        query = self._build_query("Custom", grouping=_RESOURCE_ID_GROUPING, time_period=time_period)
        rows = self._rows_as_dicts(self._run_query(
            scope, query, label="AzureCostManagement.get_cost_by_resource", caller=caller,
        ))

        # Dedup + aggregate by the full (lowercased) ResourceId - the only safe identity here.
        # Cost Management's ResourceId grouping shouldn't itself return the same resource twice
        # for one query, but pagination (_follow_pagination merges nextLink pages into the same
        # row list) has a documented eventual-consistency edge case where a boundary row can be
        # repeated across pages - summing into one entry per resource ID (rather than a plain
        # dict overwrite, which would silently drop one page's cost) keeps totals correct even
        # if that ever happens, instead of relying on it never happening.
        by_resource_id: Dict[str, Dict[str, Any]] = {}
        currency = "USD"
        for row in rows:
            resource_id = row.get("ResourceId") or ""
            if not resource_id:
                continue
            cost = row.get("totalCost", 0.0) or 0.0
            currency = row.get("Currency") or currency
            key = resource_id.lower()
            existing = by_resource_id.get(key)
            if existing is not None:
                existing["cost"] += cost
                continue
            by_resource_id[key] = {
                "resource_id": resource_id,
                "resource_name": _resource_name_from_id(resource_id),
                "resource_group": _resource_group_from_id(resource_id),
                "resource_type": _resource_type_from_id(resource_id),
                "cost": cost,
                "currency": row.get("Currency") or currency,
            }

        # Derived from the deduped-by-resource-ID rows, never the raw (pre-dedup) row list, so
        # the total can never silently drift from what "sum of the rows this dataset exposes"
        # actually adds up to.
        total_cost = sum(item["cost"] for item in by_resource_id.values())

        return {
            "time_period": time_period,
            "rows": list(by_resource_id.values()),
            "by_resource_id": by_resource_id,
            "currency": currency,
            "total_cost": total_cost,
            "available": bool(rows) and any(row.get("totalCost") for row in rows),
        }

    def _cost_by_resource_with_fallback(self, caller: str = "unknown") -> Tuple[str, Dict[str, Any]]:
        """Same "try current month-to-date, fall back to last month" semantics as
        _fetch_with_period_fallback, but sourced from the cached get_cost_by_resource dataset -
        every subscription-wide summary method below shares whatever's already cached instead
        of re-querying."""
        for period_label, time_period in (("current_month", _month_to_date_period()), ("last_month", _last_month_period())):
            data = self.get_cost_by_resource(time_period, caller=caller)
            if data["available"]:
                return period_label, data
        return "unavailable", self.get_cost_by_resource(_month_to_date_period(), caller=caller)

    @staticmethod
    def _totals_by_key(rows: List[Dict[str, Any]], key: str) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for row in rows:
            bucket = row.get(key) or "Unknown"
            totals[bucket] = totals.get(bucket, 0.0) + row.get("cost", 0.0)
        return totals

    def _lookup_resource_row(self, dataset: Dict[str, Any], resource_id: str) -> Optional[Dict[str, Any]]:
        by_id = dataset.get("by_resource_id", {})
        row = by_id.get((resource_id or "").lower())
        if row is not None:
            return row
        # Fall back to matching by leaf name only, in case the caller passed a short id instead
        # of the full ARM resource id Cost Management itself returns. Resource names are not
        # unique (verified on this subscription: e.g. two different "optimusx-backend-plan" App
        # Service Plans exist in different resource groups, billed at very different amounts) -
        # only return a name match when it's unambiguous, otherwise this would silently attach
        # one resource's cost to a different resource that merely happens to share its name.
        leaf = _resource_name_from_id(resource_id).lower()
        matches = [c for c in by_id.values() if _resource_name_from_id(c["resource_id"]).lower() == leaf]
        return matches[0] if len(matches) == 1 else None

    def get_cost_trend(
        self, time_period: Dict[str, str], granularity: str = "Daily", caller: str = "unknown"
    ) -> List[Dict[str, Any]]:
        """Subscription-wide cost trend for `time_period` at Daily or Monthly granularity - one
        ungrouped Cost Management call, cached application-wide (24h TTL, single-flight) by
        (from, to, granularity)."""
        key = (time_period["from"], time_period["to"], granularity)
        return _cached_fetch(
            _trend_cache, _trend_inflight, key,
            lambda: self._fetch_cost_trend_for_cache(time_period, granularity, caller=caller),
        )

    def _fetch_cost_trend_for_cache(
        self, time_period: Dict[str, str], granularity: str, caller: str = "unknown"
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """(data, succeeded) wrapper - see _fetch_cost_by_resource_for_cache's docstring."""
        data = self._fetch_cost_trend(time_period, granularity, caller=caller)
        return data, self.last_error is None

    def _fetch_cost_trend(self, time_period: Dict[str, str], granularity: str, caller: str = "unknown") -> List[Dict[str, Any]]:
        scope = self._subscription_scope()
        query = self._build_query("Custom", granularity=granularity, time_period=time_period)
        rows = self._rows_as_dicts(self._run_query(
            scope, query, label=f"AzureCostManagement.get_cost_trend[{granularity}]", caller=caller,
        ))
        trend = [
            {
                "date": _format_usage_date(row.get("UsageDate")),
                "cost": row.get("totalCost", 0.0) or 0.0,
                "currency": row.get("Currency", "USD"),
            }
            for row in rows
        ]
        trend.sort(key=lambda item: item["date"] or "")
        return trend

    def refresh(self, caller: str = "refresh") -> Dict[str, Any]:
        """Explicit cache-bypassing refresh, wired to the FinOps "Refresh Cost Data" button.

        Rate-limited by _REFRESH_COOLDOWN_SECONDS, application-wide (shared by every session in
        this process) - a click inside the cooldown window clears nothing and reports how much
        longer to wait, so rapid repeated clicks (from one user or several) can't each force a
        fresh Cost Management call. A successful refresh only *clears* the cache; it does not
        eagerly re-fetch anything itself, so exactly one fresh query happens per dataset that's
        actually looked at again afterwards (typically just the one date range currently on
        screen), not a burst of queries for every period anyone has ever viewed.
        """
        with _cache_lock:
            now = datetime.utcnow()
            last = _refresh_state["last_refresh_at"]
            if last is not None:
                elapsed = (now - last).total_seconds()
                if elapsed < _REFRESH_COOLDOWN_SECONDS:
                    remaining = _REFRESH_COOLDOWN_SECONDS - elapsed
                    logger.info(
                        "Cost Management refresh (caller=%s) blocked by cooldown - %.0fs remaining",
                        caller, remaining,
                    )
                    return {"refreshed": False, "retry_after_seconds": remaining}

            _refresh_state["last_refresh_at"] = now
            _resource_cost_cache.clear()
            _trend_cache.clear()

        logger.info("Cost Management cache cleared by explicit refresh (caller=%s)", caller)
        return {"refreshed": True, "retry_after_seconds": 0.0}

    # ------------------------------------------------------------------
    # Per-resource methods. get_resource_cost, get_top_cost_resources,
    # get_cost_by_resource_type/_group and get_subscription_cost_summary are all now backed by
    # the cache above (they were the ones each firing their own subscription-wide query before).
    # get_cost_breakdown and get_daily_cost_trend genuinely need a dedicated per-resource query
    # (meter-category breakdown / that one resource's own daily series) - both are only ever
    # invoked for a single, already-selected resource (an investigation, or Resource Workspace's
    # own Cost tab), never looped across every discovered resource, so they're left as direct
    # per-call queries.
    # ------------------------------------------------------------------

    def get_resource_cost(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Current + last month cost totals for a single resource, read from the shared,
        application-wide, 24h-cached subscription dataset (see get_cost_by_resource) - looking
        this up for any number of different resources, pages, or sessions still only ever costs
        the (at most) 2 live queries that first populated that cache. None if the resource has
        no recorded cost in either period."""
        if not resource_id:
            return None

        # Fired concurrently only on a cold cache (both queries miss) - once cached, each
        # get_cost_by_resource call returns immediately and the pool overhead is negligible.
        with ThreadPoolExecutor(max_workers=2) as pool:
            current_future = pool.submit(self.get_cost_by_resource, _month_to_date_period(), caller="get_resource_cost")
            last_future = pool.submit(self.get_cost_by_resource, _last_month_period(), caller="get_resource_cost")
            current_data = current_future.result()
            last_data = last_future.result()

        current_row = self._lookup_resource_row(current_data, resource_id)
        last_row = self._lookup_resource_row(last_data, resource_id)
        if current_row is None and last_row is None:
            return None

        monthly_cost = current_row["cost"] if current_row else 0.0
        last_month_cost = last_row["cost"] if last_row else 0.0
        currency = (current_row or last_row or {}).get("currency", "USD")

        days_elapsed = datetime.utcnow().day
        daily_cost = (monthly_cost / days_elapsed) if days_elapsed else 0.0
        change_pct = ((monthly_cost - last_month_cost) / last_month_cost * 100) if last_month_cost else 0.0
        trend = "increasing" if change_pct > 5 else "decreasing" if change_pct < -5 else "stable"

        return {
            "resource_id": resource_id,
            "monthly_cost": monthly_cost,
            "last_month_cost": last_month_cost,
            "daily_cost": daily_cost,
            "currency": currency,
            "cost_change_percentage": change_pct,
            "cost_trend": trend,
        }

    def get_daily_cost_trend(self, resource_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Daily cost for a single resource over the trailing `days` days, oldest first."""
        if not resource_id:
            return []

        end = datetime.utcnow().date()
        start = end - timedelta(days=days)
        time_period = {"from": start.isoformat(), "to": end.isoformat()}

        scope = self._subscription_scope()
        query = self._build_query("Custom", resource_id=resource_id, granularity="Daily", time_period=time_period)
        rows = self._rows_as_dicts(self._run_query(
            scope, query, label="AzureCostManagement.get_daily_cost_trend", caller="get_daily_cost_trend",
        ))

        trend = [
            {"date": _format_usage_date(row.get("UsageDate")), "cost": row.get("totalCost", 0.0)}
            for row in rows
        ]
        trend.sort(key=lambda item: item["date"] or "")
        return trend

    def get_cost_breakdown(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Cost for a single resource, broken down by Azure meter category. Prefers the
        current month-to-date window, falling back to last month if that's not published
        yet (see _fetch_with_period_fallback)."""
        if not resource_id:
            return None

        scope = self._subscription_scope()
        grouping = [{"type": "Dimension", "name": "MeterCategory"}]
        rows, period, _ = self._fetch_with_period_fallback(
            scope, resource_id=resource_id, grouping=grouping,
            label="AzureCostManagement.get_cost_breakdown", caller="get_cost_breakdown",
        )
        if not rows:
            return None

        breakdown = {row.get("MeterCategory", "Unknown"): row.get("totalCost", 0.0) for row in rows}
        return {"resource_id": resource_id, "cost_breakdown": breakdown, "cost_period": period}

    def get_cost_by_resource_type(self) -> Dict[str, float]:
        """Subscription-wide cost grouped by Azure resource type (current month-to-date,
        falling back to last month), derived from the shared get_cost_by_resource cache."""
        _period, data = self._cost_by_resource_with_fallback(caller="get_cost_by_resource_type")
        return self._totals_by_key(data["rows"], "resource_type")

    def get_cost_by_resource_group(self) -> Dict[str, float]:
        """Subscription-wide cost grouped by resource group (current month-to-date, falling
        back to last month), derived from the shared get_cost_by_resource cache."""
        _period, data = self._cost_by_resource_with_fallback(caller="get_cost_by_resource_group")
        return self._totals_by_key(data["rows"], "resource_group")

    def get_subscription_cost_summary(self) -> Dict[str, Any]:
        """Subscription-wide cost total plus breakdowns by type and resource group. Prefers
        current month-to-date, falling back to last month if that's not published yet -
        derived entirely from the shared get_cost_by_resource cache, so this no longer issues
        any query of its own beyond whatever populated that cache."""
        period, data = self._cost_by_resource_with_fallback(caller="get_subscription_cost_summary")
        monthly_cost = data["total_cost"]
        daily_cost = (monthly_cost / _days_in_period(data["time_period"])) if period != "unavailable" else 0.0

        return {
            "total_monthly_cost": monthly_cost,
            "total_daily_cost": daily_cost,
            "currency": data["currency"],
            "cost_by_type": self._totals_by_key(data["rows"], "resource_type"),
            "cost_by_resource_group": self._totals_by_key(data["rows"], "resource_group"),
            "cost_period": period,
        }

    def get_top_cost_resources(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Top resources in the subscription by cost, highest first (current month-to-date,
        falling back to last month), derived from the shared get_cost_by_resource cache."""
        _period, data = self._cost_by_resource_with_fallback(caller="get_top_cost_resources")
        items = [
            {
                "resource_id": row["resource_id"],
                "resource_name": row["resource_name"],
                "resource_type": row["resource_type"],
                "monthly_cost": row["cost"],
                "cost_trend": "stable",
            }
            for row in data["rows"]
        ]
        items.sort(key=lambda item: item["monthly_cost"], reverse=True)
        return items[:limit]
