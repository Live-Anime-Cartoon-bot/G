#!/usr/bin/env python3
"""Entry point for the Telegram recording bot."""
import os
import sys

# Ensure the bot directory is in the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Run the bot
from bot import app
app.run()
