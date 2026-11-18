"""Instance d'app pour `uvicorn status_backend.main:app` (compat)."""

from .app import create_app

app = create_app()
