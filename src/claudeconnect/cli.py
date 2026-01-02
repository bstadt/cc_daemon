"""Command-line interface."""

import click

from . import __version__
from .auth import login, load_tokens, get_email
from .config import TOKENS_FILE


@click.group()
@click.version_option(version=__version__)
def main():
    """Claude Connect - Connect contextualized Claude instances."""
    pass


@main.command()
def login_cmd():
    """Authenticate with Google."""
    try:
        data = login()
        click.echo(f"Logged in as {data['email']}")
    except Exception as e:
        click.echo(f"Login failed: {e}", err=True)
        raise SystemExit(1)


main.add_command(login_cmd, name="login")


@main.command()
def status():
    """Show current authentication status."""
    data = load_tokens()
    if not data:
        click.echo("Not logged in. Run 'claudeconnect login' to authenticate.")
        return

    click.echo(f"Logged in as: {data.get('email', 'unknown')}")
    if data.get("id_token"):
        click.echo(f"id_token: {data['id_token'][:50]}...")
    if data.get("refresh_token"):
        click.echo("refresh_token: stored")


@main.command()
def logout():
    """Remove stored credentials."""
    if TOKENS_FILE.exists():
        TOKENS_FILE.unlink()
        click.echo("Logged out.")
    else:
        click.echo("Not logged in.")


if __name__ == "__main__":
    main()
