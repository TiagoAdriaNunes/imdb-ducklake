"""Shiny app entry point for browsing movies and television series."""

from __future__ import annotations

from pathlib import Path

from itables.shiny import init_itables
from modules.home import titles_server, titles_ui
from shiny import App, ui

_APP_ROOT = Path(__file__).resolve().parent

app_ui = ui.page_navbar(
    ui.nav_panel("Movies", titles_ui("movies", "Movies", "e.g. The Matrix")),
    ui.nav_panel("TV Series", titles_ui("tv_series", "TV Series", "e.g. Breaking Bad")),
    title="IMDb Title Search",
    header=ui.tags.head(
        ui.tags.meta(name="color-scheme", content="light"),
        ui.tags.link(rel="preconnect", href="https://fonts.googleapis.com"),
        ui.tags.link(
            rel="stylesheet",
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
        ),
        ui.HTML(init_itables()),
        ui.include_css(_APP_ROOT / "www" / "styles.css"),
    ),
    theme=ui.Theme(preset="shiny"),
)


def server(input, output, session):
    titles_server("movies", title_type="movie", show_end_year=False)
    titles_server("tv_series", title_type="tvSeries", show_end_year=True)


app = App(app_ui, server)
