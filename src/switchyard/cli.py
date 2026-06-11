"""Switchyard CLI entry point (click).

Session S1 ships ``config``, ``verify``, and ``demo --simulate``; the remaining
commands from the CLI map in SPEC.md §6 land in later sessions.
"""

from __future__ import annotations

import json as json_module
import random
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from switchyard import __version__
from switchyard.ledger import (
    Ledger,
    PrivacyMode,
    RequestRecord,
    default_ledger_path,
)

FABLE = "claude-fable-5"
OPUS = "claude-opus-4-8"

_console = Console()
_err_console = Console(stderr=True)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="switchyard")
def main() -> None:
    """Detect silent Claude model fallbacks and log them to a local tamper-evident ledger.

    Nothing leaves your machine: the ledger lives at ~/.switchyard/ledger.db
    (override with SWITCHYARD_HOME) and stores prompt hashes only by default.

    \b
    Quickstart:
      switchyard demo --simulate   Seed a simulated ledger (no API key needed)
      switchyard verify            Re-walk the hash chain
      switchyard config get privacy
    """


# -- config ------------------------------------------------------------------

CONFIG_KEYS = ("privacy",)

_FULL_MODE_WARNING = (
    "[bold yellow]Warning:[/bold yellow] privacy mode [bold]full[/bold] stores complete "
    "prompt bodies in the local ledger.\nThey still never leave your machine, but anyone "
    "with access to ~/.switchyard/ledger.db can read them.\n"
    "Switch back anytime: [bold]switchyard config set privacy hash[/bold]"
)


@main.group()
def config() -> None:
    """Get or set Switchyard configuration (keys: privacy)."""


@config.command("get")
@click.argument("key", type=click.Choice(CONFIG_KEYS))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def config_get(key: str, as_json: bool) -> None:
    """Print the current value of KEY.

    \b
    Example:
      switchyard config get privacy
    """
    with Ledger() as ledger:
        value = ledger.privacy_mode.value
    if as_json:
        click.echo(json_module.dumps({key: value}))
    else:
        click.echo(value)


