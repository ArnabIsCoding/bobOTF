import os
import sys
import uuid
import json

sys.stdout.reconfigure(encoding='utf-8')

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich import box

_console = Console(stderr=False, highlight=False)


def validate_schema5(obj: dict) -> None:
    required_keys = {"request_id", "diff_ref", "status", "approver"}
    if not required_keys.issubset(obj.keys()):
        raise ValueError(f"Schema 5 missing keys. Required: {required_keys}")
    if obj["status"] not in ("pending", "approved", "rejected"):
        raise ValueError(f"Invalid status: {obj['status']}")


def _build_schema5(schema4_dict: dict, status: str, approver: str) -> dict:
    schema5 = {
        "request_id": str(uuid.uuid4()),
        "diff_ref": schema4_dict,
        "status": status,
        "approver": approver,
    }
    validate_schema5(schema5)
    return schema5


def request_approval(schema4_dict: dict, approver_name: str = "operator_1") -> dict:
    """
    Present a Schema-4 ladder diff for operator approval.

    Behaviour is controlled by the APPROVAL_MODE environment variable:

    - interactive  (default): Prompt the operator on stdin. Falls back to
                               'rejected' on EOF (non-TTY / CI environments).
    - auto_approve:            Return 'approved' immediately — for CI tests
                               against known-safe fixtures.
    - auto_reject:             Return 'rejected' immediately — for pipeline
                               validation tests (makes the former implicit
                               CI behaviour explicit).
    - webhook:                 POST the Schema-4 payload to APPROVAL_WEBHOOK_URL
                               and poll for a response (e.g. from a Slack bot
                               or mobile app).
    """
    mode = os.getenv("APPROVAL_MODE", "interactive").lower()

    if mode == "auto_approve":
        return _build_schema5(schema4_dict, "approved", "auto_approve")

    if mode == "auto_reject":
        return _build_schema5(schema4_dict, "rejected", "auto_reject")

    if mode == "webhook":
        return _approval_webhook(schema4_dict)

    # interactive (default)
    return _approval_interactive(schema4_dict, approver_name)


def _approval_interactive(schema4_dict: dict, approver_name: str) -> dict:
    device_id = schema4_dict.get("device_id", "unknown")
    dialect = schema4_dict.get("dialect", "")
    validation = schema4_dict.get("validation", {})
    passed = validation.get("passed", False)

    # ── Header ──────────────────────────────────────────────────────────────
    _console.print()
    _console.rule(
        f"[bold yellow]⚡ APPROVAL REQUEST[/bold yellow]  [dim]{device_id}[/dim]",
        style="yellow",
    )

    # ── Metadata strip ──────────────────────────────────────────────────────
    meta = Table.grid(padding=(0, 2))
    meta.add_row("[dim]Device[/dim]", f"[bold]{device_id}[/bold]")
    meta.add_row("[dim]Dialect[/dim]", dialect)
    _console.print(meta)

    # ── Explanation ─────────────────────────────────────────────────────────
    explanation = schema4_dict.get("explanation", "")
    _console.print(
        Panel(explanation, title="[bold]Explanation[/bold]", border_style="blue", padding=(0, 1))
    )

    # ── Validation checks ───────────────────────────────────────────────────
    check_table = Table(
        show_header=False,
        box=box.SIMPLE,
        padding=(0, 1),
        title="[bold]Validation[/bold]",
        title_style="bold",
    )
    check_table.add_column(style="bold", no_wrap=True)
    check_table.add_column()
    overall_style = "bold green" if passed else "bold red"
    overall_icon = "✔" if passed else "✘"
    check_table.add_row(
        Text(f"{overall_icon} OVERALL", style=overall_style),
        Text("PASS" if passed else "FAIL", style=overall_style),
    )
    for check in validation.get("checks", []):
        icon = "✔" if check.strip().startswith("[PASS]") else "✘"
        style = "green" if icon == "✔" else "red"
        check_table.add_row(Text(icon, style=style), Text(check, style=style))
    _console.print(check_table)

    # ── Diff ────────────────────────────────────────────────────────────────
    old_rung = schema4_dict.get("old_rung", "")
    new_rung = schema4_dict.get("new_rung", "")
    diff_grid = Table.grid(padding=(0, 2), expand=True)
    diff_grid.add_column(ratio=1)
    diff_grid.add_column(ratio=1)
    diff_grid.add_row(
        Panel(
            f"[red]{old_rung}[/red]",
            title="[bold red]─ OLD RUNG[/bold red]",
            border_style="red",
            padding=(0, 1),
        ),
        Panel(
            f"[green]{new_rung}[/green]",
            title="[bold green]+ NEW RUNG[/bold green]",
            border_style="green",
            padding=(0, 1),
        ),
    )
    _console.print(diff_grid)

    # ── Prompt ──────────────────────────────────────────────────────────────
    _console.rule(style="yellow")
    while True:
        try:
            _console.print(
                "  [bold yellow]y[/bold yellow] approve  "
                "[bold red]n[/bold red] reject  "
                "[dim]skip[/dim] defer",
                end="",
            )
            choice = input("  › ").strip().lower()
            if choice in ("y", "yes"):
                _console.print("[bold green]✔ Approved.[/bold green]")
                return _build_schema5(schema4_dict, "approved", approver_name)
            elif choice in ("n", "no"):
                _console.print("[bold red]✘ Rejected.[/bold red]")
                return _build_schema5(schema4_dict, "rejected", approver_name)
            elif choice == "skip":
                _console.print("[dim]— Deferred (pending).[/dim]")
                return _build_schema5(schema4_dict, "pending", "")
            else:
                _console.print("[yellow]Enter y, n, or skip.[/yellow]")
        except EOFError:
            _console.print("\n[dim]EOF — defaulting to rejected.[/dim]")
            return _build_schema5(schema4_dict, "rejected", "system")


