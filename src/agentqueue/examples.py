"""Example jobs. Each one is deterministic between steps, so replay works."""

from __future__ import annotations

from agentqueue.worker import job


@job("summarize")
def summarize(ctx, payload):
    """Summarize each document with the model, then join the summaries."""
    parts = []
    for i, doc in enumerate(payload["docs"]):
        parts.append(
            ctx.step(f"summarize-{i}", lambda d=doc: ctx.model(f"Summarize in one line: {d}"))
        )
    return ctx.step("join", lambda: " | ".join(parts))


@job("classify")
def classify(ctx, payload):
    """Classify a ticket with the model and map the reply to a fixed label set."""
    labels = ["billing", "bug", "question"]
    reply = ctx.step("ask", lambda: ctx.model(f"Label this ticket: {payload['ticket']}"))
    label = ctx.step("label", lambda: labels[sum(map(ord, reply)) % len(labels)])
    return {"label": label}


@job("send_email")
def send_email(ctx, payload):
    """Draft an email with the model, then send it through the outbox."""
    body = ctx.step("draft", lambda: ctx.model(f"Write a short email about {payload['topic']}"))

    def queue_send():
        ctx.emit(payload["target"], {"to": payload["to"], "body": body})
        return "queued"

    return ctx.step("send", queue_send)


@job("bench")
def bench(ctx, payload):
    """The chaos benchmark job: some model steps, then one side effect through the outbox."""
    out = []
    for i in range(payload["model_steps"]):
        out.append(ctx.step(f"m{i}", lambda i=i: ctx.model(f"job {payload['n']} step {i}")))

    def notify():
        ctx.emit(payload["target"], {"job": payload["n"], "summary": out[-1] if out else ""})
        return "queued"

    ctx.step("notify", notify)
    return len(out)


@job("bench_naive")
def bench_naive(ctx, payload):
    """The baseline: the same work, but the side effect is sent inline with no dedupe key."""
    import json
    import urllib.request

    from agentqueue.receiver import _NO_PROXY  # the same no-proxy opener the outbox uses

    out = []
    for i in range(payload["model_steps"]):
        out.append(ctx.step(f"m{i}", lambda i=i: ctx.model(f"job {payload['n']} step {i}")))

    def notify_inline():
        req = urllib.request.Request(
            payload["target"],
            data=json.dumps({"job": payload["n"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _NO_PROXY.open(req, timeout=5) as r:
            r.read()
        return "sent"

    ctx.step("notify", notify_inline)
    return len(out)
