# Changes: Streamlit Community Cloud Deployment Prep

Local git repo initialized and committed. No application logic changed, no Azure resources created, nothing pushed anywhere.

## Files added

- **`.gitignore`** - excludes `venv/`, `.env`, `__pycache__/`, `GeneratedDocs/`, stray `*.tmp.*` editor artifacts already present in `dashboard/components/g6_explorer/`, and `.claude/`.
- **`.streamlit/secrets.toml.example`** - documents every required secret key (placeholders only, safe to commit).

## Files modified

- **`config.py`** - added a small guarded block that loads `st.secrets` into `os.environ` before `load_dotenv()`, so secrets injected by Streamlit Community Cloud's Secrets manager reach the same `os.getenv()` calls the app already uses. Local `.env`-based dev is unaffected (verified).

## Validated locally

1. **Dev mode** (real `.env`): all 8 pages return HTTP 200, no errors.
2. **Production-like mode** (isolated copy, `.env` removed, only a dummy `.streamlit/secrets.toml` present): all 8 pages return HTTP 200, no exceptions, and `Config` values were confirmed to load correctly from `secrets.toml`.

## Reported, not fixed

The 5 CrewAI domain-investigation agents (`agents/crew/*`) are hard-wired to a local Ollama endpoint (`agents/crew/base.py:_build_llm()`) and will report `status="error"` on any host without a reachable Ollama server. The app does not crash - the Claude Sonnet final synthesis (the AI Copilot's actual answer) works normally either way, since it consumes raw evidence directly rather than depending on the CrewAI domain reports.

## What's left (manual steps)

1. Review `.env.example` yourself before pushing (not read-accessible to me).
2. Create a GitHub repo and push:
   ```
   git remote add origin <your-repo-url>
   git push -u origin master
   ```
3. On https://share.streamlit.io: New app -> select repo/branch `master`/main file `app.py`.
4. Advanced settings -> Python 3.12.
5. Settings -> Secrets -> paste `.streamlit/secrets.toml.example` with real values (leave `OLLAMA_BASE_URL`/`OLLAMA_MODEL` unset unless you have a reachable Ollama endpoint).
6. Deploy.

---

# GitLab Workspace improvements (2026-08-16)

Implemented on top of the existing (already read-only) GitLab integration - no new GitLab API logic, only reused existing client/provider methods.

## Files modified

- **`services/resource_service.py`** - added `investigate_pipeline()`/`investigate_git_changes()` wrappers (previously dead code at the provider layer); added caching (`_cached()`) to `get_project_merge_requests()`, `get_project_recent_commits()`, `get_commit_diff()`.
- **`dashboard/gitlab.py`** - added an in-memory project search box (no re-fetch), a Commits tab (list + diff-on-select), a Merge Requests tab, and an "Advanced Root Cause Analysis (AI)" section on the Investigation tab that combines `investigate_pipeline()` + `investigate_git_changes()` + the existing failure report and feeds it into the existing `ClaudeSynthesizer`.
- **`dashboard/pages/gitlab_workspace.py`** - wired in the two new tabs.

## Tested

Real GitLab/Anthropic credentials aren't configured locally, so tested via `streamlit.testing.v1.AppTest` (Streamlit's official headless harness) with a fake GitLab client swapped in only at the network boundary. All checks passed: search doesn't re-fetch projects, commit selection fetches diff without re-fetching the list, MR tab renders correctly, basic + advanced RCA both run and render, no-op reruns refetch nothing, other pages still import cleanly. Read-only throughout, no tokens created.

---

# AKS Workspace audit (2026-08-16, read-only - **start here tomorrow**)

Same treatment as the GitLab audit above, but AKS was **only audited, not modified**. Full findings below are the todo list for next session.

