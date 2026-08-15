from typing import Any, Dict, List, Optional
from .base_provider import BaseProvider
from .azure.cost_management import AzureCostManagement


class MockCostProvider(BaseProvider):
    """Cost provider for financial operations and cost analysis, backed by live Azure Cost Management."""

    def __init__(self, cost_management: Optional[AzureCostManagement] = None):
        self._cost_management = cost_management or AzureCostManagement()

    def get_monthly_cost(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get live current/last month cost for a specific resource"""
        return self._cost_management.get_resource_cost(resource_id)

    def get_cost_breakdown(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get live cost breakdown (by meter category) for a specific resource"""
        return self._cost_management.get_cost_breakdown(resource_id)

    def get_daily_cost_trend(self, resource_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get live daily cost trend for a specific resource"""
        return self._cost_management.get_daily_cost_trend(resource_id, days=days)

    def get_cost_by_resource(self, time_period: Dict[str, str], caller: str = "unknown") -> Dict[str, Any]:
        """Get live actual cost for every resource in the subscription for a time period, from
        a single Cost Management query grouped by ResourceId (application-wide cached, 24h TTL,
        single-flight - see AzureCostManagement.get_cost_by_resource)."""
        return self._cost_management.get_cost_by_resource(time_period, caller=caller)

    def get_cost_trend(
        self, time_period: Dict[str, str], granularity: str = "Daily", caller: str = "unknown"
    ) -> List[Dict[str, Any]]:
        """Get live subscription-wide cost trend for a time period (application-wide cached,
        24h TTL, single-flight - see AzureCostManagement.get_cost_trend)."""
        return self._cost_management.get_cost_trend(time_period, granularity=granularity, caller=caller)

    def refresh_cost_cache(self, caller: str = "refresh") -> Dict[str, Any]:
        """Explicit, cooldown-limited cache-bypassing refresh - see AzureCostManagement.refresh.
        Returns {"refreshed": bool, "retry_after_seconds": float}."""
        return self._cost_management.refresh(caller=caller)

    def get_all_costs(self) -> List[Dict[str, Any]]:
        """Get cost data for all resources"""
        return []

    def get_costs_by_type(self, resource_type: str) -> List[Dict[str, Any]]:
        """Get costs filtered by resource type"""
        return []

    def get_costs_by_environment(self, environment: str) -> List[Dict[str, Any]]:
        """Get costs filtered by environment"""
        return []

    def get_total_subscription_cost(self) -> Dict[str, Any]:
        """Get live total cost for the entire subscription, plus breakdowns"""
        summary = self._cost_management.get_subscription_cost_summary()
        return {
            "total_monthly_cost": summary.get("total_monthly_cost", 0.0),
            "total_daily_cost": summary.get("total_daily_cost", 0.0),
            "currency": summary.get("currency", "USD"),
            "total_resources": 0,
            "cost_by_type": summary.get("cost_by_type", {}),
            "cost_by_resource_group": summary.get("cost_by_resource_group", {}),
            "cost_by_environment": {},
            "increasing_trend_resources": 0,
            "stable_trend_resources": 0,
            "cost_period": summary.get("cost_period", "current_month"),
        }

    def get_top_cost_resources(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get live top resources by cost"""
        return self._cost_management.get_top_cost_resources(limit=limit)

    def get_cost_trends(self, resource_id: str) -> Dict[str, Any]:
        """Get cost trend analysis for a specific resource"""
        cost = self._cost_management.get_resource_cost(resource_id)
        if not cost:
            return {"error": "No cost data available"}
        return {
            "resource_id": resource_id,
            "trend": cost.get("cost_trend", "stable"),
            "change_percentage": cost.get("cost_change_percentage", 0.0),
        }

    def get_environment_cost_summary(self) -> Dict[str, Any]:
        """Get cost summary by environment"""
        return {}

    def get_cost_optimization_opportunities(self) -> List[Dict[str, Any]]:
        """Get cost optimization opportunities"""
        return []