@config.command("set")
@click.argument("key", type=click.Choice(CONFIG_KEYS))
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set KEY to VALUE.

    \b
    Examples:
      switchyard config set privacy hash       (default: prompt hashes only)
      switchyard config set privacy metadata   (adds engagement/task tags)
      switchyard config set privacy full       (stores prompt bodies — loud opt-in)
    """
    try:
        mode = PrivacyMode(value)
    except ValueError:
        choices = ", ".join(m.value for m in PrivacyMode)
        raise click.BadParameter(f"privacy must be one of: {choices}") from None
    if mode is PrivacyMode.FULL:
        _err_console.print(_FULL_MODE_WARNING)
    with Ledger() as ledger:
        ledger.set_privacy_mode(mode)
    click.echo(f"privacy = {mode.value}")


# -- verify ------------------------------------------------------------------


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def verify(as_json: bool) -> None:
    """Re-walk the ledger hash chain and report the first broken link, if any.

    Exits non-zero when the chain does not verify.

    \b
    Example:
      switchyard verify --json
    """
    with Ledger() as ledger:
        result = ledger.verify()
    if as_json:
        click.echo(
            json_module.dumps(
                {
                    "ok": result.ok,
                    "entries_checked": result.entries_checked,
                    "first_broken_sequence": result.first_broken_sequence,
                    "error": result.error,
                }
            )
        )
    elif result.ok:
        # ASCII-only output: legacy Windows consoles (cp1252) cannot encode
        # check marks and crash instead of degrading.
        _console.print(
            f"[bold green]OK: chain verified[/bold green] - "
            f"{result.entries_checked} entries, no broken links"
        )
    else:
        _console.print(
            f"[bold red]FAILED: chain broken[/bold red] at entry "
            f"{result.first_broken_sequence}: {result.error}"
        )
    if not result.ok:
        sys.exit(1)


# -- demo --------------------------------------------------------------------

_SIM_TASK_TYPES = (
    "exploit-analysis",
    "malware-triage",
    "detection-engineering",
    "report-writing",
    "code-review",
)


@main.command()
@click.option("--simulate", is_flag=True, help="Seed the ledger with simulated traffic.")
@click.option("--requests", "n_requests", default=60, show_default=True, help="Requests to seed.")
@click.option("--seed", default=2026, show_default=True, help="RNG seed (deterministic output).")
def demo(simulate: bool, n_requests: int, seed: int) -> None:
    """Seed a simulated ledger so you can try Switchyard without an API key.

    Simulated traffic includes a realistic share of declared fallbacks
    (Fable 5 silently served by Opus 4.8) so reports and verification have
    something to show. No network calls are made.

    \b
    Example:
      switchyard demo --simulate && switchyard verify
    """
    if not simulate:
        raise click.UsageError("demo currently requires --simulate (no live demo mode yet)")
    rng = random.Random(seed)
    fallbacks = 0
    with Ledger() as ledger:
        for i in range(n_requests):
            fell_back = rng.random() < 0.05
            served = OPUS if fell_back else FABLE
            output_tokens = rng.randint(150, 1500)
            record = RequestRecord(
                requested_model=FABLE,
                served_model=served,
                prompt=f"[simulated] {rng.choice(_SIM_TASK_TYPES)} request #{i + 1}",
                input_tokens=rng.randint(200, 4000),
                output_tokens=output_tokens,
                latency_ms=round(output_tokens * rng.uniform(18.0, 26.0), 1),
                engagement="demo",
                task_type=rng.choice(_SIM_TASK_TYPES),
            )
            entry = ledger.append_request(record)
            if fell_back:
                fallbacks += 1
                ledger.record_fallback(
                    request_sequence=entry.sequence,
                    detection_method="declared",
                    confidence=1.0,
                    requested_model=FABLE,
                    served_model=OPUS,
                    details={"simulated": True},
                )
        total = ledger.request_count()
        events = ledger.fallback_count()
        mode = ledger.privacy_mode.value

    table = Table(title="Switchyard demo - simulated ledger seeded")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Requests seeded", str(n_requests))
    table.add_row("Fallback events seeded", str(fallbacks))
    table.add_row("Ledger total requests", str(total))
    table.add_row("Ledger total fallback events", str(events))
    table.add_row("Privacy mode", mode)
    table.add_row("Ledger path", str(default_ledger_path()))
    _console.print(table)
    _console.print("Next: [bold]switchyard verify[/bold] to walk the hash chain.")


# -- report / audit / check ----------------------------------------------------


@main.command()
@click.option("--since", default=None, help="Window, e.g. 30m, 24h, 7d, 2w.")
@click.option(
    "--by",
    "group_by",
    default="task_type",
    show_default=True,
    type=click.Choice(["task_type", "engagement", "model"]),
    help="Group breakdown column.",
)
@click.option(
    "--format",
    "output_format",
    default="table",
    show_default=True,
    type=click.Choice(["table", "md", "json"]),
    help="Output format.",
)
def report(since: str | None, group_by: str, output_format: str) -> None:
    """Show fallback rates from the local ledger.

    \b
    Examples:
      switchyard report --since 7d
      switchyard report --by engagement --format md
      switchyard report --format json | jq .fallback_rate
    """
    from switchyard.report import build_report, render_terminal, to_json, to_markdown

    with Ledger() as ledger:
        try:
            data = build_report(ledger, since=since, by=group_by)
        except ValueError as exc:
            raise click.BadParameter(str(exc)) from None
    if output_format == "json":
        click.echo(to_json(data))
    elif output_format == "md":
        click.echo(to_markdown(data), nl=False)
    else:
        render_terminal(data, _console)


@main.command()
@click.option("--once", is_flag=True, help="Print capture status and exit (no live watch).")
@click.option(
    "--poll-interval", default=1.0, show_default=True, help="Seconds between ledger polls."
)
def audit(once: bool, poll_interval: float) -> None:
    """Confirm capture is working, then watch fallbacks live (Ctrl-C to stop).

    \b
    Example:
      switchyard audit          (live view)
      switchyard audit --once   (status only, for scripts)
    """
    from switchyard.report import capture_status, follow_fallbacks

    with Ledger() as ledger:
        status = capture_status(ledger)
        if status["capturing"]:
            _console.print(
                f"[bold green]capture OK[/bold green] - {status['total_requests']} requests in "
                f"{status['ledger_path']} (last: {status['last_request_at']})"
            )
        else:
            _console.print(
                "[bold yellow]no traffic captured yet[/bold yellow] - wrap your client "
                "(from switchyard import Anthropic) or start 'switchyard proxy', "
                "then send a request"
            )
        _console.print(
            f"privacy mode: {status['privacy_mode']} | "
            f"fallback events so far: {status['fallback_events']}"
        )
        if once:
            return
        _console.print("watching for fallback events (Ctrl-C to stop) ...")
        try:
            follow_fallbacks(ledger, _console, poll_interval=poll_interval)
        except KeyboardInterrupt:
            _console.print("audit stopped")


@main.command()
@click.option("--max-rate", required=True, help="Threshold, e.g. 0.02 or 2%.")
@click.option("--since", default=None, help="Window, e.g. 24h. Default: all time.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def check(max_rate: str, since: str | None, as_json: bool) -> None:
    """CI gate: exit non-zero when the fallback rate breaches --max-rate.

    \b
    Example:
      switchyard check --max-rate 2% --since 24h
    """
    from switchyard.ci import parse_rate, run_check

    try:
        threshold = parse_rate(max_rate)
        if since is not None:
            from switchyard.report import parse_since

            parse_since(since)  # fail fast on bad input
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from None
    with Ledger() as ledger:
        result = run_check(ledger, max_rate=threshold, since=since)
    if as_json:
        click.echo(json_module.dumps(result.as_dict(), sort_keys=True))
    elif result.ok:
        _console.print(
            f"[bold green]PASS[/bold green] fallback rate {result.fallback_rate:.2%} "
            f"<= {result.max_rate:.2%} ({result.fallback_events}/{result.total_requests} requests)"
        )
    else:
        _console.print(
            f"[bold red]FAIL[/bold red] fallback rate {result.fallback_rate:.2%} "
            f"> {result.max_rate:.2%} ({result.fallback_events}/{result.total_requests} requests)"
        )
    if not result.ok:
        sys.exit(1)


# -- rescope / templates -------------------------------------------------------


@main.command()
@click.argument("prompt")
@click.option("--task-type", default=None, help="Hint the task type to bias matching.")
@click.option("--llm", is_flag=True, help="Draft a tailored reframe with your Claude key.")
@click.option(
    "--model",
    default=None,
    help="Model for --llm (default: claude-sonnet-4-6).",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def rescope(
    prompt: str, task_type: str | None, llm: bool, model: str | None, as_json: bool
) -> None:
    """Suggest a compliant reframe that keeps work on the frontier model.

    Matches PROMPT against the template library; never sends anything unless you
    pass --llm (which uses your own ANTHROPIC_API_KEY). Suggestions only.

    \b
    Examples:
      switchyard rescope "exploit this binary"
      switchyard rescope "crack these hashes" --task-type credential-audit
      switchyard rescope "write a phishing email" --llm
    """
    from switchyard.ledger import hash_prompt
    from switchyard.rescope import (
        DEFAULT_LLM_MODEL,
        match_templates,
        render_suggestion,
    )

    matches = match_templates(prompt, task_type=task_type)
    if not matches:
        if as_json:
            click.echo(json_module.dumps({"matches": [], "suggestion": None}))
        else:
            _console.print(
                "[yellow]No template matched.[/yellow] Try --task-type, or use --llm "
                "to draft a tailored reframe with your own key."
            )
        if not llm:
            return

    top = matches[0].template if matches else None
    suggestion = render_suggestion(top, prompt) if top is not None else None

    if llm:
        suggestion = _llm_reframe(prompt, top, model or DEFAULT_LLM_MODEL)

    if not as_json:
        for match in matches:
            _console.print(
                f"[bold]{match.template.name}[/bold] "
                f"(task={match.template.task_type}, score={match.score:.0f}, "
                f"matched={', '.join(match.matched_signals) or '-'})"
            )
            _console.print(f"  rationale: {match.template.rationale.strip()}")
        if suggestion is not None:
            _console.print("\n[bold green]Suggested reframe[/bold green] (not sent anywhere):")
            _console.print(suggestion)
    else:
        click.echo(
            json_module.dumps(
                {
                    "matches": [
                        {
                            "name": m.template.name,
                            "task_type": m.template.task_type,
                            "score": m.score,
                            "matched_signals": m.matched_signals,
                        }
                        for m in matches
                    ],
                    "suggestion": suggestion,
                    "llm": llm,
                }
            )
        )

    if suggestion is not None:
        with Ledger() as ledger:
            ledger.record_rescope(
                suggestion=suggestion,
                template_name=top.name if top is not None else None,
                original_sha256=hash_prompt(prompt),
            )


def _llm_reframe(prompt: str, template: object, model: str) -> str:
    """Call the user's own Claude client to draft a tailored reframe."""
    import os

    from switchyard.rescope import build_llm_messages

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise click.ClickException(
            "--llm needs ANTHROPIC_API_KEY in the environment (your key, your call)."
        )
    import anthropic

    from switchyard.rescope import Template

    client = anthropic.Anthropic()
    tmpl = template if isinstance(template, Template) else None
    messages = build_llm_messages(prompt, tmpl)
    response = client.messages.create(model=model, max_tokens=1024, messages=messages)  # type: ignore[arg-type]
    return "".join(block.text for block in response.content if block.type == "text").strip()


