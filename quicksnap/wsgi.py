"""WSGI config for quicksnap."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "quicksnap.settings")

application = get_wsgi_application()