## 1. Current features
- Live AKS cluster discovery + ARM metadata (version, node pools, provisioning state, FQDN) via `ContainerServiceClient`
- Live namespaces, nodes, deployments, ReplicaSets, pods, services, ingress, events via the Kubernetes python client (all-namespaces or one selected namespace)
- Read-only pod log tailing (200 lines) + per-pod events, button-gated
- ConfigMap/Secret metadata collection - docgen only, no dashboard tab
- Client-side health rollups (unhealthy node/deployment/pod warnings) + ARM-level health/alerts via the shared subscription health overview
- Cluster-level search via the general resource search
- Private-cluster detection (FQDN-based `privatelink` check) + a bounded, classified 12s timeout for every K8s call **from the dashboard**
- Keyword-routed chatbot investigation (no dedicated "Run Investigation" button, unlike GitLab)

## 2. Working / Partial / Broken / Missing

| Feature | Status |
|---|---|
| Cluster selection/details | Working |
| Nodes/node pools | Working |
| Namespaces | Working |
| Workloads/Deployments + ReplicaSets | Working |
| Pods | Working |
| Services/Ingress | Working |
| Events | Working |
| Pod logs | Working |
| Health/status | Working |
| Search/filtering | Partial - cluster-level only; no pod/deployment/service name search within a cluster |
| AKS AI investigation/chatbot | Partial - works, but no dashboard entry point, and fragile against unreachable clusters (see below) |

## 3. Main problems (ranked)
1. **Chatbot path has no timeout on K8s calls** - `agents/aks_agent.py` calls `MockAKSProvider` methods directly, bypassing `call_with_timeout` entirely. A private/unreachable cluster (the expected case on Streamlit Community Cloud) can stall an entire chat turn indefinitely instead of failing in 12s like the dashboard does.
2. **Docgen crashes instead of degrading** on an unreachable cluster - `workflow/docgen_graph.py`'s collect node has no exception handling around `_collect_aks`, so `AKSUnreachableError` (correctly raised after 12s) propagates uncaught and kills the whole doc-generation run, contradicting that module's own "fails closed" design intent.
3. **No caching on any live K8s data** - namespaces/nodes/pods/deployments/services/ingress/events/logs are re-fetched on every Streamlit rerun (unlike Azure Monitor/Cost data, which is memoized per resource).
4. **No dedicated "Run AKS Investigation" UI** - AI-assisted root-cause analysis only reachable by phrasing a chatbot question with the right keywords; no equivalent of GitLab's Investigation tab.
5. **CrewAI AKS domain agent depends on a local Ollama endpoint** that won't exist on Streamlit Cloud, so it degrades to `status="error"` in production - mitigated since Claude's final synthesis consumes raw evidence directly regardless.
6. **Six dead provider/service methods**: `MockAKSProvider.get_namespace/get_deployment/get_pod/get_deployment_pods/get_service/get_ingress_resource`, `ResourceService.get_namespace_deployments/get_namespace_pods` - no functional impact, maintenance noise.
7. **`AKS_CLUSTER_NAME`/`AKS_RESOURCE_GROUP` config values unused** - cluster identity comes entirely from live Azure-wide discovery, not config.

## 4. Recommended changes (prioritized) - pick up here
1. Wrap every K8s call in `agents/aks_agent.py` with `utils/k8s_safety.call_with_timeout` - highest impact, directly addresses the private-cluster-on-Streamlit-Cloud scenario this app is being deployed into.
2. Catch `AKSUnreachableError` in `workflow/docgen_graph.py`'s collect node and degrade gracefully instead of crashing the graph.
3. Add short-lived caching/memoization for `get_cluster_namespaces/nodes/deployments/pods/services/ingress/events` in `ResourceService`, same pattern as the existing `_cached()` helper.
4. Add a "Run AKS Investigation" button on the AKS Workspace page, matching the GitLab Investigation tab's UX.
5. Remove or wire up the six dead methods and the two unused config values to reduce surface-area confusion.

### Architecture reference (for tomorrow)
```
dashboard/pages/aks_workspace.py -> dashboard/aks.py (render_* tabs)
  -> services/resource_service.py (self.aks_provider)
  -> utils/k8s_safety.call_with_timeout (12s bounded, classified errors)
  -> providers/aks_provider.py:MockAKSProvider
  -> providers/azure/aks.py:AzureAKS -> ContainerServiceClient (ARM) + kubernetes client (cluster API)

Separately: agents/aks_agent.py -> orchestrator ("aks" capability) -> CrewAI aks_investigation_agent
  (local Ollama) -> synthesis/claude_synthesizer.py (Claude Sonnet)
```

