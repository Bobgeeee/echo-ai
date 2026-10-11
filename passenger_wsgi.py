import sys
import os

# Set execution path to application root
sys.path.insert(0, os.path.dirname(__file__))

# Import FastAPI app as WSGI/ASGI application
from main import app as application