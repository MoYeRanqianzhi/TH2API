"""Pelican qualification test: an auditable, on-demand model check.

The registration pipeline deliberately does not run this.  A serious run asks
a thinking model for tens of thousands of reasoning tokens, which is both slow
and expensive, so it is a manual gate rather than part of account creation.

The prompt is fixed: generate an HTML page containing an SVG animation of a
pelican riding a bicycle.  The report embeds the model's page in a sandboxed
iframe next to the raw response, usage counters, and reasoning text, so a human
can judge whether the animation is real without trusting a summary.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

DEFAULT_BASE_URL = "https://tokenharbor.ai"
DEFAULT_MODEL = "deepseek-v4.1-flash:free"
PELICAN_PROMPT = "生成HTML，内容是svg动画：鹈鹕骑自行车"


class QualificationError(RuntimeError):
    """The model call failed or returned something that cannot be audited."""


@dataclass(frozen=True)
class QualificationRun:
    model: str
    requested_model: str
    effort: str
    content: str
    reasoning: str
    usage: dict
    finish_reason: str
    request_id: str
    elapsed_seconds: float
    started_at: datetime
    max_tokens: int


def run_pelican_test(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = "max",
    max_tokens: int = 64_000,
    timeout: float = 1_800.0,
    base_url: str = DEFAULT_BASE_URL,
) -> QualificationRun:
    """Ask ``model`` for the pelican animation and return the streamed result.

    The call streams SSE.  A non-streamed request has to buffer the whole
    response, and the tunnel in front of Token Harbor sometimes drops such a
    long HTTPS read with ``BAD_DECRYPT`` after several minutes; streaming keeps
    bytes moving and lets the caller see partial progress in the logs.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PELICAN_PROMPT}],
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    result = _stream_with_retry(
        f"{base_url.rstrip('/')}/v1/chat/completions", api_key, payload, timeout
    )
    elapsed = time.monotonic() - started

    if not result["content"].strip():
        raise QualificationError("model returned no HTML content")

    return QualificationRun(
        model=result["model"] or model,
        requested_model=model,
        effort=effort,
        content=result["content"],
        reasoning=result["reasoning"],
        usage=result["usage"],
        finish_reason=result["finish_reason"],
        request_id=result["id"],
        elapsed_seconds=elapsed,
        started_at=started_at,
        max_tokens=max_tokens,
    )


