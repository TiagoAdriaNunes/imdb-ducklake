"""Shiny app: search IMDb titles from the locally promoted DuckLake build."""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
from itables.shiny import DT, init_itables
from shiny import App, render, ui

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError
from imdb_ducklake.query.service import connect_readonly, search_titles

_WRAP_COLUMNS = ("Directors", "Cast")
_WRAP_WIDTH_PX = 320
_WRAP_STYLE = (
    f"display:inline-block;max-width:{_WRAP_WIDTH_PX}px;white-space:normal;word-break:break-word;"
)
_NARROW_COLUMNS = ("IMDb ID", "Type", "Years", "Runtime (min)", "Rating", "Votes")
_NARROW_WIDTH_PX = 70
_HERE = Path(__file__).parent


def _title_cell(primary: str, original: str) -> str:
    primary_html = html.escape(str(primary))
    if pd.isna(original) or str(original) == str(primary):
        return f"<div>{primary_html}</div>"
    original_html = html.escape(str(original))
    subtitle = f'<div style="font-size:0.85em;color:#888;">{original_html}</div>'
    return f"<div>{primary_html}</div>{subtitle}"


def _years_cell(start: object, end: object) -> str:
    if pd.isna(start):
        return ""
    if pd.isna(end):
        return str(int(start))
    return f"{int(start)}-{int(end)}"


app_ui = ui.page_fluid(
    ui.tags.head(
        ui.tags.meta(name="color-scheme", content="light"),
        ui.tags.link(rel="preconnect", href="https://fonts.googleapis.com"),
        ui.tags.link(
            rel="stylesheet",
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
        ),
        ui.HTML(init_itables()),
        ui.include_css(_HERE / "www" / "styles.css"),
    ),
    ui.div({"class": "app-navbar"}, ui.h2("IMDb Title Search")),
    ui.card(
        ui.card_header("Search Titles"),
        ui.input_text("query", None, placeholder="e.g. The Matrix"),
    ),
    ui.card(
        ui.card_header("Results"),
        ui.output_ui("results"),
    ),
    theme=ui.Theme(preset="shiny"),
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
        frame = search_titles(connection, input.query() or "", limit=500).df()
        if frame.empty:
            return ui.p("No titles matched.")

        frame["Title"] = [
            _title_cell(p, o)
            for p, o in zip(frame["Primary Title"], frame["Original Title"], strict=True)
        ]
        frame["Years"] = [
            _years_cell(s, e) for s, e in zip(frame["Start Year"], frame["End Year"], strict=True)
        ]
        frame = frame.drop(columns=["Primary Title", "Original Title", "Start Year", "End Year"])
        frame = frame[
            [
                "IMDb ID",
                "Type",
                "Title",
                "Years",
                "Runtime (min)",
                "Rating",
                "Votes",
                "Genres",
                "Directors",
                "Cast",
            ]
        ]
        for column in _WRAP_COLUMNS:
            frame[column] = frame[column].map(
                lambda value: f'<span style="{_WRAP_STYLE}">{html.escape(str(value))}</span>'
            )

        column_defs = [
            {
                "targets": [frame.columns.get_loc(c) for c in _NARROW_COLUMNS],
                "width": f"{_NARROW_WIDTH_PX}px",
            },
            {
                "targets": [frame.columns.get_loc(c) for c in _WRAP_COLUMNS],
                "width": f"{_WRAP_WIDTH_PX}px",
            },
        ]
        return ui.HTML(
            DT(
                frame,
                pageLength=10,
                style="width:100%;margin:0",
                maxBytes=0,
                allow_html=True,
                autoWidth=False,
                columnDefs=column_defs,
            )
        )


app = App(app_ui, server)
