"""``POST /webhooks/github`` — the entrypoint of the whole system.

Responsibility
--------------
Receive a push event from the customer repo, verify it, and start a pipeline
run. Nothing else — the handler must return immediately.

Inputs:  a GitHub push payload plus the ``X-Hub-Signature-256`` header.
Outputs: a created :class:`~orchestrator.schemas.Run` and a background task
         driving :class:`~orchestrator.pipeline.Pipeline`.

Why it returns immediately
--------------------------
GitHub times webhook deliveries out in seconds; a full run takes minutes. The
handler creates the run row, hands the pipeline to a background task, and
responds with the run id and its dashboard URL. All progress is observed over
the WebSocket, not the HTTP response.

Filtering
---------
Only pushes to ``TARGET_BRANCH`` of ``TARGET_REPO`` start a run. Everything else
gets a 200 with ``{"ignored": reason}`` — returning an error for events we
deliberately skip makes the customer's webhook page look broken.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 check of ``X-Hub-Signature-256``.

    Must use :func:`hmac.compare_digest`; a naive ``==`` here is a timing oracle
    on the webhook secret.
    """
    raise NotImplementedError
    # TODO(build): hmac.new(secret, body, sha256).hexdigest(), compare against
    # the "sha256=" prefixed header with compare_digest.


@router.post("/github")
async def github_push(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a push event and start a run."""
    raise NotImplementedError
    # TODO(build): read raw body + signature header (needs Request, not just
    # the parsed payload), verify, filter on repo/branch, extract sha +
    # message + pusher, create Run, spawn the pipeline as a background task,
    # return {"run_id": ..., "dashboard_url": ...}.


@router.post("/manual")
async def manual_trigger(repo: str, sha: str, branch: str = "main") -> dict[str, Any]:
    """Start a run without GitHub. The demo and development path.

    Kept deliberately: a live webhook depends on a tunnel and someone else's
    infrastructure, and neither belongs on the critical path of a stage demo.
    """
    raise NotImplementedError
    # TODO(build): same flow as github_push, minus verification.


# TODO(build): dedupe concurrent runs for the same SHA — GitHub redelivers, and
# two pipelines racing on one repo will fight over branches.
