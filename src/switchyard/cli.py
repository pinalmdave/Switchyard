"""Switchyard CLI entry point (click).

Session S1 ships ``config``, ``verify``, and ``demo --simulate``; the remaining
commands from the CLI map in SPEC.md §6 land in later sessions.
"""

from __future__ import annotations

import json as json_module
import random
import sys

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


if __name__ == "__main__":
    main()
