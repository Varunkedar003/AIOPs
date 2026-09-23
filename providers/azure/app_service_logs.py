"""Reads an App Service's own container stdout/stderr log file directly via Kudu's VFS
API - read-only (GET only, never writes/deletes anything).

Why this instead of Log Analytics (see log_analytics.py): AzureLogAnalytics is fully
functional, but this environment has no Log Analytics workspace at all - confirmed live,
every App Service returns zero rows regardless of query/window. This reads the same
underlying log data the standalone production-logs/App-Testing viewers and `az webapp
log tail` ultimately read, with zero extra Azure infrastructure required.

Auth: reuses the app's existing ClientSecretCredential (AAD Bearer token, ARM scope) -
Kudu/SCM accepts AAD auth directly, no separate publish-profile/Basic-auth credential
is introduced or handled here.
"""
import logging
from typing import Any, Dict, List, Optional

import requests

from .auth import AzureAuth

logger = logging.getLogger(__name__)

_ARM_SCOPE = "https://management.azure.com/.default"
_TAIL_BYTES = 300_000  # bounded tail fetch - never downloads a whole (potentially huge) log file
_REQUEST_TIMEOUT = 15


class AzureAppServiceLogs:
    """Tails an App Service's container log file via Kudu VFS."""

    def __init__(self, azure_auth: Optional[AzureAuth] = None):
        self.azure_auth = azure_auth or AzureAuth()
        self.last_error: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        token = self.azure_auth.get_credential().get_token(_ARM_SCOPE).token
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _scm_host(default_host_name: str) -> str:
        """The app's own SCM/Kudu hostname, derived from its real `defaultHostName`
        (already available on every discovered App Service resource) - NOT guessed from
        the display name, which can differ from the site's actual hostname when Azure
        auto-generates a unique suffix (observed: display name "App-Testing" vs actual
        site "app-testing-d0abh6hub3cpg8h5").

        `.scm.` goes right after the site name (first label), not before
        `.azurewebsites.net` - a region-suffixed hostname like
        "app-testing-d0abh6hub3cpg8h5.centralindia-01.azurewebsites.net" becomes
        "app-testing-d0abh6hub3cpg8h5.scm.centralindia-01.azurewebsites.net", confirmed
        against this environment's actual apps (a plain-before-.azurewebsites.net
        replace was tried first and does NOT resolve for these region-suffixed hosts).
        """
        site_name, _, rest = default_host_name.partition(".")
        return f"{site_name}.scm.{rest}" if rest else default_host_name

    def get_recent_lines(self, default_host_name: str, max_lines: int = 500) -> List[str]:
        """Fetch the most recent lines from the app's own container log file.

        Filenames ending `_scm_docker.log` are Kudu's own build/deploy log (irrelevant
        here) and are always excluded - only the app's real `_docker.log` (its container
        stdout/stderr) is read. Returns [] if the app has no docker log file yet (e.g.
        never deployed as a container, or too new to have written one) or the request
        fails; check `last_error` to tell the two apart.
        """
        if not default_host_name:
            return []

        scm_host = self._scm_host(default_host_name)

        try:
            list_resp = requests.get(
                f"https://{scm_host}/api/vfs/LogFiles/", headers=self._headers(), timeout=_REQUEST_TIMEOUT
            )
            list_resp.raise_for_status()
            files = list_resp.json()
        except Exception as exc:
            logger.error("Kudu VFS log listing failed for %s: %s", scm_host, exc)
            self.last_error = str(exc)
            return []

        docker_logs = [
            f for f in files
            if f.get("name", "").endswith("_docker.log") and "_scm_" not in f.get("name", "")
        ]
        if not docker_logs:
            self.last_error = None
            return []
        docker_logs.sort(key=lambda f: f.get("mtime", ""), reverse=True)
        latest = docker_logs[0]

        try:
            tail_resp = requests.get(
                latest["href"],
                headers={**self._headers(), "Range": f"bytes=-{_TAIL_BYTES}"},
                timeout=_REQUEST_TIMEOUT,
            )
            tail_resp.raise_for_status()
        except Exception as exc:
            logger.error("Kudu VFS log tail fetch failed for %s: %s", scm_host, exc)
            self.last_error = str(exc)
            return []

        self.last_error = None
        lines = tail_resp.text.splitlines()
        # A range request can start mid-line when _TAIL_BYTES lands inside one - drop
        # that first partial line rather than show a truncated fragment as if it were
        # a real, complete log entry.
        if lines and tail_resp.status_code == 206 and len(tail_resp.content) >= _TAIL_BYTES:
            lines = lines[1:]
        return lines[-max_lines:]