---

# AKS Run Command fallback for private clusters (2026-08-19)

Implemented the item flagged above ("private-cluster detection... but no fallback data path").
Zero Azure resources/config/networking/RBAC changed - verified live against the real
`mplexaksdev` cluster (MetroPlexCMS). All Kubernetes operations remain read-only.

## Files changed

- **`providers/azure/aks_run_command.py`** (new) - `AKSRunCommandClient` (direct SDK equivalent
  of `az aks command invoke`, via `ContainerServiceClient.managed_clusters.begin_run_command` /
  `get_command_result`), plus JSON parsers that turn one batched `kubectl get ... -o json`
  Run Command response into the same dict shapes the direct Kubernetes-client path already
  produces, and a targeted `kubectl logs` helper for on-demand pod logs.
- **`providers/azure/aks.py`** - every namespace/node/deployment/replicaset/pod/service/ingress/
  event/pod-log/pod-events method now tries the direct Kubernetes API first, then falls back to
  AKS Run Command on failure (`_fetch_k8s`/`_use_run_command`/`_run_command_bulk`). The chosen
  path is remembered per cluster so a known-private cluster skips the direct attempt entirely on
  later calls. `_to_cluster_dict` now also carries `enable_private_cluster` (from
  `apiServerAccessProfile.enablePrivateCluster`) and `private_fqdn`. The ARM discovery path
  itself is untouched.
- **`utils/k8s_safety.py`** - fixed `is_private_cluster()`, which only checked `fqdn` for
  `"privatelink"` and **misclassified `mplexaksdev` as not private** (it has
  `enablePrivateClusterPublicFQDN` set, so ARM gives it a public-looking `fqdn` too - the real
  private endpoint is only in the separate `private_fqdn` field). Now prefers the authoritative
  `enable_private_cluster` ARM field. Added `assert_read_only_kubectl()` - the single choke
  point every command passes through before reaching Azure, allow-listing only
  `get/describe/logs/top/version/cluster-info/explain/api-resources/api-versions`, blocking every
  mutating verb (`apply/delete/create/patch/edit/scale/rollout/exec/...`) and shell
  metacharacters. Added `call_with_timeout(..., timeout=...)` override and
  `AKS_RUN_COMMAND_AWARE_TIMEOUT_SECONDS` (120s) for calls that may fall back to Run Command,
  without changing the existing 12s default used everywhere else.
- **`services/resource_service.py`** / **`agents/aks_agent.py`** - the AKS get_cluster_*/
  pod-log/pod-events calls now pass the larger Run-Command-aware timeout instead of the plain
  12s default (which would otherwise cut off a valid, still-running Run Command call); also
  added the missing `_cached()` wrapper to `get_cluster_replicasets` for consistency with its
  siblings.
- **`dashboard/pages/aks_workspace.py`** - removed the early bail-out that showed a static
  warning and skipped straight to the Investigation tab for every private cluster without even
  trying to fetch data. Private clusters now go through the normal fetch path (which
  transparently uses Run Command) and only show a warning if that *also* fails, with three new,
  specific messages (`run_command_forbidden`, `run_command_failed`, `blocked_command`) alongside
  the existing ones.

`providers/aks_provider.py` (`MockAKSProvider`) needed **zero changes** - it already just
delegates to `AzureAKS`, so the fallback is fully transparent to it and to the dashboard/agent
call sites above it.

## Azure SDK / API used for Run Command

`azure-mgmt-containerservice` 41.5.0 (already a pinned dependency, `>=20.0.0`), reusing the
existing `AzureAuth`/`ClientSecretCredential` - no new auth path:

```python
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.containerservice.models import RunCommandRequest

poller = client.managed_clusters.begin_run_command(
    resource_group, cluster_name, RunCommandRequest(command="kubectl get ... -o json"),
)
result = poller.result(timeout=90)          # CommandResultProperties: exit_code, logs, ...
```

