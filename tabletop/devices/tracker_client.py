"""HTTP helpers for starting and monitoring tracker devices."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

try:  # pragma: no cover - optional dependency during tests
    import requests
except Exception:  # pragma: no cover - fallback for test environments
    requests = None  # type: ignore[assignment]


class HttpTracker:
    """Small helper around the HTTP API exposed by the trackers."""

    STATUS_ENDPOINT: str = "/api/status"
    START_ENDPOINTS: tuple[str, str] = ("/api/start_stream", "/api/recording/start")

    def __init__(self, host: str, port: int = 8080, *, timeout: float = 1.5) -> None:
        base_host = host.strip().rstrip("/")
        self.base_url = f"http://{base_host}:{int(port)}"
        self.timeout = float(timeout)

    # ------------------------------------------------------------------
    # HTTP helpers
    def _request(self, method: str, endpoint: str) -> Optional[requests.Response]:
        if requests is None:
            return None
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.request(method, url, timeout=self.timeout)
        except Exception:
            return None
        return response

    def status(self) -> Optional[Dict[str, Any]]:
        """Query the tracker status endpoint."""

        response = self._request("GET", self.STATUS_ENDPOINT)
        if response is None or not response.ok:
            return None
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return payload
        text = ""
        try:
            text = response.text.strip()
        except Exception:
            text = ""
        if not text:
            return None
        return {"text": text}

    def start_stream(self) -> Optional[str]:
        """Send a start request using the first responding API endpoint."""

        for endpoint in self.START_ENDPOINTS:
            response = self._request("POST", endpoint)
            if response is None:
                continue
            if response.ok:
                return endpoint
        return None

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

    # ------------------------------------------------------------------
    # Status helpers
    @staticmethod
    def _normalise_status(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        normalised: Dict[str, Any] = {}
        for key in ("state", "mode"):
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                normalised[key] = text.lower()
        frame_keys = ("frame", "frame_index", "frame_id")
        for key in frame_keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                normalised["frame"] = int(value)
                break
            except (TypeError, ValueError):
                continue
        fps_value = payload.get("fps") or payload.get("frame_rate")
        if fps_value is not None:
            try:
                normalised["fps"] = float(fps_value)
            except (TypeError, ValueError):
                pass
        if "text" in payload:
            text_value = str(payload.get("text", "")).strip().lower()
            if text_value:
                normalised["text"] = text_value
        return normalised

    @classmethod
    def is_streaming(cls, payload: Optional[Dict[str, Any]]) -> bool:
        data = cls._normalise_status(payload)
        if not data:
            return False
        for key in ("state", "mode", "text"):
            value = data.get(key)
            if isinstance(value, str) and value in {"streaming", "recording", "running", "ok"}:
                return True
        frame = data.get("frame")
        if isinstance(frame, int) and frame >= 0:
            return True
        fps = data.get("fps")
        if isinstance(fps, (int, float)) and fps > 0:
            return True
        return False

    @classmethod
    def describe_status(cls, payload: Optional[Dict[str, Any]]) -> str:
        data = cls._normalise_status(payload)
        parts: list[str] = []
        state = data.get("state")
        mode = data.get("mode")
        if state:
            parts.append(f"state={state}")
        if mode and mode != state:
            parts.append(f"mode={mode}")
        if "frame" in data:
            parts.append(f"frame={data['frame']}")
        if "fps" in data:
            fps = data["fps"]
            if isinstance(fps, float):
                parts.append(f"fps={fps:.2f}")
            else:
                parts.append(f"fps={fps}")
        if not parts and "text" in data:
            parts.append(f"text={data['text']}")
        return ", ".join(parts) or "unbekannt"


def ensure_tracker_running(
    client: HttpTracker,
    *,
    start_timeout: float = 8.0,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Ensure that the tracker is reachable and streaming via the HTTP API."""

    status_payload: Optional[Dict[str, Any]] = None
    ready = client.wait_for_state(
        lambda payload: bool(payload), timeout=5.0, interval=0.5
    )
    if ready:
        status_payload = client.status()
    if not ready or not status_payload:
        if logger:
            logger.debug("Tracker %s status probe failed.", client.base_url)
        return False

    if logger:
        logger.info(
            "Tracker %s status ok (%s).",
            client.base_url,
            client.describe_status(status_payload),
        )

    endpoint = client.start_stream()
    if endpoint and logger:
        logger.info("Tracker %s start command via %s ok.", client.base_url, endpoint)
    elif logger:
        logger.debug(
            "Tracker %s start command not acknowledged.", client.base_url
        )

    streaming = client.wait_for_state(
        HttpTracker.is_streaming,
        timeout=max(0.5, float(start_timeout)),
        interval=0.5,
    )
    if streaming:
        if logger:
            logger.info("Tracker %s streaming ok.", client.base_url)
        return True

    if logger:
        logger.debug(
            "Tracker %s did not reach streaming state within %.1fs.",
            client.base_url,
            float(start_timeout),
        )
    return False


TrackerClient = HttpTracker

__all__ = ["HttpTracker", "TrackerClient", "ensure_tracker_running"]
