"""Shared test-process isolation for local Provider configuration."""

import os

import pytest


@pytest.fixture(autouse=True)
def clear_provider_environment() -> None:
    """Prevent imported third-party clients from leaking the developer .env."""
    names = tuple(
        name
        for name in os.environ
        if name.startswith("CONFLUX_WEAVE_PROVIDER_")
    )
    for name in names:
        os.environ.pop(name, None)