This is the exact SDK equivalent of `az aks command invoke --resource-group ... --name ...
--command "..."` - same ARM operation
(`Microsoft.ContainerService/managedClusters/runCommand/action`), same AKS-managed
`aks-command` pod lifecycle, no Azure CLI subprocess.

## kubectl commands executed

- **Bulk list fetch** (one call covers 8 of 9 supported views):
  `kubectl get namespaces,nodes,deployments.apps,replicasets.apps,pods,services,ingresses.networking.k8s.io,events --all-namespaces -o json`
- **Pod logs** (only on explicit "Fetch Latest Logs" click):
  `kubectl logs <pod> -n <namespace> [-c <container>] --tail=200 --timestamps=true`

Every command is validated by `assert_read_only_kubectl()` before being sent; nothing else ever
reaches Run Command, and no free-form/user-supplied command string is accepted anywhere in this
path.

## Run Command calls per Workspace load / latency / caching

- **1 Run Command call** for the entire page load (Namespaces, Nodes, Deployments, ReplicaSets,
  Pods, Services, Ingress, Events all parsed from that one response), **+1 more** only if/when
  the user clicks "Fetch Latest Logs" for a specific pod. Verified with a mocked Run Command
  response: 8 `get_*()` calls (including a namespace-filtered one and `get_pod_events`) against
  a fresh `AzureAKS` instance produced exactly 1 call to `AKSRunCommandClient.run`; fetching pod
  logs afterward added exactly 1 more.
- A known-private cluster skips the direct-connection attempt entirely on every call after the
  first (`_reachability` cache) - no repeated 12s waits.
- `ResourceService._cached()` still memoizes each `get_cluster_*` result per cluster(+namespace)
  for the life of the session on top of that, so tab switches/reruns never re-issue a Run
  Command call at all once the page has loaded successfully.
- A failed attempt (RBAC, timeout, bad output) is never cached at either layer - confirmed live:
  three separate calls to `get_namespaces`/`get_nodes`/`get_pods` against the RBAC-blocked real
  cluster produced three separate Run Command attempts, not one cached failure replayed.

## Test results (live, against the real `mplexaksdev` cluster)

- **Private-cluster detection fixed**: `is_private_cluster()` now correctly returns `True` for
  `mplexaksdev` (previously `False`, due to the `fqdn`-only bug above).
- **Direct connection genuinely fails** for this cluster: DNS resolution failure on the private
  FQDN, ~5.5s.
- **Run Command SDK path is live and reachable**: `begin_run_command` successfully submits to
  Azure and gets a definitive response - but the app's configured service-principal credential
  is **not authorized** for `Microsoft.ContainerService/managedClusters/runCommand/action` on
  this cluster (`403 AuthorizationFailed`, confirmed with a direct SDK call, no CLI). This is an
  RBAC gap on the existing identity, not a code defect - per your instruction, this was not
  changed. The `az aks command invoke` capability you verified earlier evidently ran under a
  different, more-privileged identity (interactive `az login` or a different SP) than this app's
  `AzureAuth` service principal.
- **End-to-end UI behavior verified with Streamlit's `AppTest` harness** against the real,
  RBAC-blocked cluster: no traceback; ARM metadata (version, node count, provisioning state,
  FQDN, node pools) renders normally; a new info banner explains the private-cluster/Run-Command
  path; the warning shown is the new specific `run_command_forbidden` message (naming the exact
  missing permission and a role that grants it); the Investigation tab remains available and
  clickable.
- **Read-only guard verified**: 15/15 cases (`get`/`describe`/`logs` allowed;
  `delete`/`apply`/`exec`/`patch`/`edit`/`scale`/`rollout restart`/`create`/`helm install`,
  shell chaining `;`/`&&`, and command substitution `$(...)` all rejected before reaching Azure).
- **Reachable-cluster path unchanged, verified**: with direct access mocked to succeed, Run
  Command is never called and the result is returned exactly as before.
