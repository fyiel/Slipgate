"""Recipe base class.

A recipe is stateless: given a FlareSolverr client and the request, it drives the
host through FlareSolverr to a direct download URL and returns a ResolveResponse.
All gate clearing goes through the client, so recipes are unit-testable against a
fake client with no network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ResolveRequest, ResolveResponse
from ..solver import FlareSolverrClient


class Recipe(ABC):
    # Human-readable recipe name, surfaced in /health.
    name: str = "base"
    # Host keys this recipe answers to (matched against ResolveRequest.host).
    hosts: tuple[str, ...] = ()

    @abstractmethod
    async def resolve(self, client: FlareSolverrClient, req: ResolveRequest) -> ResolveResponse:
        """Drive the host to a direct download URL, or return ok=false."""
        raise NotImplementedError
