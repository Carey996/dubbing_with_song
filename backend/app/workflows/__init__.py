"""LangGraph workflow agents for the dubbing pipeline.

Each module here wraps one existing service flow in a ``StateGraph`` so it can be run,
observed node by node, and replayed from the HTTP layer (see ``backend.app.routers.workflows``).
The graph nodes delegate to ``backend.app.services``; they do not reimplement business logic.
"""
