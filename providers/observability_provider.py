from typing import Any, Dict, List, Optional
from .base_provider import BaseProvider
from .azure.monitor import AzureMonitorMetrics
from .azure.alerts import AzureAlerts
from .azure.log_analytics import AzureLogAnalytics


class MockObservabilityProvider(BaseProvider):
    """Observability provider for monitoring and metrics.

    Metrics, alerts, resource health, and Log Analytics logs are backed by live Azure
    Monitor / Resource Health / Log Analytics data. Application Insights remains an
    empty placeholder pending a later phase.
    """

    def __init__(
        self,
        monitor_metrics: Optional[AzureMonitorMetrics] = None,
        alerts: Optional[AzureAlerts] = None,
        log_analytics: Optional[AzureLogAnalytics] = None,
    ):
        self._monitor_metrics = monitor_metrics or AzureMonitorMetrics()
        self._alerts = alerts or AzureAlerts()
        self._log_analytics = log_analytics or AzureLogAnalytics()

    def get_metrics(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get live Azure Monitor metrics for a specific resource. Empty if it exposes none."""
        return self._monitor_metrics.get_metrics(resource_id)

    def get_metric(self, metric_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific metric by ID"""
        return None

    def get_metrics_by_type(self, resource_id: str, metric_type: str) -> List[Dict[str, Any]]:
        """Get metrics for a resource filtered by type"""
        return self._filter_by_field(self.get_metrics(resource_id), "metric_type", metric_type)

    def get_alerts(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get live Azure Monitor alerts for a specific resource. Empty if none exist."""
        return self._alerts.get_alerts(resource_id)

    def get_alert(self, alert_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific alert by ID"""
        return None

    def get_all_alerts(self) -> List[Dict[str, Any]]:
        """Get all alerts across all resources"""
        return []

    def get_alerts_by_severity(self, severity: str) -> List[Dict[str, Any]]:
        """Get alerts filtered by severity"""
        return []

    def get_alerts_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get alerts filtered by status"""
        return []

    def get_health(self, resource_id: str) -> Dict[str, Any]:
        """Get live Azure Resource Health status for a specific resource"""
        return self._alerts.get_resource_health(resource_id)

    def get_logs(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get live Log Analytics logs for a specific resource. Empty if none available."""
        return self._log_analytics.get_logs(resource_id)

    def get_application_insights(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get Application Insights data for a resource"""
        return None

    def get_all_application_insights(self) -> List[Dict[str, Any]]:
        """Get all Application Insights instances"""
        return []

    def get_log_analytics(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get Log Analytics data for a resource"""
        return None

    def get_all_log_analytics(self) -> List[Dict[str, Any]]:
        """Get all Log Analytics workspaces"""
        return []

    def get_resource_metrics_summary(self, resource_id: str, metrics: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Get a summary of live Azure Monitor metrics for a resource.

        Pass an already-fetched `metrics` list (e.g. from get_metrics()/get_resource_metrics())
        to avoid a second, redundant live Azure Monitor call for data the caller already has -
        ResourceService.get_resource_metrics_summary does exactly this. Fetches fresh only if
        `metrics` is omitted, so this stays a valid standalone call for anyone who wants one.
        """
        if metrics is None:
            metrics = self.get_metrics(resource_id)
        current_metrics = {
            metric.get("name", "unknown"): {
                "value": metric.get("current_value", 0),
                "unit": metric.get("unit", ""),
            }
            for metric in metrics
        }

        return {
            "resource_id": resource_id,
            "health_status": "Unknown",
            "total_alerts": 0,
            "critical_alerts": 0,
            "warning_alerts": 0,
            "metrics_count": len(metrics),
            "current_metrics": current_metrics,
        }

    def get_anomaly_detection(self, resource_id: str) -> Dict[str, Any]:
        """Get anomaly detection results for a resource"""
        return {
            "resource_id": resource_id,
            "anomalies_detected": 0,
            "anomalies": [],
            "overall_status": "unknown",
        }
