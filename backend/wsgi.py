"""Production Gunicorn factory. Import/factory never initializes or migrates data."""
import os

from app import create_app as create_api_app
from batch_worker import start_workers
from static_spa import install_spa


def create_app(config_override=None):
    source = os.environ if config_override is None else config_override
    if source.get("APP_ENV") != "production":
        raise ValueError("The production WSGI entry requires explicit APP_ENV=production")
    application = create_api_app(config_override)
    if application.config["APP_ENV"] != "production":
        raise ValueError("WSGI must use production configuration")
    # Gunicorn calls this factory once per worker process after fork, so each
    # worker gets its own poll loop; job claims are atomic DB-side.
    start_workers(application, int(source.get("BATCH_WORKERS", 1)))
    return install_spa(application)
