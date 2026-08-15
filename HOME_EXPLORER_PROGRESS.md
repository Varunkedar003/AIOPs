# FinOps / Monitoring & Observability — Work Log (2026-08-12 to 2026-08-13)

Read this before touching FinOps (Cost tab), the Utilization/Subscription Health overview, the
sidebar's resource categories, or Monitoring & Observability (Metrics/Alerts/Logs). Covers what
was broken, why, what was fixed, and what's still an open/external constraint rather than a code
bug.

## Where the code lives

- **Cost Management queries:** `providers/azure/cost_management.py` (`AzureCostManagement`) -
  all live Azure Cost Management Query API calls. `providers/cost_provider.py` wraps it as
  `MockCostProvider` (name predates it becoming a live integration).
- **Cost tab UI:** `dashboard/cost.py`.
- **Sidebar resource categories + "Other Resources" catch-all:** `_CATEGORY_ARM_TYPES` /
  `_NOISE_ARM_TYPES` / `get_azure_resources()` in `services/resource_service.py`;
  rendered by `dashboard/sidebar.py`.
- **Subscription-wide Utilization overview (health + alerts sampling):**
  `ResourceService.get_subscription_health_overview()` in `services/resource_service.py`,
  rendered by `dashboard/metrics.py`'s `render_subscription_health_overview()`.
- **Azure Monitor platform metrics:** `providers/azure/monitor.py` (`AzureMonitorMetrics`).
- **Log Analytics logs:** `providers/azure/log_analytics.py` (`AzureLogAnalytics`).
- **Resource Health + fired alerts:** `providers/azure/alerts.py` (`AzureAlerts`) - reviewed,
  no bugs found; its "Unknown"/empty results are genuine Azure Resource Health API limitations
  or the resource genuinely having no alert rules, not a code defect.

## What changed, in order

1. **Cost tab showed $0 / "Cost data unavailable" everywhere.** Root cause: the code used
   Cost Management's `"MonthToDate"`/`"TheLastMonth"` shorthand timeframes. On this
   subscription's billing type, `"TheLastMonth"` is rejected outright
   (`BadRequest: timeframe TheLastMonth is currently not supported`), and `"MonthToDate"`
   returns a clean, successful, **all-zero** response instead of erroring. Fixed by switching
   every query to `"Custom"` with explicit computed date ranges
   (`_month_to_date_period()` / `_last_month_period()`).
2. **Current month-to-date is genuinely unpublished on this account.** Even with the
   timeframe fixed, the still-open current period comes back with every row's `totalCost`
   explicitly `0.0` (not missing rows - a real, successful, zero response) because this
   billing type doesn't expose actual cost for an open period until it's finalized. Last
   month (fully closed) has real data (verified: ₹20+ lakh). Added
   `_fetch_with_period_fallback()`: tries current month, and if **no row has a nonzero
   cost** (not just "no rows" - the first version of this check only caught the latter and
   missed the all-zero-rows case), falls back to last month. `get_subscription_cost_summary`
   returns a `"cost_period"` field (`"current_month"` / `"last_month"` / `"unavailable"`) so
   `dashboard/cost.py` can caption which period is actually being shown instead of silently
   swapping numbers.
3. **Currency bug.** This subscription bills in **INR**, but `dashboard/cost.py` hardcoded a
   `$` sign on every figure. Added `_money()` (currency-aware formatting) and threaded the
   real `currency` field through every metric/chart/table in the Cost tab.
4. **Cost Management is aggressively rate-limited** (observed a per-subscription "entity"
   quota as tight as `QueriesPer10Sec:11`, refilling in the ~5-20s the
   `x-ms-ratelimit-microsoft.costmanagement-entity-retry-after` header specifies, plus a much
   smaller, slower-refilling tenant-wide quota). A single FinOps page load fires ~7-10 Cost
   Management queries; with no retry logic, one throttled call failed permanently for the
   rest of that Streamlit session. Added retry/backoff in `_run_query` that honors the
   server's `Retry-After`/rate-limit headers (`_MAX_429_RETRIES=2`, capped wait 20s).
5. **Page was taking 6-7 minutes to load / Cost tab looked "stuck".** Root cause: every
   independent live call was running **serially**, and Streamlit runs both tabs' bodies on
   every script pass regardless of which tab is visible - so Utilization's health/alerts
   scan had to finish before the Cost tab's code even started. Parallelized with
   `ThreadPoolExecutor`: the 2 queries inside `get_resource_cost`, the 3 inside
   `get_subscription_cost_summary`, the 5 independent calls in `render_cost_dashboard`
   (cost/summary/trend/breakdown/top-resources), and the per-resource health+alerts scan in
   `get_subscription_health_overview`. This is also why a throttled retry (which blocks for
   tens of seconds) no longer stacks across every query on the page.
