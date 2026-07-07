"""Recipe base class.

A recipe is stateless: it receives a ready `Page` and the request, drives the
host, and returns a ResolveResponse. All browser work goes through the `Page`
protocol so recipes are unit-testable against a fake page.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..engine import Page
from ..models import ResolveRequest, ResolveResponse


class Recipe(ABC):
    # Human-readable recipe name, surfaced in /health.
    name: str = "base"
    # Host keys this recipe answers to (matched against ResolveRequest.host).
    hosts: tuple[str, ...] = ()

    @abstractmethod
    async def resolve(self, page: Page, req: ResolveRequest) -> ResolveResponse:
        """Drive the page to a direct download URL, or return ok=false."""
        raise NotImplementedError