def write_report(run: QualificationRun, output_dir: Path | str = "var/tests") -> Path:
    """Write a self-contained HTML report and return its path."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    stamp = run.started_at.strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", run.requested_model)
    path = destination / f"pelican-{slug}-{stamp}.html"
    path.write_text(_render_report(run), encoding="utf-8")
    return path


def _stream_with_retry(
    url: str, api_key: str, payload: dict, timeout: float
) -> dict:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return _consume_stream(url, api_key, payload, timeout)
        except requests.exceptions.RequestException as exc:  # type: ignore[attr-defined]
            last_error = exc
            if attempt == 0:
                time.sleep(5)
    raise QualificationError(f"stream failed twice: {last_error}")


def _consume_stream(url: str, api_key: str, payload: dict, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        impersonate="chrome",
        timeout=(30, min(300.0, timeout)),
        stream=True,
    )
    if response.status_code != 200:
        raise QualificationError(
            f"model call failed: {response.status_code} {response.text[:500]}"
        )

    content: list[str] = []
    reasoning: list[str] = []
    usage: dict = {}
    model = ""
    request_id = ""
    finish_reason = ""
    try:
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise QualificationError(f"stream exceeded {timeout:.0f}s")
            if not line or not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                break
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            request_id = request_id or chunk.get("id", "")
            model = chunk.get("model") or model
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                content.append(delta.get("content") or "")
                reasoning.append(delta.get("reasoning_content") or "")
                finish_reason = choice.get("finish_reason") or finish_reason
    finally:
        response.close()

    return {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "usage": usage,
        "model": model,
        "id": request_id,
        "finish_reason": finish_reason,
    }


def _extract_document(text: str) -> str:
    """Turn a fenced or bare answer into an HTML document for the iframe."""
    fence = re.search(r"```(?:html)?\s*(.*?)```", text, re.S | re.I)
    body = fence.group(1).strip() if fence else text.strip()
    if re.search(r"<html[\s>]", body, re.I):
        return body
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;min-height:100%;background:#f7f5f0}</style>"
        f"</head><body>{body}</body></html>"
    )


def _render_report(run: QualificationRun) -> str:
    usage = run.usage
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    reasoning_tokens = (
        (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        or usage.get("reasoning_tokens")
        or 0
    )
    document = _extract_document(run.content)

    raw_json = json.dumps(
        {
            "model": run.model,
            "effort": run.effort,
            "usage": usage,
            "finish_reason": run.finish_reason,
            "id": run.request_id,
            "content": run.content,
            "reasoning_content": run.reasoning,
        },
        ensure_ascii=False,
        indent=2,
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pelican qualification — {html.escape(run.requested_model)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f2efe8; color: #1a1918;
         font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  main {{ max-width: 1100px; margin: 0 auto; padding: 28px 20px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #6b675f; margin: 0 0 20px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
           gap: 10px; margin-bottom: 20px; }}
  .card {{ background: #fff; border: 1px solid #ddd8cc; border-radius: 12px;
           padding: 12px 14px; }}
  .card b {{ display: block; font-size: 12px; text-transform: uppercase;
             letter-spacing: .06em; color: #8a847a; margin-bottom: 3px; }}
  .card span {{ font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }}
  section {{ background: #fff; border: 1px solid #ddd8cc; border-radius: 14px;
             overflow: hidden; margin-bottom: 18px; }}
  section > h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
                  margin: 0; padding: 12px 16px; background: #faf8f3;
                  border-bottom: 1px solid #eee9de; color: #6b675f; }}
  iframe {{ display: block; width: 100%; height: 640px; border: 0; background: #fff; }}
  .pad {{ padding: 16px; }}
  pre {{ margin: 0; padding: 14px 16px; overflow: auto; max-height: 520px;
         background: #14130f; color: #e9e4d8; font: 12px/1.55 ui-monospace, monospace;
         white-space: pre-wrap; overflow-wrap: anywhere; }}
  details {{ border-top: 1px solid #eee9de; }}
  details summary {{ cursor: pointer; padding: 12px 16px; font-weight: 600; }}
  details[open] summary {{ border-bottom: 1px solid #eee9de; }}
  .prompt {{ font-size: 15px; }}
  .note {{ color: #6b675f; font-size: 13px; }}
  .ok {{ color: #1f7a4c; }}
</style>
</head>
<body>
<main>
  <h1>Pelican qualification</h1>
  <p class="sub">SVG animation audit — the iframe below is the model's own page,
     rendered with scripts sandboxed away from this report. Completion tokens
     include hidden reasoning when the gateway does not break it out.</p>

  <div class="grid">
    <div class="card"><b>Model requested</b><span>{html.escape(run.requested_model)}</span></div>
    <div class="card"><b>Model answered</b><span>{html.escape(run.model)}</span></div>
    <div class="card"><b>Reasoning effort</b><span>{html.escape(run.effort)}</span></div>
    <div class="card"><b>Finish reason</b><span>{html.escape(run.finish_reason or "—")}</span></div>
    <div class="card"><b>Elapsed</b><span>{run.elapsed_seconds:.1f}s</span></div>
    <div class="card"><b>Total tokens</b><span>{total_tokens:,}</span></div>
    <div class="card"><b>Completion tokens</b><span>{completion_tokens:,}</span></div>
    <div class="card"><b>Reasoning tokens</b><span>{f"{int(reasoning_tokens):,}" if reasoning_tokens else "— not reported"}</span></div>
    <div class="card"><b>Prompt tokens</b><span>{prompt_tokens:,}</span></div>
    <div class="card"><b>Request id</b><span>{html.escape(run.request_id)}</span></div>
    <div class="card"><b>Started</b><span>{run.started_at.isoformat()}</span></div>
    <div class="card"><b>Max tokens</b><span>{run.max_tokens:,}</span></div>
  </div>

  <section>
    <h2>Prompt</h2>
    <div class="pad"><p class="prompt">{html.escape(PELICAN_PROMPT)}</p></div>
  </section>

  <section>
    <h2>Rendered output (sandboxed iframe)</h2>
    <iframe sandbox="allow-scripts" srcdoc="{html.escape(document, quote=True)}"></iframe>
    <p class="pad note">If the scene is blank, the model produced no usable
       animation; expand the raw output below to confirm.</p>
  </section>

  <section>
    <h2>Model output</h2>
    <pre>{html.escape(run.content)}</pre>
  </section>

  <section>
    <h2>Reasoning trace</h2>
    <details>
      <summary>Show {len(run.reasoning):,} characters of reasoning</summary>
      <pre>{html.escape(run.reasoning) or "(no reasoning_content returned)"}</pre>
    </details>
  </section>

  <section>
    <h2>Raw response</h2>
    <details>
      <summary>Show JSON</summary>
      <pre>{html.escape(raw_json)}</pre>
    </details>
  </section>
</main>
</body>
</html>
"""
