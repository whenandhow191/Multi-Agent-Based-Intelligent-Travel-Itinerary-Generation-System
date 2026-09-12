"""Deterministic implementation of the provider-neutral model gateway."""

from collections.abc import Sequence

from packages.harness.model_gateway import ModelGateway, ModelRequest, ModelTurn


class ScriptedModelGateway(ModelGateway):
    """Return fixed model turns and retain requests for assertions."""

    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        if not turns:
            raise ValueError("ScriptedModelGateway requires at least one turn")
        self._turns = tuple(turns)
        self._cursor = 0
        self.requests: list[ModelRequest] = []

    @property
    def remaining_turns(self) -> int:
        """Return how many turns remain in the deterministic script."""

        return len(self._turns) - self._cursor

    async def generate(self, request: ModelRequest) -> ModelTurn:
        """Return the next turn without network or provider credentials."""

        self.requests.append(request)
        if self._cursor >= len(self._turns):
            raise RuntimeError("scripted model gateway has no remaining turns")
        turn = self._turns[self._cursor]
        self._cursor += 1
        return turn
