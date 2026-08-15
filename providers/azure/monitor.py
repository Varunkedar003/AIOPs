"""Azure Monitor platform metrics for a single resource.

Fetches CPU / memory / requests / availability / response-time metrics
directly from Azure Monitor's Metrics API for whichever resource is
selected. Metric names vary by Azure resource type, so available metric
definitions are checked first and only metrics that actually exist for
the given resource are requested - resources with none of them simply
come back with no data ("Metrics unavailable").
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from azure.mgmt.monitor import MonitorManagementClient

from .auth import AzureAuth
from utils.timing import log_timing

logger = logging.getLogger(__name__)

# Conceptual metric category -> candidate Azure Monitor metric names (most
# preferred first), covering the resource types this app discovers. Only
# candidates that actually exist on a given resource get queried. Category
# keys match dashboard/metrics.py's existing metric_type vocabulary (it
# already knows how to label/order "cpu"/"memory"/"requests"/"availability"/
# "latency") so that unchanged UI renders these correctly.
_CANDIDATE_METRICS: Dict[str, List[str]] = {
    "cpu": [
        "Percentage CPU", "CpuPercentage", "cpu_percent",
        "CpuTime", "percentProcessorTime", "node_cpu_usage_percentage",
        # Application Insights (microsoft.insights/components) namespaces its own metrics
        # under "<category>/<name>" instead of a flat name, e.g. "performanceCounters/..."
        # instead of "CpuPercentage" - none of the flat names above ever match an App
        # Insights resource, which is why it always came back "Metrics unavailable".
        "performanceCounters/processorCpuPercentage", "performanceCounters/processCpuPercentage",
    ],
    "memory": [
        "MemoryPercentage", "memory_percent", "MemoryWorkingSet",
        "usedmemorypercentage", "node_memory_working_set_percentage",
        "performanceCounters/processPrivateBytes",
    ],
    "requests": [
        "Requests", "totalcommandsprocessed", "TotalRequests",
        "requests/rate", "requests/count",
    ],
    "availability": [
        "HealthCheckStatus", "Availability", "connection_successful",
        "availabilityResults/availabilityPercentage",
    ],
    "latency": [
        "AverageResponseTime", "ResponseTime", "SuccessE2ELatency",
        "requests/duration",
    ],
}

# Azure Monitor's Unit enum values, aliased to the literal strings
# dashboard/metrics.py already special-cases for display formatting.
_UNIT_ALIASES = {
    "Percent": "percentage",
    "MilliSeconds": "ms",
    "CountPerSecond": "rps",
}

# A 1-hour/5-min window (12 points) missed most resources entirely - anything without
# traffic in that exact hour showed "Metrics unavailable" even though it's fine, just quiet.
# 24 hours at 1-hour granularity gives a much better chance of catching real data for
# lower-traffic resources, while _latest_data_point still just takes the most recent
# non-null point, so this doesn't change what "current value" means for busy resources.
_TIMESPAN = "PT24H"
_INTERVAL = "PT1H"


class AzureMonitorMetrics:
    """Fetches live Azure Monitor platform metrics for a single resource."""

    def __init__(self, azure_auth: Optional[AzureAuth] = None):
        self.azure_auth = azure_auth or AzureAuth()
        self._client: Optional[MonitorManagementClient] = None
        self.last_error: Optional[str] = None

    def _get_client(self) -> MonitorManagementClient:
        if self._client is None:
            credential = self.azure_auth.get_credential()
            # azure-core's own defaults here are 300s connection / 300s read - fine for a
            # one-off call, but this method fires 2 of these per resource, and both the
            # subscription-wide health/alerts sweep and a single selected resource's metrics
            # view depend on it never quietly sitting on a stalled connection for minutes.
            # 10s to connect / 30s to read is generous for a normal Monitor response but caps
            # the worst case to something a user will actually wait through.
            self._client = MonitorManagementClient(
                credential, self.azure_auth.subscription_id,
                connection_timeout=10, read_timeout=30,
            )
        return self._client

    def _available_metric_names(self, resource_id: str) -> set:
        client = self._get_client()
        with log_timing(logger, "AzureMonitorMetrics.get_metrics[definitions]"):
            definitions = list(client.metric_definitions.list(resource_id))
        return {definition.name.value for definition in definitions if definition.name}

    def get_metrics(self, resource_id: str) -> List[Dict[str, Any]]:
        """Fetch the latest value for each supported metric category available on this resource.

        Returns one {"metric_type", "name", "current_value", "unit", "timestamp"} dict per
        category the resource actually exposes. An empty list means the resource exposes none
        of the supported platform metrics (i.e. "Metrics unavailable").
        """
        if not resource_id:
            return []

        try:
            available_names = self._available_metric_names(resource_id)
            self.last_error = None
        except Exception as exc:
            logger.error("Azure Monitor metric definitions lookup failed for %s: %s", resource_id, exc)
            self.last_error = str(exc)
            return []

        category_to_metric: Dict[str, str] = {}
        for category, candidates in _CANDIDATE_METRICS.items():
            for candidate in candidates:
                if candidate in available_names:
                    category_to_metric[category] = candidate
                    break

        if not category_to_metric:
            return []

        try:
            client = self._get_client()
            with log_timing(logger, "AzureMonitorMetrics.get_metrics[values]"):
                response = client.metrics.list(
                    resource_id,
                    timespan=_TIMESPAN,
                    interval=_INTERVAL,
                    metricnames=",".join(category_to_metric.values()),
                    aggregation="Average,Total",
                )
            self.last_error = None
        except Exception as exc:
            logger.error("Azure Monitor metrics query failed for %s: %s", resource_id, exc)
            self.last_error = str(exc)
            return []

        metric_name_to_category = {name: category for category, name in category_to_metric.items()}
        results: List[Dict[str, Any]] = []

        for metric in response.value or []:
            metric_name = metric.name.value if metric.name else None
            category = metric_name_to_category.get(metric_name)
            if not category:
                continue

            latest_value, latest_timestamp = self._latest_data_point(metric)
            if latest_value is None:
                continue

            unit_str = str(metric.unit) if metric.unit else ""
            results.append({
                "metric_type": category,
                "name": metric_name,
                "current_value": latest_value,
                "unit": _UNIT_ALIASES.get(unit_str, unit_str),
                "timestamp": latest_timestamp,
            })

        return results

    @staticmethod
    def _latest_data_point(metric: Any) -> Tuple[Optional[float], Optional[str]]:
        """Return the most recent non-null value (Average, falling back to Total) and its timestamp."""
        latest_value: Optional[float] = None
        latest_timestamp = None

        for timeseries in metric.timeseries or []:
            for point in timeseries.data or []:
                value = point.average if point.average is not None else point.total
                if value is None:
                    continue
                if latest_timestamp is None or point.time_stamp > latest_timestamp:
                    latest_value = value
                    latest_timestamp = point.time_stamp

        return latest_value, latest_timestamp.isoformat() if latest_timestamp else None
