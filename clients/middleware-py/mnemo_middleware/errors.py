"""Public exception types for mnemo-middleware."""

from __future__ import annotations


class UnsupportedClient(TypeError):  # noqa: N818 -- public name; "Error" suffix would churn adopters
    """Raised when patch() is called with an SDK client the middleware
    doesn't know how to wrap. Tell the user which providers we support
    so they can install the right ``[extras]`` set."""
