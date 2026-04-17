import os
import sys

# Add your project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "utility_system.settings")

from django.core.wsgi import get_wsgi_application
app = get_wsgi_application()