"""Recipe registry.

A recipe knows how to turn a host's page into a direct download URL. Recipes
register their host keys here so the API can dispatch a ResolveRequest by
`host`.
"""

from __future__ import annotations

from .base import Recipe
from .nexus import NexusRecipe

_RECIPES: dict[str, Recipe] = {}


def register(recipe: Recipe) -> None:
    for key in recipe.hosts:
        _RECIPES[key.lower()] = recipe


def get_recipe(host: str) -> Recipe | None:
    return _RECIPES.get(host.lower())


def recipe_names() -> list[str]:
    return sorted({r.name for r in _RECIPES.values()})


register(NexusRecipe())
