"""Allow `python -m xi …` (used by the zone-editor bridge launcher)."""
from xi.xi_cli import cli

if __name__ == "__main__":
    cli(prog_name="xi")