6. **Utilization tab only reflected ~7 ARM types.** `get_subscription_health_overview` was
   sourcing from `get_azure_resources()`, which only recognizes the 7 ARM types the
   sidebar/explorer categorizes by (App Service, SQL, Redis, Storage, Key Vault, App
   Insights, Log Analytics) - everything else (VMs, AKS, Databricks, Container Registry,
   ...) was silently dropped before sampling even started. Switched to the full inventory
   (`get_all_azure_resources_topology_only()`, ~771 resources).
7. **Utilization sample size.** Was hardcoded to 10, then bumped to 25 by user choice, then
   the user asked for full coverage - `limit` is now `Optional[int] = None` (default = every
   discovered resource, ~771 → ~1,500 live Azure Monitor/Resource Health calls,
   `max_workers=25`). First load per session takes roughly 1-2 minutes; every resource's
   health/alerts are cached per-session afterward (`ResourceService._cached`), so subsequent
   tab visits are instant. Pass an explicit `limit` if a caller wants the cheaper bounded
   version back.
8. **Sidebar only showed 7 resource categories.** Added PostgreSQL Servers (explicit ask)
   plus 14 more real categories with meaningful counts (Virtual Machines, VM Scale Sets,
   Managed Disks, Container Registries, Container Apps, Container Instances, Cosmos DB
   Accounts, Cognitive Services, Virtual Networks, Network Security Groups, Load Balancers,
   Application Gateways, Public IP Addresses, App Service Plans), plus an **"Other
   Resources"** catch-all (`other_resources` key) for everything else real and meaningful
   (Databricks, Data Factory, Recovery Services Vaults, Event Hubs, API Management, bare SQL
   Servers, ...) that doesn't have its own section. Deliberately excluded ~20 ARM types that
   are pure Azure-generated plumbing/child objects, not something anyone browses
   (`_NOISE_ARM_TYPES` in `resource_service.py`: alert rules, VM extensions, TLS certs, DNS
   zone vnet-links, etc.) - and excluded `microsoft.containerservice/managedclusters`
   specifically because it's already shown via the dedicated AKS Clusters expander (richer
   AKS management-plane data), so it wouldn't appear twice. `dashboard/sidebar.py` refactored
   from ~7 copy-pasted expander blocks to one data-driven `_SIDEBAR_CATEGORIES` list.
9. **Application Insights resources always showed "Metrics unavailable".** Root cause:
   `_CANDIDATE_METRICS` in `providers/azure/monitor.py` only listed flat metric names
   (`"CpuPercentage"`, `"Requests"`, ...) meant for VMs/App Services/AKS. App Insights
   (`microsoft.insights/components`) namespaces its own metrics
   (`"performanceCounters/processorCpuPercentage"`, `"requests/rate"`,
   `"availabilityResults/availabilityPercentage"`, `"requests/duration"`, ...) - none of
   which ever matched, so every App Insights resource returned zero metrics regardless of
   whether it had real telemetry. Added the namespaced candidates. Verified live across all
   71 real Web App resources (100% matched + returned real data) and confirmed the fix
   surfaces real values on App Insights resources that actually have recent telemetry.
10. **Metrics window widened from 1 hour to 24 hours** (`_TIMESPAN`/`_INTERVAL` in
    `monitor.py`) - a random 1-hour slice was missing real data for lower-traffic resources
    purely by chance; `_latest_data_point` already takes the most recent non-null point
    regardless of window size, so this doesn't change what "current value" means for busy
    resources.