- **Batched parsing verified**: mocked Run Command JSON output parsed into namespaces, nodes,
  deployments (incl. namespace filtering), replicasets, pods, services, ingresses, and events,
  each matching the exact dict shape the direct Kubernetes-client path already produced.
- Every other page (Resource Workspace, AI Copilot, Monitoring, FinOps, GitLab Workspace,
  Infrastructure Explorer, Settings) and `docgen`/`workflow` still import and run cleanly -
  unrelated areas untouched.

## Not yet confirmed (blocked on RBAC, not code)

A genuine end-to-end success (Run Command actually returning live namespace/pod/etc. data) could
not be demonstrated, because the app's service principal lacks the `runCommand/action`
permission on the tested cluster and granting/changing RBAC was explicitly out of scope for me.
Once that permission is granted to the app's identity (e.g. the built-in "Azure Kubernetes
Service Cluster User Role", which includes `runCommand/action`), the exact same code path
should return live data - the failure mode observed is specifically `403 AuthorizationFailed`,
not a timeout, DNS, or SDK/parsing error.

## Confirmed: no Azure resources, configuration, networking, or RBAC changed

No AKS resource, cluster setting, VNet/subnet/private DNS/firewall rule, or IAM role assignment
was created, deleted, or modified. `az`/CLI was never invoked from app code - only the
`azure-mgmt-containerservice` Python SDK, through the existing `AzureAuth` credential. Every
Kubernetes operation used or added is read-only. AKS's own `aks-command` pod lifecycle (created
and torn down by Azure for each Run Command invocation) was never touched directly - only the
existing, already-enabled Run Command mechanism was invoked through its SDK, exactly as
`az aks command invoke` already was in your own prior test. No new billable resource or Azure
service was introduced.

---

# AKS Run Command: RBAC fix, 512 KiB limit, latency, lazy loading (2026-08-19/20)

Continuation of the section above, same day/next session - **start here tomorrow**. Everything
below is implemented, tested live against `mplexaksdev` and `mplexaks`, and confirmed zero
Azure/AKS/Kubernetes changes throughout (only `kubectl get`/`kubectl logs`, no RBAC/network/
resource changes made by me at any point).

## 1. RBAC was granted externally, unblocking real data

You created a custom role ("AIOps AKS Run Command Reader": `managedClusters/read`,
`managedClusters/runcommand/action`, `managedClusters/commandResults/read`) and assigned it to
the NightbeatX service principal on both clusters (role definition saved as
`aiops-aks-runcommand-role.json` in repo root - untracked, not written by me). Confirmed live:
the previous `403 AuthorizationFailed` is gone; Run Command genuinely executes.

## 2. New problem found and fixed: the 512 KiB Run Command output cap

The original single-8-kind-batch design (from the section above) reliably truncated on real
data. Root-caused and fixed in **`providers/azure/aks_run_command.py`** and
**`providers/azure/aks.py`**:
- Every kind is now fetched with its own Run Command call, namespace-scoped (`-n <namespace>`)
  when the caller has one - never combined with other kinds (combining even 2-3 small kinds was
  proven unsafe: fit on the 21-namespace `mplexaksdev`, truncated on the 75-namespace `mplexaks`).
- `--show-managed-fields=false` on every full-JSON call (real kubectl flag, zero information
  loss, stays inside the read-only allow-list unchanged).
- Explicit truncation detection (`_looks_truncated`/`_parse_list_or_raise`, `AKSRunCommandTruncatedError`):
  output at/past the exact 512 KiB Azure cap, or anything that fails `json.loads`, is **never**
  parsed or trusted as partial data.
