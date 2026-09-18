"""Offline HTML graph viewer. No server, no network, no Typer."""

from __future__ import annotations

from daygent.viewer.html import html_path_for_graph, render_html, write_html

__all__ = ["html_path_for_graph", "render_html", "write_html"]
