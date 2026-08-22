"""Home page: search IMDb titles from the locally promoted DuckLake build."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pandas as pd
from itables.shiny import DT, init_itables
from shiny import render, ui

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
_APP_ROOT = Path(__file__).resolve().parent.parent


def _title_cell(primary: str, original: str) -> str:
    primary_html = html.escape(str(primary))
    if pd.isna(original) or str(original) == str(primary):
        return f"<div>{primary_html}</div>"
    original_html = html.escape(str(original))
    subtitle = f'<div style="font-size:0.85em;color:#888;">{original_html}</div>'
    return f"<div>{primary_html}</div>{subtitle}"


def _format_title_type(value: str) -> str:
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", value).split()
    return " ".join("TV" if word.lower() == "tv" else word.capitalize() for word in words)


_SEARCHABLE_TYPES = ("movie", "tvSeries")


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
        ui.include_css(_APP_ROOT / "www" / "styles.css"),
    ),
    ui.div({"class": "app-navbar"}, ui.h2("IMDb Title Search")),
    ui.card(
        ui.card_header("Search Titles"),
        ui.layout_columns(
            ui.input_text("query", "Search", placeholder="e.g. The Matrix"),
            ui.input_selectize(
                "types",
                "Type",
                choices={t: _format_title_type(t) for t in _SEARCHABLE_TYPES},
                selected=["movie"],
                multiple=True,
                options={"dropdownParent": "body"},
            ),
            col_widths=[4, 3],
        ),
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
        selected_types = list(input.types())
        frame = search_titles(
            connection, input.query() or "", title_types=selected_types or None, limit=500
        ).df()
        if frame.empty:
            return ui.p("No titles matched.")

        frame["Type"] = frame["Type"].map(_format_title_type)
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
                pageLength=5,
                lengthMenu=[5, 10, 25, 50, 100],
                style="width:100%;margin:0",
                maxBytes=0,
                allow_html=True,
                autoWidth=False,
                columnDefs=column_defs,
            )
        )