11. **Logs tab was silently broken for every resource, not just quiet ones.** The KQL query
    (`union isfuzzy=true withsource=SourceTable * | project TimeGenerated, ...`) failed with
    a real semantic error (`'project' operator: Failed to resolve scalar expression named
    'TimeGenerated'`) for **every single resource tested** (Web App, AKS, Key Vault, the Log
    Analytics workspace itself, App Insights) - `union isfuzzy=true` doesn't tolerate a
    completely empty union, and the exception was being swallowed and shown as "No logs
    available", indistinguishable from genuinely having no logs. Fixed with
    `column_ifexists()` (KQL's standard guard for a possibly-missing column) on every
    projected field. Verified: zero errors now across every resource type tested.

## Known external constraints (not code bugs - don't try to "fix" these again)

- **No resource in this subscription has Diagnostic Settings routing logs to Log Analytics.**
  Verified by sweeping all 771 discovered resources after the Logs fix above - none returned
  any rows. The query itself is correct now; there's simply nothing flowing into Log
  Analytics yet. Needs an Azure-side Diagnostic Settings configuration (per-resource or via
  Policy) before the Logs tab will ever show real data.
- **Azure Resource Health doesn't support many ARM types at all** (returns HTTP 422): private
  DNS zones, DNS zone vnet-links, smart-detector alert rules, backup restore points, and
  notably **Application Insights components** (`microsoft.insights/components`). This is an
  Azure API limitation, not something `providers/azure/alerts.py` can work around.
- **Cost Management's quota got driven into an extended cooldown** by heavy testing during
  this work session (far more requests than a normal single page load generates). If the
  Cost tab shows "unavailable" again, don't assume the code regressed - check whether
  something has been hammering it, and avoid rapid repeated reloads (each reload re-consumes
  the recovering quota).
- **A specific resource with "Application Insights enabled" showing "Metrics unavailable" is
  not automatically a bug.** "Enabled" is a setting; it doesn't guarantee telemetry is
  currently flowing. Before assuming a code issue, check the raw Azure Monitor response for
  that exact resource ID (definitions + values, see `AzureMonitorMetrics._available_metric_names`
  / `.get_metrics`) - if every data point is genuinely null/absent, that matches what the
  Azure Portal itself would show for the same window.

---

# Home Infrastructure Explorer — Work Log (2026-08-11)

Read this before starting new work on the Home Infrastructure Explorer (the graph on the
`Infrastructure Explorer` page). It explains what changed today, why, and what's still open.

## Where the code lives

- **Graph component (all the real logic):** `dashboard/components/g6_explorer/index.html`
  — a single vanilla-JS file (no build step) using AntV G6 v5, loaded as a Streamlit custom
  component. `g6.min.js` next to it is the vendored library.
- **Python wrapper:** `dashboard/components/g6_explorer/__init__.py` — defines the `g6_explorer()`
  function signature (props passed into the component: `resources`, `selected_id`, `reset_token`,
  etc.) and its return contract (clicked resource id / `"__refresh__:<ts>"` / `"__deselect__"` /
  `None`).
- **Page that hosts it:** `dashboard/pages/infrastructure_explorer.py` — handles the component's
  return value (selection, refresh, deselect), renders the "Quick Resource Summary" and the AI
  side panel next to the graph.
- Related but **not** touched today except for a small perf fix: `dashboard/components/cytoscape_topology/`
  (used by Resource Workspace's topology graph — separate component, separate code).

## What changed today, in order

1. **Layout cleanup**: removed Force/Grouped/Circular. Kept exactly 4 layouts — Hierarchical
   (left-to-right dagre), Tree (top-down dendrogram), Network, Radial.
2. **Radial and Network are custom, not G6-native.** G6 v5.1.1's built-in `radial` and `force`
   layouts were tested live and don't hold up at this node count/shape (radial collapsed a flat
   sibling set into a narrow wedge instead of a ring; force left distinct nodes stacked exactly on
   top of each other for a hub-and-spokes graph). Both are now hand-written: see
   `computeRadialPositions`/`applyRadialPositions` and `computeNetworkPositions`/
   `applyNetworkPositions` in index.html. They compute positions once and paint them via
   `setData()+draw()`, bypassing G6's layout pipeline entirely.
3. **Fixed a real G6 rendering bug**: the graph was constructed with a static
   `node: { type: "rect" }` default, which silently overrode every node's own `type` — this is why
   Network's circles were never actually rendering as circles. Removed that default; each node's
   own `type` field now controls its shape correctly.
4. **Click-to-expand is now fully local/incremental** (see `toggleExpand`, `placeNewNodesAround`,
   `resolveAgainstExistingPositions`). Expanding a Resource Group/Type no longer re-runs the whole
   layout — it fans new children out in rings around their own parent's current position and
   never moves anything else. This is what fixed the "everything jumps into a collided blob at
   the center" bug. The wide-rank compaction used by the *initial* automatic layout
   (`compactifyWideRanks`) was also rewritten to lay sibling groups out sequentially so two
   different parents' children can never overlap (previous per-parent-anchored version could still
   collide when many parents were packed close together).
