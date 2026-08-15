"""Azure Monitor Alerts and Resource Health for a single resource.

The fired-alert-instance view (Microsoft.AlertsManagement/alerts) and Azure
Resource Health aren't exposed by the azure-mgmt-monitor SDK already used
for metrics, so this calls the Azure Resource Manager REST APIs directly,
authenticated with the same service-principal credential as the rest of
the app.
"""
import logging
from typing import Any, Dict, List, Optional

import requests

from .auth import AzureAuth
from utils.timing import log_timing

logger = logging.getLogger(__name__)

_ARM_BASE_URL = "https://management.azure.com"
_ARM_SCOPE = "https://management.azure.com/.default"
_ALERTS_API_VERSION = "2019-05-05-preview"
_RESOURCE_HEALTH_API_VERSION = "2022-10-01"
_REQUEST_TIMEOUT = 30

# Azure Monitor alert severities (Sev0 = highest) mapped to the app's existing
# Critical/Warning/Info severity vocabulary (see dashboard/alerts.py).
_SEVERITY_MAP = {
    "sev0": "Critical",
    "sev1": "Critical",
    "sev2": "Warning",
    "sev3": "Info",
    "sev4": "Info",
}


class AzureAlerts:
    """Fetches live Azure Monitor alerts and resource health for a single resource."""

    def __init__(self, azure_auth: Optional[AzureAuth] = None):
        self.azure_auth = azure_auth or AzureAuth()
        self.last_error: Optional[str] = None

    def _arm_get(self, url: str, params: Dict[str, str], label: str = "ARM GET") -> Dict[str, Any]:
        credential = self.azure_auth.get_credential()
        token = credential.get_token(_ARM_SCOPE).token
        with log_timing(logger, label):
            response = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_REQUEST_TIMEOUT,
            )
        response.raise_for_status()
        return response.json() if response.content else {}

    def get_alerts(self, resource_id: str) -> List[Dict[str, Any]]:
        """Fetch Azure Monitor alerts targeting this resource. Empty if none exist."""
        if not resource_id:
            return []

        try:
            payload = self._arm_get(
                f"{_ARM_BASE_URL}/subscriptions/{self.azure_auth.subscription_id}"
                f"/providers/Microsoft.AlertsManagement/alerts",
                params={"api-version": _ALERTS_API_VERSION, "targetResource": resource_id},
                label="AzureAlerts.get_alerts",
            )
            self.last_error = None
        except Exception as exc:
            logger.error("Azure Monitor alerts query failed for %s: %s", resource_id, exc)
            self.last_error = str(exc)
            return []

        return [self._to_alert_dict(item) for item in payload.get("value", [])]

    @staticmethod
    def _to_alert_dict(item: Dict[str, Any]) -> Dict[str, Any]:
        essentials = ((item.get("properties") or {}).get("essentials")) or {}

        severity_raw = (essentials.get("severity") or "").lower()
        alert_state = (essentials.get("alertState") or "").lower()
        monitor_condition = (essentials.get("monitorCondition") or "").lower()
        is_active = monitor_condition == "fired" and alert_state != "closed"

        return {
            "id": item.get("id", ""),
            "name": essentials.get("alertRule") or item.get("name", "Unknown Alert"),
            "severity": _SEVERITY_MAP.get(severity_raw, "Info"),
            "status": "Active" if is_active else "Resolved",
            "created_at": essentials.get("startDateTime"),
            "last_updated": essentials.get("lastModifiedDateTime"),
            "resource_name": essentials.get("targetResourceName", ""),
            "message": essentials.get("description") or essentials.get("alertRule") or "No description available.",
            "count": 1,
        }

    def get_resource_health(self, resource_id: str) -> Dict[str, Any]:
        """Fetch the current Azure Resource Health status for this resource."""
        if not resource_id:
            return {"resource_id": resource_id, "health_status": "Unknown", "message": "No resource selected"}

        try:
            payload = self._arm_get(
                f"{_ARM_BASE_URL}{resource_id}/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
                params={"api-version": _RESOURCE_HEALTH_API_VERSION},
                label="AzureAlerts.get_resource_health",
            )
            self.last_error = None
        except Exception as exc:
            logger.error("Azure Resource Health query failed for %s: %s", resource_id, exc)
            self.last_error = str(exc)
            return {"resource_id": resource_id, "health_status": "Unknown", "message": str(exc)}

        properties = payload.get("properties") or {}
        return {
            "resource_id": resource_id,
            "health_status": properties.get("availabilityState", "Unknown"),
            "message": properties.get("summary", ""),
            "reason": properties.get("reasonType", ""),
            "occurred_at": properties.get("occurredTime"),
        }
