"""Azure Blob Storage service — upload PDFs and generate SAS URLs."""

from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import (
    BlobServiceClient,
    BlobSasPermissions,
    generate_blob_sas,
    UserDelegationKey,
)

logger = logging.getLogger(__name__)


class BlobStorageService:
    """Handles uploading PDFs to Azure Blob Storage and generating SAS URLs."""

    def __init__(
        self,
        account_url: str | None = None,
        credential: DefaultAzureCredential | None = None,
        container_name: str | None = None,
    ) -> None:
        self.account_url = account_url or os.environ["AZURE_STORAGE_ACCOUNT_URL"]
        self.credential = credential or DefaultAzureCredential()
        self.container_name = container_name or os.environ.get("AZURE_STORAGE_CONTAINER", "pdf-uploads")
        self.blob_service_client = BlobServiceClient(self.account_url, credential=self.credential)
        self._ensure_container()

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _ensure_container(self) -> None:
        """Create the blob container if it doesn't already exist."""
        container_client = self.blob_service_client.get_container_client(self.container_name)
        try:
            container_client.get_container_properties()
        except Exception:
            logger.info("Creating blob container '%s'", self.container_name)
            container_client.create_container()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def upload_pdf(self, local_path: str | Path) -> str:
        """Upload a local PDF file to blob storage.

        Returns the blob name (key) used in the container.
        """
        local_path = Path(local_path)
        blob_name = f"annual-reports/{local_path.name}"

        blob_client = self.blob_service_client.get_blob_client(
            container=self.container_name,
            blob=blob_name,
        )

        logger.info("Uploading '%s' → blob '%s'", local_path.name, blob_name)
        with open(local_path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)

        return blob_name

    def generate_sas_url(self, blob_name: str, expiry_hours: int = 1) -> str:
        """Generate a read-only SAS URL for a blob using user delegation key.

        The URL is valid for *expiry_hours* (default 1 hour).
        """
        account_name = self.blob_service_client.account_name
        expiry_time = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)

        # Request a user delegation key valid for the SAS lifetime
        user_delegation_key = self.blob_service_client.get_user_delegation_key(
            key_start_time=datetime.now(timezone.utc) - timedelta(minutes=5),
            key_expiry_time=expiry_time,
        )

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=self.container_name,
            blob_name=blob_name,
            user_delegation_key=user_delegation_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry_time,
        )

        url = f"https://{account_name}.blob.core.windows.net/{self.container_name}/{blob_name}?{sas_token}"
        logger.debug("Generated SAS URL for '%s' (expires in %dh)", blob_name, expiry_hours)
        return url

    def upload_and_get_sas_url(self, local_path: str | Path, expiry_hours: int = 1) -> str:
        """Convenience: upload a PDF and return its SAS URL in one call."""
        blob_name = self.upload_pdf(local_path)
        return self.generate_sas_url(blob_name, expiry_hours)
