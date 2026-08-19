"""Shiny app: search IMDb titles from the locally promoted DuckLake build."""

from __future__ import annotations

from great_tables import GT
from shiny import App, render, ui

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError
from imdb_ducklake.query.service import connect_readonly, search_titles

app_ui = ui.page_fluid(
    ui.h2("IMDb Title Search"),
    ui.input_text("query", "Search titles", placeholder="e.g. The Matrix"),
    ui.output_ui("results"),
)


def server(input, output, session):
    settings = Settings.load()
    startup_error: str | None = None
    connection = None
    try:
        connection = connect_readonly(settings)
    except NoPromotedBuildError as error:
        startup_error = str(error)

    @output
    @render.ui
    def results():
        if startup_error is not None or connection is None:
            ui.notification_show(startup_error, type="error", duration=None)
            return ui.p("No promoted build available. Run `make build` first.")
        frame = search_titles(connection, input.query() or "", limit=50).df()
        if frame.empty:
            return ui.p("No titles matched.")
        table = GT(frame)
        return ui.HTML(table.as_raw_html())


app = App(app_ui, server)
