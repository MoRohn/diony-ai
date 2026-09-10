"""Diony Director Agent service.

Deliberately does not import the FastAPI app here: adapters, rate cards, and
the estimator must stay importable without pulling the whole service stack.
"""

__all__: list[str] = []
