"""Concrete research templates and dataset adapters for the unified core.

Importing this package registers every plugin with the core registry. The
kernel never imports a concrete template, so adding one is a purely additive
change here.
"""

from app.experiment.templates import dress_new  # noqa: F401

__all__: list[str] = []
