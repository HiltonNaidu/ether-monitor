"""
cli/main.py - ethmon CLI entry point

Commands:
    ethmon [mac/alias]              → show device info + live ping
    ethmon add [mac]                → register a new device
    ethmon alias [mac/alias] [name] → assign or update an alias
    ethmon monitor [mac/alias/all]  → live status table
    ethmon wake [mac/alias/all]     → send WoL magic packet
    ethmon list                     → list all registered devices
    ethmon ping [mac/alias/all]     → ping device(s)
    ethmon scan                     → discover devices on the network

Install as a command:
    Add to pyproject.toml:
        [project.scripts]
        ethmon = "cli.main:app"
    Then run: pip install -e .
"""

import typer
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich import box

# TODO: swap these relative imports for absolute once config is resolved
from core.registry import Registry, DeviceNotFoundError, DuplicateMacError, DuplicateAliasError
from core.wol import send_magic_packet
from core.scanner import run_scan

app     = Console()
cli     = typer.Typer(
            name="ethmon",
            help="LAN device manager — monitor, wake, and track devices on your network.",
            no_args_is_help=True,
        )
console = Console()


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _get_registry() -> Registry:
    """Initialise and return the registry. Single place to swap db path later."""
    return Registry()


def _abort(message: str) -> None:
    """Print an error and exit with a non-zero status code."""
    console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=1)


def _resolve_targets(registry: Registry, identifier: str) -> list:
    """
    Resolve 'all', a MAC, or an alias to a list of Device objects.
    Used by any command that accepts [mac/alias/all].
    """
    if identifier.lower() == "all":
        devices = registry.list_all()
        if not devices:
            _abort("No devices registered. Use 'ethmon add' to add one.")
        return devices

    try:
        return [registry.resolve(identifier)]
    except DeviceNotFoundError:
        _abort(f"No device found for '{identifier}'.")


def _ping_device(ip: str) -> Optional[float]:
    """
    Ping a device by IP and return latency in ms, or None if unreachable.
    Stub until core/net.py is built — falls back to a basic socket check.
    """
    # TODO: replace with shared ping utility from core/net.py
    import socket
    try:
        sock = socket.create_connection((ip, 80), timeout=1)
        sock.close()
        return 0.0   # placeholder — real latency comes from ping3
    except OSError:
        return None


# ── Commands ───────────────────────────────────────────────────────────────────

@cli.command(name="add")
def add(
    mac: str = typer.Argument(..., help="MAC address to register, e.g. AA:BB:CC:DD:EE:FF"),
    alias: Optional[str] = typer.Option(None, "--alias", "-a", help="Optional alias for the device"),
):
    """Register a new device by MAC address."""
    registry = _get_registry()
    try:
        device = registry.add(mac, alias=alias)
        console.print(f"[green]✓[/green] Device added: [bold]{device.mac}[/bold]", end="")
        if device.alias:
            console.print(f" [dim]({device.alias})[/dim]", end="")
        console.print()
    except DuplicateMacError:
        _abort(f"Device already registered: {mac}")
    except DuplicateAliasError:
        _abort(f"Alias already in use: '{alias}'")
    except ValueError as exc:
        _abort(str(exc))


@cli.command(name="alias")
def set_alias(
    identifier: str = typer.Argument(..., help="MAC address or current alias of the device"),
    alias: str      = typer.Argument(..., help="New alias to assign"),
):
    """Assign or update the alias for a device."""
    registry = _get_registry()
    try:
        device = registry.set_alias(identifier, alias)
        console.print(
            f"[green]✓[/green] Alias set: [bold]{device.mac}[/bold] → [cyan]{alias}[/cyan]"
        )
    except DeviceNotFoundError:
        _abort(f"No device found for '{identifier}'.")
    except DuplicateAliasError:
        _abort(f"Alias '{alias}' is already taken by another device.")


@cli.command(name="list")
def list_devices():
    """List all registered devices."""
    registry = _get_registry()
    devices  = registry.list_all()

    if not devices:
        console.print("[dim]No devices registered. Use 'ethmon add' to add one.[/dim]")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("MAC Address",  style="dim",  min_width=17)
    table.add_column("Alias",                      min_width=15)
    table.add_column("Last Known IP",              min_width=15)
    table.add_column("Added",        style="dim")

    for d in devices:
        table.add_row(
            d.mac,
            d.alias   or "[dim]—[/dim]",
            d.ip      or "[dim]—[/dim]",
            d.added_at[:10] if d.added_at else "[dim]—[/dim]",
        )

    console.print(table)


@cli.command(name="ping")
def ping(
    identifier: str = typer.Argument(..., help="MAC address, alias, or 'all'"),
):
    """Ping one or all devices and show their response status."""
    registry = _get_registry()
    devices  = _resolve_targets(registry, identifier)

    table = Table(box=box.SIMPLE, header_style="bold cyan")
    table.add_column("Device",  min_width=20)
    table.add_column("IP",      min_width=15)
    table.add_column("Status",  min_width=10)
    table.add_column("Latency", justify="right")

    for device in devices:
        label = device.alias or device.mac

        if not device.ip:
            table.add_row(label, "[dim]unknown[/dim]", "[yellow]no IP[/yellow]", "[dim]—[/dim]")
            continue

        latency = _ping_device(device.ip)
        if latency is not None:
            status  = "[green]online[/green]"
            latency_str = f"{latency:.1f} ms" if latency > 0 else "[dim]<1 ms[/dim]"
            registry.update_network_info(device.mac, ip=device.ip, is_online=True, ping_ms=latency)
        else:
            status      = "[red]offline[/red]"
            latency_str = "[dim]—[/dim]"
            registry.update_network_info(device.mac, is_online=False)

        table.add_row(label, device.ip, status, latency_str)

    console.print(table)


