"""Local Shopline monitoring dashboard."""

from .env_loader import load_environment

load_environment()

__all__ = ["backend"]
