import os
import sys

# The pack's folder is a ComfyUI package (it has __init__.py); tests import the subpackage directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
