"""Open the web level editor at a specific zone.

If the editor server isn't running, prints the command to start it. Doesn't
manage the server lifecycle (you may already have it running for other work).
"""

from __future__ import annotations

import socket
import urllib.parse
import urllib.request
import webbrowser

import click

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8777


def _server_alive(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@click.command("preview")
@click.argument("zone_id", type=int)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int)
@click.option("--no-open", is_flag=True, help="Print the URL but don't launch a browser.")
def cmd(zone_id, host, port, no_open):
    """Open the level editor at zone ZONE_ID.

    Pre-selects the zone via the `?zone=<id>` query param so you don't have
    to scroll the picker.
    """
    url = f"http://{host}:{port}/?zone={urllib.parse.quote(str(zone_id))}"

    if not _server_alive(host, port):
        click.echo(click.style(
            f"Level editor not running on {host}:{port}.\n"
            f"Serve the zone editor on that port first, then re-run this\n"
            f"command, or open: {url}",
            fg="yellow"))
        return

    click.echo(f"Opening: {url}")
    if not no_open:
        webbrowser.open(url)
