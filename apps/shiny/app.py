"""Shiny app entry point: wires the home page into a runnable App."""

from __future__ import annotations

from modules.home import app_ui, server
from shiny import App

app = App(app_ui, server)
