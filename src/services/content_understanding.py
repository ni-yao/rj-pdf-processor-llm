"""Azure Content Understanding API wrapper.

Handles:
  - Creating / registering the custom analyzer
  - Submitting PDFs for analysis (async)
  - Polling for results
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"

_API_VERSION = "2025-11-01"


class ContentUnderstandingService:
    """Thin wrapper around the Content Understanding REST API."""

    def __init__(
        self,
        endpoint: str | None = None,
        credential: DefaultAzureCredential | None = None,
        analyzer_id: str | None = None,
        api_version: str = _API_VERSION,
    ) -> None:
        self.endpoint = (endpoint or os.environ["AZURE_AI_ENDPOINT"]).rstrip("/")
        self.credential = credential or DefaultAzureCredential()
        self.analyzer_id = analyzer_id or os.environ.get("ANALYZER_ID", "pcu_annual_report")
        self.api_version = api_version

        self._token: str | None = None
        self._token_expires_on: float = 0.0

        self._client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Content-Type": "application/json",
            },
            event_hooks={"request": [self._inject_auth_header]},
        )

    # ------------------------------------------------------------------ #
    #  Token management
    # ------------------------------------------------------------------ #

    def _get_token(self) -> str:
        """Return a valid bearer token, refreshing if needed."""
        # Refresh if within 5 minutes of expiry
        if self._token is None or time.time() >= self._token_expires_on - 300:
            token = self.credential.get_token(_COGNITIVE_SERVICES_SCOPE)
            self._token = token.token
            self._token_expires_on = token.expires_on
            logger.debug("Refreshed Azure AD token for Content Understanding")
        return self._token

    def _inject_auth_header(self, request: httpx.Request) -> None:
        """httpx event hook that adds the Authorization header to each request."""
        request.headers["Authorization"] = f"Bearer {self._get_token()}"

    # ------------------------------------------------------------------ #
    #  Analyzer management
    # ------------------------------------------------------------------ #

    def create_or_update_analyzer(self, schema_path: str | Path) -> dict:
        """PUT the custom analyzer schema to Content Understanding.

        Returns the operation status dict.
        """
        schema_path = Path(schema_path)
        with open(schema_path) as f:
            schema = json.load(f)

        url = (
            f"{self.endpoint}/contentunderstanding/analyzers/{self.analyzer_id}"
            f"?api-version={self.api_version}"
        )

        logger.info("PUT analyzer '%s' from %s", self.analyzer_id, schema_path.name)
        resp = self._client.put(url, json=schema)
        resp.raise_for_status()

        # The PUT returns 201 with an Operation-Location header
        operation_url = resp.headers.get("Operation-Location")
        if operation_url:
            logger.info("Analyzer creation started — polling operation …")
            return self._poll_operation(operation_url)

        return resp.json()

    def get_analyzer(self) -> dict | None:
        """GET the current analyzer definition. Returns None if not found."""
        url = (
            f"{self.endpoint}/contentunderstanding/analyzers/{self.analyzer_id}"
            f"?api-version={self.api_version}"
        )

        resp = self._client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    #  Analyze a document
    # ------------------------------------------------------------------ #

    def analyze(self, sas_url: str) -> dict:
        """Submit a document for analysis and poll until complete.

        *sas_url* must be a publicly-readable URL (e.g. a Blob SAS URL).
        Returns the full analysis result JSON.
        """
        url = (
            f"{self.endpoint}/contentunderstanding/analyzers/{self.analyzer_id}:analyze"
            f"?api-version={self.api_version}"
        )

        body = {"inputs": [{"url": sas_url}]}

        logger.info("POST analyze request to '%s'", self.analyzer_id)
        resp = self._client.post(url, json=body)
        resp.raise_for_status()

        # 202 Accepted — poll the Operation-Location header
        operation_url = resp.headers.get("Operation-Location")
        if operation_url:
            return self._poll_result(operation_url)

        # Some responses return the result inline
        return resp.json()

    # ------------------------------------------------------------------ #
    #  Polling helpers
    # ------------------------------------------------------------------ #

    def _poll_operation(
        self,
        operation_url: str,
        interval: int | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Poll an operation URL until it succeeds or times out."""
        interval = interval or int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
        timeout = timeout or int(os.environ.get("POLL_TIMEOUT_SECONDS", "600"))
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            resp = self._client.get(operation_url)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "").lower()

            if status == "succeeded":
                logger.info("Operation succeeded.")
                return data
            if status in ("failed", "canceled"):
                logger.error("Operation %s: %s", status, data)
                raise RuntimeError(f"Operation {status}: {data}")

            logger.debug("Operation status: %s — retrying in %ds", status, interval)
            time.sleep(interval)

        raise TimeoutError(f"Operation did not complete within {timeout}s")

    def _poll_result(
        self,
        operation_url: str,
        interval: int | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Poll an analyze result URL until status is Succeeded."""
        interval = interval or int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
        timeout = timeout or int(os.environ.get("POLL_TIMEOUT_SECONDS", "600"))
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            resp = self._client.get(operation_url)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "").lower()

            if status == "succeeded":
                logger.info("Analysis succeeded.")
                return data
            if status in ("failed", "canceled"):
                logger.error("Analysis %s: %s", status, data)
                raise RuntimeError(f"Analysis {status}: {data}")

            logger.debug("Analysis status: %s — retrying in %ds", status, interval)
            time.sleep(interval)

        raise TimeoutError(f"Analysis did not complete within {timeout}s")

    # ------------------------------------------------------------------ #
    #  Cleanup
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ContentUnderstandingService:
        return self

    def __exit__(self, *args) -> None:
        self.close()
