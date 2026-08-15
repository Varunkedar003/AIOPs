"""Azure authentication via service principal (client secret) credentials.

Loads Tenant ID / Client ID / Client Secret / Subscription ID from .env
(via config.Config) and exposes a reusable ClientSecretCredential for
Azure SDK clients, plus a simple connection test.
"""
import logging
from typing import Any, Dict, Optional

from azure.identity import ClientSecretCredential
from azure.mgmt.resource.resources import ResourceManagementClient

from config import Config

logger = logging.getLogger(__name__)


class AzureAuth:
    """Builds and caches a ClientSecretCredential from .env configuration."""

    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ):
        self.tenant_id = tenant_id or Config.AZURE_TENANT_ID
        self.client_id = client_id or Config.AZURE_CLIENT_ID
        self.client_secret = client_secret or Config.AZURE_CLIENT_SECRET
        self.subscription_id = subscription_id or Config.AZURE_SUBSCRIPTION_ID
        self._credential: Optional[ClientSecretCredential] = None

    def is_configured(self) -> bool:
        """Whether all required service principal fields are present"""
        return bool(self.tenant_id and self.client_id and self.client_secret and self.subscription_id)

    def get_credential(self) -> ClientSecretCredential:
        """Return a cached ClientSecretCredential, building it on first use."""
        if not self.is_configured():
            raise ValueError(
                "Missing Azure credentials - set AZURE_TENANT_ID, AZURE_CLIENT_ID, "
                "AZURE_CLIENT_SECRET, and AZURE_SUBSCRIPTION_ID in .env"
            )

        if self._credential is None:
            self._credential = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
        return self._credential

    def test_connection(self) -> Dict[str, Any]:
        """Authenticate and list resource groups in the subscription to verify connectivity."""
        try:
            credential = self.get_credential()
            client = ResourceManagementClient(credential, self.subscription_id)
            resource_groups = [rg.name for rg in client.resource_groups.list()]

            return {
                "connected": True,
                "message": "Azure Connected",
                "subscription_id": self.subscription_id,
                "resource_groups": resource_groups,
            }
        except Exception as exc:
            logger.error("Azure authentication failed: %s", exc)
            return {
                "connected": False,
                "message": str(exc),
                "subscription_id": self.subscription_id,
                "resource_groups": [],
            }


if __name__ == "__main__":
    result = AzureAuth().test_connection()
    if result["connected"]:
        print("Azure Connected")
        print(f"Resource groups ({len(result['resource_groups'])}): {result['resource_groups']}")
    else:
        print(f"Azure authentication error: {result['message']}")
