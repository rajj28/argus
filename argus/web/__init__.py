"""Argus web: FastAPI console + in-process live job runner (docs/WEB_SPEC.md).

`argus.web.app` exposes the JSON API and serves the static console; `argus.web.jobs` owns the
whitelisted scenarios, the single-slot FIFO queue and the SSE event stream.
"""
