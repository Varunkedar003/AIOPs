"""Azure Log Analytics log retrieval for a single resource.

Uses `LogsQueryClient.query_resource`, which runs a KQL query scoped directly
to an ARM resource ID (rather than requiring a Log Analytics workspace ID),
authenticated with the same service-principal credential used everywhere
else in the app. Diagnostic logs land in different tables depending on
resource type (AzureDiagnostics, AzureActivity, resource-specific tables,
...), so a fuzzy `union` across all tables is used - mirroring monitor.py's
"only report what's actually available" approach - and each row's severity
and message are read from whichever of several candidate column names that
table happens to populate.
"""
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from azure.monitor.query import LogsQueryClient, LogsQueryStatus

from .auth import AzureAuth
from utils.timing import log_timing

logger = logging.getLogger(__name__)

_TIMESPAN = timedelta(hours=24)
_MAX_ROWS = 200

# Candidate column names (most preferred first) for each display field, since
# different tables use different naming conventions for the same concept.
_SEVERITY_COLUMNS = ["Level", "SeverityLevel", "Severity"]
_MESSAGE_COLUMNS = ["Message", "RenderedDescription", "OperationName", "ResultDescription"]

_QUERY = """
union isfuzzy=true withsource=SourceTable *
| project
    TimeGenerated=column_ifexists("TimeGenerated", datetime(null)),
    SourceTable,
    Level=column_ifexists("Level", ""),
    SeverityLevel=column_ifexists("SeverityLevel", ""),
    Severity=column_ifexists("Severity", ""),
    Message=column_ifexists("Message", ""),
    RenderedDescription=column_ifexists("RenderedDescription", ""),
    OperationName=column_ifexists("OperationName", ""),
    ResultDescription=column_ifexists("ResultDescription", "")
| where isnotempty(TimeGenerated)
| order by TimeGenerated desc
| take {limit}
""".strip()


class AzureLogAnalytics:
    """Fetches live Log Analytics logs scoped to a single Azure resource."""

    def __init__(self, azure_auth: Optional[AzureAuth] = None):
        self.azure_auth = azure_auth or AzureAuth()
        self._client: Optional[LogsQueryClient] = None
        self.last_error: Optional[str] = None

    def _get_client(self) -> LogsQueryClient:
        if self._client is None:
            credential = self.azure_auth.get_credential()
            self._client = LogsQueryClient(credential)
        return self._client

    def get_logs(self, resource_id: str, limit: int = _MAX_ROWS) -> List[Dict[str, Any]]:
        """Fetch the most recent log entries for a resource, newest first.

        Returns an empty list if the resource has no diagnostic logs flowing into Log
        Analytics (i.e. "No logs available"), or if the query itself fails.
        """
        if not resource_id:
            return []

        try:
            client = self._get_client()
            with log_timing(logger, "AzureLogAnalytics.get_logs"):
                response = client.query_resource(
                    resource_id, _QUERY.format(limit=limit), timespan=_TIMESPAN
                )
        except Exception as exc:
            logger.error("Log Analytics query failed for %s: %s", resource_id, exc)
            self.last_error = str(exc)
            return []

        if response.status == LogsQueryStatus.SUCCESS:
            tables = response.tables
        elif response.status == LogsQueryStatus.PARTIAL:
            logger.warning(
                "Log Analytics query partially failed for %s: %s", resource_id, response.partial_error
            )
            tables = response.partial_data
        else:
            self.last_error = "Log Analytics query failed"
            return []

        self.last_error = None
        if not tables or not tables[0].rows:
            return []

        return [self._to_log_entry(row) for row in tables[0].rows]

    @staticmethod
    def _first_present(row: Any, candidates: List[str]) -> str:
        for name in candidates:
            try:
                value = row[name]
            except (KeyError, IndexError):
                continue
            if value:
                return str(value)
        return ""

    def _to_log_entry(self, row: Any) -> Dict[str, Any]:
        timestamp = row["TimeGenerated"]
        return {
            "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp,
            "severity": self._first_present(row, _SEVERITY_COLUMNS) or "Informational",
            "message": self._first_present(row, _MESSAGE_COLUMNS) or "No message",
            "source": row["SourceTable"] or "Unknown",
        }
