"""HTTP helpers for starting and monitoring tracker devices."""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Optional

import requests


class TrackerClient:
    """Small helper around the HTTP API exposed by the trackers."""

    _STATUS_ENDPOINTS: tuple[str, ...] = ("/status", "/api/status", "/health")
    _START_ENDPOINTS: tuple[str, ...] = (
        "/start_stream",
        "/api/start_stream",
        "/api/recording/start",
    )

    def __init__(self, host: str, port: int = 8080, *, timeout: float = 1.5) -> None:
        base_host = host.strip().rstrip("/")
        self.base_url = f"http://{base_host}:{int(port)}"
        self.timeout = float(timeout)

    # ------------------------------------------------------------------
    # HTTP helpers
    def _request(self, method: str, endpoint: str) -> Optional[requests.Response]:
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.request(method, url, timeout=self.timeout)
        except Exception:
            return None
        return response

    def status(self) -> Optional[Dict[str, Any]]:
        """Query the tracker status endpoint using multiple fallbacks."""

        for endpoint in self._STATUS_ENDPOINTS:
            response = self._request("GET", endpoint)
            if response is None or not response.ok:
                continue
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                try:
                    return response.json()
                except Exception:
                    pass
            return {"text": response.text.strip()}
        return None

    def start_stream(self) -> bool:
        """Send a start request using the first responding API endpoint."""

        for endpoint in self._START_ENDPOINTS:
            response = self._request("POST", endpoint)
            if response is None:
                continue
            if response.ok:
                return True
        return False

    def wait_for_state(
        self,
        predicate: Callable[[Optional[Dict[str, Any]]], bool],
        *,
        timeout: float = 10.0,
        interval: float = 0.5,
    ) -> bool:
        """Poll ``status`` until ``predicate`` returns ``True`` or the timeout expires."""

        deadline = time.time() + float(timeout)
        sleep_interval = max(0.1, float(interval))
        while time.time() < deadline:
            status = self.status()
            try:
                if predicate(status):
                    return True
            except Exception:
                pass
            time.sleep(sleep_interval)
        return False


def _is_streaming(status: Optional[Dict[str, Any]]) -> bool:
    if not status:
        return False
    if isinstance(status, dict):
        state = str(status.get("state", "")).strip().lower()
        if state in {"streaming", "recording", "running"}:
            return True
        if any(key in status for key in ("frame", "frame_index", "frame_id")):
            return True
        text = str(status.get("text", "")).strip().lower()
        if text in {"ok", "streaming", "running"}:
            return True
    return False


def ensure_tracker_running(
    client: TrackerClient,
    *,
    start_timeout: float = 8.0,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Ensure that the tracker is reachable and streaming via the HTTP API."""

    ready = client.wait_for_state(lambda payload: bool(payload), timeout=5.0, interval=0.5)
    if not ready:
        if logger:
            logger.debug("Tracker %s not ready – status probe failed.", client.base_url)
        return False

    started = client.start_stream()
    if not started and logger:
        logger.debug("Tracker %s start_stream() did not acknowledge start.", client.base_url)

    streaming = client.wait_for_state(
        _is_streaming,
        timeout=max(0.5, float(start_timeout)),
        interval=0.5,
    )
    if streaming:
        if logger:
            logger.info("Tracker %s confirmed streaming state.", client.base_url)
        return True

    if logger:
        logger.debug(
            "Tracker %s did not reach streaming state within %.1fs.",
            client.base_url,
            float(start_timeout),
        )
    return False


__all__ = ["TrackerClient", "ensure_tracker_running"]