def _approval_webhook(schema4_dict: dict) -> dict:
    import urllib.request
    import time

    webhook_url = os.getenv("APPROVAL_WEBHOOK_URL", "")
    if not webhook_url:
        raise ValueError("APPROVAL_MODE=webhook requires APPROVAL_WEBHOOK_URL to be set.")

    poll_interval = float(os.getenv("APPROVAL_WEBHOOK_POLL_INTERVAL", "5"))
    timeout = float(os.getenv("APPROVAL_WEBHOOK_TIMEOUT", "300"))

    payload = json.dumps(schema4_dict).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        response_body = json.loads(resp.read().decode("utf-8"))

    # If the webhook responds immediately with a decision, use it.
    if "status" in response_body and response_body["status"] in ("approved", "rejected", "pending"):
        approver = response_body.get("approver", "webhook")
        return _build_schema5(schema4_dict, response_body["status"], approver)

    # Otherwise poll a status URL returned by the webhook.
    poll_url = response_body.get("poll_url", "")
    if not poll_url:
        raise ValueError("Webhook response missing 'poll_url' and no immediate status.")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        with urllib.request.urlopen(poll_url, timeout=10) as resp:
            status_body = json.loads(resp.read().decode("utf-8"))
        if status_body.get("status") in ("approved", "rejected"):
            approver = status_body.get("approver", "webhook")
            return _build_schema5(schema4_dict, status_body["status"], approver)

    # Timed out — fail safe.
    return _build_schema5(schema4_dict, "rejected", "webhook_timeout")


if __name__ == "__main__":
    # Standalone test with fixture from Device 4
    fixture = {
        "device_id": "conveyor_motor_2",
        "dialect": "IEC-61131-3 ladder",
        "old_rung": "|  Rung 0: Normal operation — conveyor_motor_2 running\n|--[ CMD_START ]----( conveyor_motor_2 )--|\n|",
        "new_rung": "|  Rung 0: If vibration exceeds threshold → stop conveyor_motor_2\n|----[vibration > THR]----(/conveyor_motor_2/)----|\n|",
        "ir": {"rungs": [{"contacts": [{"tag": "vibration", "type": "GT", "label": "vibration exceeds threshold"}], "coils": [{"tag": "conveyor_motor_2", "type": "NEGATED", "label": "Stop conveyor_motor_2"}], "timers": []}]},
        "explanation": "Fix instruction: \"If vibration exceeds threshold, stop conveyor motor 2\" | Compiled to 1 rung(s): |   Rung 0: If vibration exceeds threshold → stop conveyor_motor_2 | Total: 1 contact(s), 1 coil(s), 0 timer(s).",
        "validation": {"passed": True, "checks": ["[PASS] program is non-empty: Program has 1 rung(s)."]}
    }
    print("Running standalone test...")
    result = request_approval(fixture)
    print("\nFinal Schema 5 output:")
    print(json.dumps(result, indent=2))