@main.group()
def templates() -> None:
    """List or show built-in and user re-scope templates."""


@templates.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def templates_list(as_json: bool) -> None:
    """List available templates (built-in + user)."""
    from switchyard.rescope import load_templates

    items = load_templates()
    if as_json:
        click.echo(
            json_module.dumps(
                [{"name": t.name, "task_type": t.task_type, "source": t.source} for t in items]
            )
        )
        return
    table = Table(title=f"Re-scope templates ({len(items)})")
    table.add_column("Name")
    table.add_column("Task type")
    table.add_column("Source")
    for template in items:
        table.add_row(template.name, template.task_type, template.source)
    _console.print(table)


@templates.command("show")
@click.argument("name")
def templates_show(name: str) -> None:
    """Show one template by NAME (pattern, rationale, before/after)."""
    from switchyard.rescope import load_templates

    for template in load_templates():
        if template.name == name:
            _console.print(f"[bold]{template.name}[/bold] ({template.source})")
            _console.print(f"task_type: {template.task_type}")
            _console.print(f"trip_signature: {', '.join(template.trip_signature)}")
            _console.print(f"\nrewrite_pattern:\n{template.rewrite_pattern.strip()}")
            _console.print(f"\nrationale: {template.rationale.strip()}")
            _console.print(f"\nexample_before: {template.example_before.strip()}")
            _console.print(f"example_after: {template.example_after.strip()}")
            return
    raise click.ClickException(f"no template named {name!r} (try: switchyard templates list)")


