"""Home page: search IMDb titles from the configured DuckLake catalog."""

from __future__ import annotations

import html
import json
import re

import pandas as pd
from itables.shiny import DT
from shiny import module, reactive, render, ui

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import NoPromotedBuildError
from imdb_ducklake.query.service import connect_readonly, get_title_cast, search_titles

_WRAP_COLUMNS = ("Directors", "Writers")
_WRAP_WIDTH_PX = 320
_WRAP_STYLE = (
    f"display:inline-block;max-width:{_WRAP_WIDTH_PX}px;white-space:normal;word-break:break-word;"
)
_NARROW_COLUMNS = ("IMDb ID", "Rating", "Votes")
_NARROW_WIDTH_PX = 70
_ACTION_WIDTH_PX = 110


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


def _year_cell(start: object) -> str:
    if pd.isna(start):
        return ""
    return str(int(start))


def _cast_button(tconst: object, input_id: str) -> str:
    javascript = (
        f"Shiny.setInputValue({json.dumps(input_id)}, {json.dumps(str(tconst))}, "
        "{priority: 'event'})"
    )
    return (
        '<button type="button" class="btn btn-sm btn-outline-dark cast-button" '
        f'onclick="{html.escape(javascript, quote=True)}">Display cast</button>'
    )


@module.ui
def titles_ui(title: str, search_placeholder: str):
    """Build one title-type tab with an independent search field and result table."""
    return ui.TagList(
        ui.card(
            ui.card_header(f"Search {title}"),
            ui.input_text("query", "Search", placeholder=search_placeholder),
        ),
        ui.card(
            ui.card_header("Results"),
            ui.output_ui("results"),
        ),
    )


@module.server
def titles_server(
    input,
    output,
    session,
    *,
    title_type: str,
    show_end_year: bool,
) -> None:
    """Serve one title-type tab from the configured DuckLake catalog."""
    settings = Settings.load()
    startup_error: str | None = None
    connection = None
    try:
        connection = connect_readonly(settings)
    except NoPromotedBuildError as error:
        startup_error = str(error)

    @reactive.effect
    @reactive.event(input.cast_title)
    def show_cast():
        if startup_error is not None or connection is None:
            return
        tconst = input.cast_title()
        frame = get_title_cast(connection, tconst).df()
        if frame.empty:
            ui.modal_show(
                ui.modal(
                    ui.p(f"No principal cast is available for {tconst}."),
                    title="Cast",
                    easy_close=True,
                )
            )
            return

        title = str(frame.pop("Title").iloc[0])
        frame["Characters"] = frame["Characters"].fillna("")
        frame["Role"] = frame["Role"].map(lambda value: str(value).capitalize())
        ui.modal_show(
            ui.modal(
                ui.HTML(
                    DT(
                        frame,
                        pageLength=10,
                        lengthMenu=[10, 25, 50],
                        style="width:100%;margin:0",
                        maxBytes=0,
                        autoWidth=False,
                    )
                ),
                title=f"Cast — {title}",
                easy_close=True,
                size="l",
            )
        )

    @output
    @render.ui
    def results():
        if startup_error is not None or connection is None:
            ui.notification_show(startup_error, type="error", duration=None)
            return ui.p("No queryable lakehouse is available. Build the catalog first.")
        frame = search_titles(
            connection, input.query() or "", title_type=title_type, limit=500
        ).df()
        if frame.empty:
            return ui.p("No titles matched.")

        frame["Title"] = [
            _title_cell(p, o)
            for p, o in zip(frame["Primary Title"], frame["Original Title"], strict=True)
        ]
        if show_end_year:
            year_columns = ["Start Year", "End Year"]
            for column in year_columns:
                frame[column] = frame[column].map(_year_cell)
        else:
            frame["Year"] = frame["Start Year"].map(_year_cell)
            year_columns = ["Year"]
        runtime_column = "Episode runtime (min)" if show_end_year else "Runtime (min)"
        frame = frame.rename(columns={"Runtime (min)": runtime_column})
        frame["Cast"] = frame["IMDb ID"].map(
            lambda tconst: _cast_button(tconst, session.ns("cast_title"))
        )
        columns_to_drop = ["Primary Title", "Original Title", "Type"]
        if not show_end_year:
            columns_to_drop.extend(["Start Year", "End Year"])
        if not show_end_year:
            columns_to_drop.append("Episodes")
        frame = frame.drop(columns=columns_to_drop, errors="ignore")
        result_columns = [
            "IMDb ID",
            "Title",
            *year_columns,
            runtime_column,
        ]
        if show_end_year:
            result_columns.append("Episodes")
        result_columns.extend(["Rating", "Votes", "Genres", "Directors", "Cast"])
        result_columns.insert(result_columns.index("Cast"), "Writers")
        frame = frame[result_columns]
        for column in _WRAP_COLUMNS:
            frame[column] = frame[column].map(
                lambda value: f'<span style="{_WRAP_STYLE}">{html.escape(str(value))}</span>'
            )

        column_defs = [
            {
                "targets": [
                    frame.columns.get_loc(c)
                    for c in (*_NARROW_COLUMNS, *year_columns, runtime_column)
                ],
                "width": f"{_NARROW_WIDTH_PX}px",
            },
            {
                "targets": [frame.columns.get_loc(c) for c in _WRAP_COLUMNS],
                "width": f"{_WRAP_WIDTH_PX}px",
            },
            {
                "targets": [frame.columns.get_loc("Cast")],
                "width": f"{_ACTION_WIDTH_PX}px",
                "orderable": False,
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
