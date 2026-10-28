"""Production Gunicorn factory. Import/factory never initializes or migrates data."""
import os

from app import create_app as create_api_app
from static_spa import install_spa


def create_app(config_override=None):
    source = os.environ if config_override is None else config_override
    if source.get("APP_ENV") != "production":
        raise ValueError("The production WSGI entry requires explicit APP_ENV=production")
    application = create_api_app(config_override)
    if application.config["APP_ENV"] != "production":
        raise ValueError("WSGI must use production configuration")
    return install_spa(application)
