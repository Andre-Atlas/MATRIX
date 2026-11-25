"""Hyperparameter sweeps SDK: `sweep()` and `agent()` (contract G2)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import httpx

from .client import MatrixError, _seg
from .tracking import _resolve_client, _short_error, _sweep_trial_ctx, _SweepTrialContext

if TYPE_CHECKING:
    from .client import MatrixClient

logger = logging.getLogger("matrix_sdk.sweeps")


def sweep(
    config: Mapping[str, Any],
    name: str,
    *,
    description: str | None = None,
    client: MatrixClient | None = None,
) -> str:
    """Create a hyperparameter sweep (`POST /api/v1/sweeps`) and return its ID."""
    http_client, owns = _resolve_client(client)
    body: dict[str, Any] = {"name": name, "config": dict(config)}
    if description is not None:
        body["description"] = description
    try:
        resp = http_client._http.post("/api/v1/sweeps", json=body)
        resp.raise_for_status()
        data = resp.json()
        return str(data["id"])
    except httpx.HTTPStatusError as exc:
        raise MatrixError(
            f"could not create sweep: HTTP {exc.response.status_code} {exc.response.text[:200]}"
        ) from None
    except httpx.HTTPError as exc:
        raise MatrixError(f"could not create sweep: {exc}") from None
    finally:
        if owns:
            http_client._http.close()


def agent(
    sweep_id: str,
    function: Callable[[], Any],
    count: int | None = None,
    agent_id: str | None = None,
    *,
    client: MatrixClient | None = None,
    poll_paused: bool = False,
    max_pause_wait: float = 0.0,
) -> int:
    """Run the sweep agent loop: `next` -> `function()` -> `next`.

    Inside `function()`, `matrix_sdk.start_run()` automatically binds to the server-allocated run
    with `run.config` populated and `run.should_stop()` wired to pruning. Errors raised by a single
    run mark that run `failed` and continue to the next trial. Returns the number of trials executed.
    """
    http_client, owns = _resolve_client(client)
    executed = 0
    try:
        detail_resp = http_client._http.get(f"/api/v1/sweeps/{_seg(sweep_id)}")
        detail_resp.raise_for_status()
        detail = detail_resp.json()
        metric_key = str((detail.get("sweep") or {}).get("config", {}).get("metric", {}).get("key", ""))

        while count is None or executed < count:
            next_body: dict[str, Any] = {}
            if agent_id is not None:
                next_body["agent_id"] = agent_id
            resp = http_client._http.post(f"/api/v1/sweeps/{_seg(sweep_id)}/next", json=next_body)
            if resp.status_code == 204:
                retry_after = resp.headers.get("retry-after")
                if poll_paused and retry_after and max_pause_wait > 0:
                    try:
                        delay = min(float(retry_after), max_pause_wait)
                    except ValueError:
                        delay = min(1.0, max_pause_wait)
                    time.sleep(max(0.01, delay))
                    continue
                break
            resp.raise_for_status()
            payload = resp.json()
            run_data = dict(payload["run"])
            params = dict(payload.get("params") or {})

            ctx = _SweepTrialContext(
                client=http_client,
                sweep_id=sweep_id,
                run_data=run_data,
                params=params,
                metric_key=metric_key,
            )
            _sweep_trial_ctx.trial = ctx
            try:
                function()
            except Exception as exc:  # noqa: BLE001 - a single trial failure must not abort the agent
                err_text = _short_error(exc)
                logger.warning("sweep %s run %s failed: %s", sweep_id, run_data.get("id"), err_text)
                if ctx.run is not None:
                    ctx.run.finish("failed", error=err_text)
                else:
                    try:
                        http_client._http.patch(
                            f"/api/v1/runs/{_seg(run_data['id'])}",
                            json={"status": "failed", "error": err_text},
                        )
                    except httpx.HTTPError:
                        logger.debug("could not mark run %s failed", run_data.get("id"))
            else:
                if ctx.run is not None and ctx.run.status == "running":
                    ctx.run.finish("killed" if ctx.run._pruned else "finished")
                elif ctx.run is None:
                    try:
                        http_client._http.patch(
                            f"/api/v1/runs/{_seg(run_data['id'])}",
                            json={"status": "finished"},
                        )
                    except httpx.HTTPError:
                        logger.debug("could not mark run %s finished", run_data.get("id"))
            finally:
                _sweep_trial_ctx.trial = None
            executed += 1
        return executed
    except httpx.HTTPStatusError as exc:
        raise MatrixError(
            f"sweep agent error: HTTP {exc.response.status_code} {exc.response.text[:200]}"
        ) from None
    except httpx.HTTPError as exc:
        raise MatrixError(f"sweep agent error: {exc}") from None
    finally:
        if owns:
            http_client._http.close()
