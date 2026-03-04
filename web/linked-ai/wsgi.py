"""WSGI entry point for Apache mod_wsgi and Gunicorn."""
import importlib
import sys
import os

# Add the linked-ai directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Use importlib because "linked-ai" has a hyphen (not a valid Python identifier)
_module = importlib.import_module("linked-ai")
application = _module.app
