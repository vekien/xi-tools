"""Every public command has help text. Click takes it from the docstring, and an
f-string is not a docstring: the ``xi ui items <type>`` factory's commands listed with
blank help until their text moved into ``help=``."""
import click

from xi.xi_cli import cli


def _visible(cmd, path):
    yield path, cmd
    if isinstance(cmd, click.Group):
        for name, sub in cmd.commands.items():
            if not sub.hidden:
                yield from _visible(sub, path + [name])


def test_every_visible_command_has_help():
    blank = [" ".join(path) for path, cmd in _visible(cli, ["xi"]) if not (cmd.help or "").strip()]
    assert blank == []


def test_the_item_type_commands_name_their_type():
    armor = cli.commands["ui"].commands["items"].commands["armor"]
    assert armor.help.startswith("Armor (IDs")
    assert armor.commands["search"].help == "Search the armor items by name."
    assert "xi ui items armor json --icons" in armor.commands["json"].help
