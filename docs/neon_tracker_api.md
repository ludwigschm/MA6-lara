# Neon Tracker HTTP API Quick Reference

The tabletop bridge uses the lightweight HTTP interface that ships with the
Pupil Labs Neon Companion App. Each tracker exposes a small REST server on port
`8080` of the Android device. The bridge relies on the following calls:

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/status` | `GET` | Returns a JSON document containing the current tracker state. Important keys are `state`, `mode`, `frame`, and `fps`. |
| `/api/start_stream` | `POST` | Enables the live video stream of the tracker. No body is required. |
| `/api/recording/start` | `POST` | (Fallback) Starts a recording session; also enables the stream. |

The tracker accepts plain POST requests without an additional JSON payload. Any
2xx response indicates success. The bridge automatically probes `/api/status`
and then triggers `/api/start_stream`. If this endpoint is unavailable, it
immediately tries `/api/recording/start` instead.

The HTTP helper `tabletop.devices.tracker_client.HttpTracker` encapsulates this
behaviour. The helper also normalises the status payload and checks whether the
tracker has entered a streaming state. A tracker is considered running when at
least one of the following conditions is met:

* `state`, `mode`, or `text` equals `streaming`, `recording`, `running`, or `ok`
* the payload contains a non-negative `frame` counter
* the payload reports a positive `fps` value

The higher level `ensure_tracker_running(...)` routine wraps these calls: it
polls `/api/status`, sends the start request, and waits until the tracker
reports a streaming state (default timeout: eight seconds).

Because the bridge performs these steps automatically whenever a tracker is
connected, no manual user interaction is required to start the devices.
