"""Register all domain routes on the shared API blueprint."""
from . import public, auth, toolkits, assets, batch, library, commerce, admin

__all__ = ["public", "auth", "toolkits", "assets", "batch", "library", "commerce", "admin"]