- `nodes` (cluster-scoped, can't be split by namespace) falls back to a reduced-field
  `-o custom-columns` query when full JSON truncates - verified on `mplexaks`'s 28 nodes: 524 KB+
  (truncated) -> ~14 KB, same fields the dashboard reads.
- New, specific `AKSUnreachableError` reasons surfaced to the UI: `scope_too_broad` (an
  all-namespaces fetch for this kind is too large - pick a namespace) and `namespace_too_large`
  (even the selected namespace is too large for this kind - genuinely hit on `mplexaks`'s busy
  `kube-system` pods).
- `providers/aks_provider.py`/`services/resource_service.py` needed no interface changes - same
  `get_cluster_*(cluster_id, namespace)` signatures throughout.

## 3. Latency: concurrency, then true lazy loading

Two iterations, in `dashboard/pages/aks_workspace.py` / `dashboard/aks.py`:

1. **Bounded concurrency** (`dashboard.aks.run_concurrent_fetches`, `ThreadPoolExecutor(max_workers=3)`):
   fetch namespaces+nodes concurrently, then deployments/replicasets/pods/services/ingress/events
   concurrently. Cut a full "All namespaces" page load from **305.5s -> 128.3s** (mplexaksdev) /
   **162.3s** (mplexaks). Added a `threading.Lock` around `AKSRunCommandStats` in
   `aks_run_command.py` since concurrent calls genuinely race on that counter.
2. **True lazy loading** (superseded the "fetch everything up front" approach): `st.tabs()`
   replaced with `st.radio("View", ...)`, because switching `st.tabs()` never reruns the script
   (every tab's fetch already ran before any tab was clickable) while a radio's value change
   does. Initial load now does exactly ARM metadata + one `get_cluster_namespaces()` call (~31s
   of real Run Command time; ~56-58s wall including cold ARM/credential overhead in a fresh
   process - a warm session should sit closer to the ~31s floor). Every other kind is fetched
   only inside the `if/elif` branch matching the currently-selected view. `run_concurrent_fetches`
   is still defined/tested but no longer called here - each view now needs only one fetch, not a
   batch. Verified: 7 distinct views = exactly 7 Run Command calls total (not 7 per visit);
   switching back to an already-viewed kind is a **0.0s** cache hit; Pod Logs view reuses the
   Pods view's cached pod list (no duplicate call) and still gates actual log content behind the
   explicit "Fetch Latest Logs" button (`tail=200` unchanged).

## 4. UI cleanup (2026-08-20)

Removed the Node Pools table and the blue private-cluster `st.info` description box from the
AKS Workspace cluster overview (`dashboard/aks.py`, `dashboard/pages/aks_workspace.py`) - purely
cosmetic, verified live that Kubernetes Version/Node Count/Provisioning State/Resource
Group/API Server FQDN/Node Resource Group all still render, zero exceptions.

## Files touched across this whole continuation

- `providers/azure/aks_run_command.py` - per-kind fetch, truncation detection, node fallback,
  stats lock (new file, written in the earlier section, substantially reworked in this one)
- `providers/azure/aks.py` - per-(cluster,kind,namespace) cache/dispatch (reworked)
- `utils/k8s_safety.py` - `is_private_cluster` ARM-field fix, `assert_read_only_kubectl`,
  `AKS_RUN_COMMAND_AWARE_TIMEOUT_SECONDS`, `call_with_timeout(..., timeout=...)` (from the
  earlier section, unchanged in this continuation)
- `services/resource_service.py`, `agents/aks_agent.py` - Run-Command-aware timeouts (earlier
  section, unchanged here)
- `dashboard/pages/aks_workspace.py` - tabs -> radio, lazy per-view fetch, error messages, UI
  cleanup
- `dashboard/aks.py` - render functions take pre-fetched data, `run_concurrent_fetches` helper,
  Node Pools/info-box removal

## What's left / where to pick up tomorrow

- Nothing is broken or half-finished; the app is in a working, tested state on both clusters.
- Not yet re-verified after the two most recent small UI edits: a full click-through in an
  actual running `streamlit run app.py` session (everything so far was verified via
  `streamlit.testing.v1.AppTest` against the real Azure clusters, not a live browser).
  `aiops-aks-runcommand-role.json` is untracked in the repo root - worth deciding whether to
  commit it, move it under a docs/ folder, or `.gitignore` it, since it documents the custom
  RBAC role.
- Possible future tuning (not done, discussed but deliberately not applied without your
  go-ahead): raising `max_workers` above 3 for `run_concurrent_fetches` if a "load everything at
  once" mode is ever wanted again.
- Nothing currently pending from you; all requested tasks this session were completed and
  tested.
