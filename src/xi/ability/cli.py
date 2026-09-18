"""``xi ability`` command group."""

import click

from xi.ability.xi_compose import compose_cmd, recipe_cmd
from xi.ability.xi_inspect import cmd as inspect_cmd
from xi.ability.xi_publish import publish_cmd, slots_cmd


@click.group("ability")
def group():
    """Ability presentations — routine timelines of motion, VFX and sound.

    \b
    inspect  flatten a job ability / spell / weapon skill into one timeline
    recipe   starter recipe reproducing one source (schema/ability_recipe.json)
    compose  recipe -> new DAT(s)
    publish  the same as `xi dats prepare … --type ability` + `xi dats build`
    slots    the weapon-skill numbers a mix can publish to, and what holds each
    The viewer's pick list is baked by `xi mv update --only abilities`.
    """


group.add_command(inspect_cmd, "inspect")
group.add_command(recipe_cmd, "recipe")
group.add_command(compose_cmd, "compose")
group.add_command(publish_cmd, "publish")
group.add_command(slots_cmd, "slots")