5. **Resource selection highlighting**: clicking a resource highlights the full ancestor-chain
   path (Subscription→RG→Type→Resource — the only "relationship" this pure containment tree has,
   there's no separate dependency graph here) with a bright red, glowing, 4px edge; dims every
   unrelated edge and node; keeps type-identity colors/icons unchanged (only opacity changes).
   See `highlightedEdgeIdsFor`, `highlightedAncestorNodeIdsFor`, `edgeStyleFor` in index.html.
   Selecting/deselecting never touches node positions or re-layouts (verified: zoom/positions are
   byte-identical before/after selecting).
6. **Deselect on empty-canvas click**: `canvas:click` sends a `"__deselect__"` sentinel, handled
   in `infrastructure_explorer.py` (guarded so it can't loop).
7. **Refresh button was actually broken (infinite loop)**: Streamlit custom components keep
   returning the same value on every rerun until JS sends a new one. The old code sent a fixed
   `"__refresh__"` string, so after one click every subsequent rerun re-triggered
   `azure_provider.refresh()` + `st.rerun()` forever — this is what made Refresh look
   broken/slow. Fixed by sending a unique `"__refresh__:" + Date.now()` token per click and
   guarding on it in Python (`_last_refresh_token` in session_state).
8. **Refresh now also resets the view** ("original setup"): clears the selected resource and bumps
   a `reset_token` prop that tells the component to discard expand/collapse state and any
   manually-dragged positions, going back to the pristine initial view — not just silently
   re-fetching data underneath whatever was expanded/dragged.
9. **Perf fixes for "page unresponsive"**:
   - Debounced the search input in g6_explorer (was relayouting on every keystroke) and in
     cytoscape_topology (same bug, `applyCombinedFilter`).
   - `dashboard/sidebar.py` (rendered globally on almost every page) was calling
     `resource_service.get_azure_resources()`, which included an **uncached live Azure ARM call**
     (`managed_clusters.list()`) on every single Streamlit rerun. Added caching to
     `MockAKSProvider.get_clusters()` (`providers/aks_provider.py`) and
     `MockGitLabProvider.get_projects()` (`providers/gitlab_provider.py`) — same pattern
     `MockAzureProvider.get_all_resources()` already used.
10. **Removed the left "Azure Subscription Explorer" sidebar from AKS Workspace and GitLab
    pages** — `app.py`'s `NO_SIDEBAR_PAGES` tuple now also excludes `AKS_WORKSPACE`/`GITLAB` (they
    have their own dedicated picker).
11. **Removed the Minimap entirely** — button, container div, CSS, G6 plugin registration, and the
    toggle handler are all gone. No leftover empty space where it used to sit.
12. **Brightened resource node fill** — was a flat near-black (`#171b24`, barely different from
    the page background), only the thin border/icon carried color. Now tinted with the resource's
    own type color at low alpha (`hexToRgba(meta.color, 0.28-0.32)`) so the node body itself reads
    as colorful, not just a dark circle with a colored ring.

## How this was tested

No live Azure/GitLab connection is available in the dev sandbox, so testing was done by loading
`index.html` directly in a headless Chromium (Playwright) with a synthetic ~820-resource dataset
(42 Resource Groups × several types/resources each, matching real Azure ARM type strings), driven
via the same `postMessage({type:"streamlit:render", args:{...}})` protocol Streamlit uses. This
caught several real bugs (the `node:{type:"rect"}` override, the degenerate force-layout
symmetry, a `graph.draw()` Promise not being awaited before `fitView`, the compaction overlap
bug) that a purely-read-the-code review would have missed. The test scripts themselves live in a
session-scoped scratch directory and were not saved — if you need to re-verify this component,
write a similar throwaway harness: open `index.html` via `page.goto(file_uri)`, feed it a resource
list via the postMessage shim above, then drive the toolbar/graph via `page.evaluate()` calls into
the global JS functions (most of them, like `toggleExpand`, `computeVisibleG6Data`, `graph`,
`tree`, `nodePositions`, are plain top-level `var`/`function` declarations, so they're reachable
as `window.<name>` from Playwright).

## Open item — needs your confirmation

The user reported (with a screenshot) that resource-click highlighting wasn't showing at all —
plain thin gray edges, no red glow. Everything above was re-verified working correctly via the
headless test harness immediately after that report, so this is almost certainly the **user's
browser/Streamlit server running stale code**, not a real bug:

- Streamlit custom components load their JS into an iframe once; editing the file on disk does
  not make an already-open browser tab reload it.
- Told the user to fully restart the Streamlit server (`Ctrl+C` then `streamlit run app.py`
  again) **and** hard-refresh the browser (Ctrl+F5), then retest.
- **Have not yet gotten confirmation back that this resolved it.** If they report it's still
  broken after a real server restart + hard refresh, that would mean there's an actual bug I
  haven't found yet — dig in fresh rather than assuming it's caching again.

## Things intentionally left alone (don't "fix" without being asked)

- Resource Workspace's own topology graph (`cytoscape_topology`) — separate component, only
  touched for the search-debounce perf fix.
- AI investigation / LangGraph / CrewAI pipeline — untouched all day.
- Azure/GitLab data-fetching logic itself — only caching was added around existing calls, no
  query/projection logic changed.
- `dashboard/aks.py`'s resource-ID matching bug (fixed earlier, uses `resource_ids_match` now) —
  unrelated to anything above, just noting it's already done.