# -- export / verify-export ----------------------------------------------------


@main.command()
@click.option("--engagement", default=None, help="Only export entries with this tag.")
@click.option(
    "--format",
    "output_format",
    default="json",
    show_default=True,
    type=click.Choice(["json", "md"]),
    help="Output format.",
)
@click.option(
    "--output", "-o", type=click.Path(dir_okay=False), default=None, help="Write to a file."
)
def export(engagement: str | None, output_format: str, output: str | None) -> None:
    """Produce a signed, self-contained export of ledger entries.

    The export carries the chain head and an HMAC signature so a third party can
    verify it offline (see docs/LEDGER_FORMAT.md and switchyard verify-export).

    \b
    Examples:
      switchyard export --engagement acme-q2 -o acme.json
      switchyard export --format md
    """
    from switchyard.export import build_export, export_markdown

    with Ledger() as ledger:
        doc = build_export(ledger, engagement=engagement)
    text = export_markdown(doc) if output_format == "md" else doc.to_json()
    if output:
        Path(output).write_text(text, encoding="utf-8")
        _console.print(
            f"wrote {doc.document['entry_count']} entries to {output} "
            f"(signature {doc.signature[:16]}...)"
        )
    else:
        click.echo(text)


@main.command(name="verify-export")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def verify_export(file: str, as_json: bool) -> None:
    """Verify a signed export FILE offline (signature + internal hash chain).

    Exits non-zero if either check fails.

    \b
    Example:
      switchyard verify-export acme.json
    """
    from switchyard.export import verify_export_file

    result = verify_export_file(file)
    if as_json:
        click.echo(
            json_module.dumps(
                {
                    "ok": result.ok,
                    "signature_valid": result.signature_valid,
                    "chain_valid": result.chain_valid,
                    "entry_count": result.entry_count,
                    "error": result.error,
                }
            )
        )
    elif result.ok:
        _console.print(
            f"[bold green]OK: export verified[/bold green] - "
            f"{result.entry_count} entries, signature + chain valid"
        )
    else:
        _console.print(f"[bold red]FAILED: export invalid[/bold red] - {result.error}")
    if not result.ok:
        sys.exit(1)


# -- proxy -------------------------------------------------------------------


@main.command()
@click.option("--port", default=4140, show_default=True, help="Port to listen on.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Loopback address to bind.")
@click.option(
    "--upstream",
    default="https://api.anthropic.com",
    show_default=True,
    help="Upstream API base URL.",
)
def proxy(port: int, host: str, upstream: str) -> None:
    """Start a localhost-only passthrough proxy that audits Claude traffic.

    Point any tool at it and keep working — responses stream through
    byte-for-byte; detection happens off the response path.

    \b
    Example:
      switchyard proxy --port 4140
      $env:ANTHROPIC_BASE_URL = "http://127.0.0.1:4140"   (PowerShell)
      export ANTHROPIC_BASE_URL=http://127.0.0.1:4140     (bash)
    """
    try:
        from switchyard.proxy import run_proxy
    except ImportError as exc:
        raise click.ClickException(str(exc)) from None
    _console.print(
        f"switchyard proxy listening on http://{host}:{port} -> {upstream}\n"
        f"Set ANTHROPIC_BASE_URL=http://{host}:{port} in the tool you want to audit."
    )
    try:
        run_proxy(host=host, port=port, upstream=upstream)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from None


# -- mcp ---------------------------------------------------------------------


@main.command()
def mcp() -> None:
    """Start the read-only MCP server (stdio) over the local ledger.

    Exposes get_fallback_summary, list_fallback_events, suggest_rescope, and
    verify_ledger to any MCP client. Read-only; nothing leaves the machine.

    \b
    Example:
      switchyard mcp
    """
    try:
        from switchyard.mcp_server import main as mcp_main
    except ImportError as exc:
        raise click.ClickException(str(exc)) from None
    mcp_main()


if __name__ == "__main__":
    main()