@cli.command(name="wake")
def wake(
    identifier: str = typer.Argument(..., help="MAC address, alias, or 'all'"),
):
    """Send a Wake-on-LAN magic packet to one or all devices."""
    registry = _get_registry()
    devices  = _resolve_targets(registry, identifier)

    for device in devices:
        label  = device.alias or device.mac
        result = send_magic_packet(device.mac)
        if result.success:
            console.print(f"[green]✓[/green] Magic packet sent → [bold]{label}[/bold]")
        else:
            console.print(f"[red]✗[/red] Failed to wake [bold]{label}[/bold]: {result.message}")


@cli.command(name="monitor")
def monitor(
    identifier: str = typer.Argument(..., help="MAC address, alias, or 'all'"),
):
    """Show full status for one or all devices including ping and IP."""
    registry = _get_registry()
    devices  = _resolve_targets(registry, identifier)

    table = Table(box=box.ROUNDED, header_style="bold cyan", show_lines=True)
    table.add_column("Device",    min_width=20)
    table.add_column("MAC",       style="dim", min_width=17)
    table.add_column("IP",        min_width=15)
    table.add_column("Hostname",  min_width=20)
    table.add_column("Status",    min_width=10)
    table.add_column("Last Seen", min_width=20)

    for device in devices:
        label = device.alias or "[dim]—[/dim]"

        if device.ip:
            latency   = _ping_device(device.ip)
            is_online = latency is not None
            registry.update_network_info(device.mac, ip=device.ip, is_online=is_online)
        else:
            is_online = False

        status    = "[green]online[/green]" if is_online else "[red]offline[/red]"
        last_seen = device.last_seen[:19].replace("T", " ") if device.last_seen else "[dim]never[/dim]"

        table.add_row(
            label,
            device.mac,
            device.ip       or "[dim]—[/dim]",
            device.hostname or "[dim]—[/dim]",
            status,
            last_seen,
        )

    console.print(table)


@cli.command(name="scan")
def scan(
    subnet: Optional[str] = typer.Option(
        None, "--subnet", "-s",
        help="Subnet to scan in CIDR notation, e.g. 192.168.1.0/24. Defaults to config value."
    ),
    update: bool = typer.Option(
        True, "--update/--no-update",
        help="Automatically update registry with any known devices found."
    ),
):
    """Scan the network for active devices."""
    console.print("[dim]Scanning network...[/dim]")

    kwargs = {}
    if subnet:
        kwargs["subnet"] = subnet

    result = run_scan(**kwargs)

    if result.error:
        _abort(result.error)

    if not result.devices:
        console.print("[yellow]No devices found.[/yellow]")
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("IP Address",  min_width=15)
    table.add_column("MAC Address", min_width=17, style="dim")
    table.add_column("Hostname",    min_width=25)
    table.add_column("Method",      min_width=6)
    table.add_column("In Registry", min_width=12)

    registry = _get_registry()

    for found in result.devices:
        in_registry = False

        if found.mac and update:
            existing = registry.get(found.mac)
            if existing:
                registry.update_network_info(
                    found.mac,
                    ip=found.ip,
                    hostname=found.hostname,
                    is_online=True,
                    ping_ms=found.latency_ms,
                )
                in_registry = True

        table.add_row(
            found.ip,
            found.mac       or "[dim]—[/dim]",
            found.hostname  or "[dim]—[/dim]",
            found.method,
            "[green]✓[/green]" if in_registry else "[dim]—[/dim]",
        )

    console.print(table)
    console.print(
        f"\n[dim]Found {len(result.devices)} device(s) in {result.scan_duration_seconds:.2f}s "
        f"via {result.method_used} scan.[/dim]"
    )


@cli.command(name="info")
def info(
    identifier: str = typer.Argument(..., help="MAC address or alias of the device"),
):
    """Show all stored information for a device and ping it."""
    registry = _get_registry()

    try:
        device = registry.resolve(identifier)
    except DeviceNotFoundError:
        _abort(f"No device found for '{identifier}'.")
        return

    # Live ping if we have an IP
    if device.ip:
        latency   = _ping_device(device.ip)
        is_online = latency is not None
        registry.update_network_info(device.mac, ip=device.ip, is_online=is_online)
    else:
        is_online = False

    status = "[green]online[/green]" if is_online else "[red]offline[/red]"

    console.print(f"\n[bold cyan]{device.alias or device.mac}[/bold cyan]")
    console.print(f"  MAC:        {device.mac}")
    console.print(f"  Alias:      {device.alias   or '[dim]not set[/dim]'}")
    console.print(f"  IP:         {device.ip       or '[dim]unknown[/dim]'}")
    console.print(f"  Hostname:   {device.hostname or '[dim]unknown[/dim]'}")
    console.print(f"  Status:     {status}")
    console.print(f"  Last seen:  {device.last_seen or '[dim]never[/dim]'}")
    console.print(f"  Added:      {device.added_at  or '[dim]unknown[/dim]'}")
    console.print()


# ── Default command (ethmon [mac/alias]) ──────────────────────────────────────
# Typer doesn't natively support a default positional command, so we use a
# callback that fires when no subcommand is given but an argument is passed.

@cli.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    identifier: Optional[str] = typer.Argument(None, help="MAC address or alias"),
):
    """
    LAN device manager. Pass a MAC address or alias to inspect a device,
    or use a subcommand. Run 'ethmon --help' for all commands.
    """
    if ctx.invoked_subcommand is None and identifier:
        # Delegate to the info command
        info(identifier)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()