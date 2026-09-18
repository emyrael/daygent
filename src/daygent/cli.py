"""CLI entry. Typer lives here only — core modules must not import this file."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from daygent import __version__
from daygent.analysis import (
    AmbiguousNodeError,
    NodeNotFoundError,
    impact_as_dict,
)
from daygent.analysis.impact import impact as compute_impact
from daygent.config import load_config
from daygent.exceptions import DaygentConfigError, GraphNotFoundError
from daygent.graph.render import display_name, format_chains, render_mermaid
from daygent.graph.store import GraphStoreError, load_graph, render_graph_json, save_graph
from daygent.models import Graph, Node, NodeType
from daygent.parsers import ParserRegistry, default_registry
from daygent.scanner import Scanner
from daygent.utils.logging import configure_logging
from daygent.viewer import html_path_for_graph, write_html

SCAN_HINT = "Run `daygent scan .` first."


def _version_callback(value: bool) -> None:
    """Print the package version and exit."""
    if value:
        typer.echo(f"daygent {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="daygent",
    help="Static lineage and impact analysis for modern data + AI repositories.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the Daygent version and exit.",
    ),
) -> None:
    """Static lineage and impact analysis for modern data + AI repositories."""
    _ = version


def _registry() -> ParserRegistry:
    """Return parsers. Technology parsers register here as they land."""
    return default_registry()


def _graph_path(root: Path, config_path: Path | None = None) -> Path:
    """Resolve the saved graph path from config or the default artifact."""
    config, _warnings = load_config(root, config_path)
    return config.output_path(root)


def _load_saved_graph(root: Path, config_path: Path | None = None) -> Graph:
    """Load graph.json for display/impact commands."""
    path = _graph_path(root, config_path)
    try:
        return load_graph(path)
    except GraphNotFoundError:
        err_console.print(f"[red]Graph not found at {path}. {SCAN_HINT}[/red]")
        raise typer.Exit(code=1) from None
    except (GraphStoreError, OSError, ValueError) as exc:
        err_console.print(f"[red]Unable to load graph: {exc}[/red]")
        raise typer.Exit(code=1) from exc


def _print_json(payload: object) -> None:
    """Write JSON to stdout without Rich highlighting."""
    if isinstance(payload, str):
        typer.echo(payload, nl=not payload.endswith("\n"))
        return
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@app.command()
def scan(
    path: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Repository root to scan.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show extra diagnostics."),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to daygent.yml (defaults to <root>/daygent.yml).",
    ),
) -> None:
    """Scan a repository and write .daygent/graph.json."""
    configure_logging(verbose=verbose)
    scanner = Scanner(registry=_registry())
    try:
        report = scanner.scan(path, config_path=config, verbose=verbose)
        output = save_graph(report.graph, report.output_path)
    except KeyboardInterrupt:
        err_console.print("[yellow]Scan cancelled. No partial graph was written.[/yellow]")
        raise typer.Exit(code=130) from None
    except DaygentConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        err_console.print(f"[red]Unable to write graph: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    _print_scan_summary(report.graph, output)
    for warning in report.graph.scan.warnings:
        err_console.print(f"[yellow]{warning}[/yellow]")
    if verbose:
        for line in report.verbose_lines:
            err_console.print(line)


@app.command("graph")
def graph_cmd(
    json_output: bool = typer.Option(False, "--json", help="Print graph JSON."),
    mermaid: bool = typer.Option(False, "--mermaid", help="Print Mermaid graph LR."),
    html: bool = typer.Option(False, "--html", help="Write an offline HTML viewer."),
    include_source: bool = typer.Option(
        False,
        "--include-source",
        help="Embed small source excerpts around known lines (requires --html).",
    ),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the HTML viewer (requires --html)."
    ),
    root: Path = typer.Option(
        Path("."),
        "--root",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Repository root that contains the saved graph.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to daygent.yml used to locate the graph artifact.",
    ),
) -> None:
    """Display the saved dependency graph."""
    formats = [json_output, mermaid, html]
    if sum(1 for flag in formats if flag) > 1:
        err_console.print("[red]Use only one of --json, --mermaid, or --html.[/red]")
        raise typer.Exit(code=1)
    if open_browser and not html:
        err_console.print("[red]--open requires --html.[/red]")
        raise typer.Exit(code=1)
    if include_source and not html:
        err_console.print("[red]--include-source requires --html.[/red]")
        raise typer.Exit(code=1)
    try:
        graph = _load_saved_graph(root, config)
    except DaygentConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if json_output:
        _print_json(render_graph_json(graph))
        return
    if mermaid:
        typer.echo(render_mermaid(graph), nl=False)
        return
    if html:
        json_path = _graph_path(root, config)
        html_path = html_path_for_graph(json_path)
        try:
            written = write_html(
                graph,
                html_path,
                include_source=include_source,
                source_root=root,
            )
        except OSError as exc:
            err_console.print(f"[red]Unable to write HTML graph: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print("HTML graph written to:")
        typer.echo(str(written))
        if open_browser:
            webbrowser.open(written.resolve().as_uri())
        return
    console.print(format_chains(graph), end="")


@app.command()
def impact(
    node: str = typer.Argument(help="Node id or name to analyze."),
    depth: int | None = typer.Option(None, "--depth", help="Maximum downstream depth."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON impact report."),
    root: Path = typer.Option(
        Path("."),
        "--root",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Repository root that contains the saved graph.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to daygent.yml used to locate the graph artifact.",
    ),
) -> None:
    """Show downstream blast radius."""
    if depth is not None and depth < 1:
        err_console.print("[red]--depth must be >= 1.[/red]")
        raise typer.Exit(code=1)
    try:
        graph = _load_saved_graph(root, config)
    except DaygentConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        result = compute_impact(graph, node, max_depth=depth)
    except AmbiguousNodeError as exc:
        _print_ambiguous(exc, json_output)
        raise typer.Exit(code=1) from exc
    except NodeNotFoundError as exc:
        _print_unknown(exc, json_output)
        raise typer.Exit(code=1) from exc
    if json_output:
        _print_json(impact_as_dict(result, node))
        return
    _print_impact_text(result)


def _print_scan_summary(graph: Graph, output: Path) -> None:
    """Print the scan count table."""
    nodes = graph.nodes
    type_counts = {
        "Python files": _count_metadata(nodes, "python_file")
        or _count_type(nodes, NodeType.PYTHON_MODULE),
        "SQL files": _count_metadata(nodes, "sql_file"),
        "dbt models": _count_type(nodes, NodeType.DBT_MODEL),
        "FastAPI routes": _count_type(nodes, NodeType.API_ROUTE),
        "LangGraph nodes": _count_type(nodes, NodeType.LANGGRAPH_NODE),
        "External systems": _count_type(nodes, NodeType.EXTERNAL_SYSTEM),
    }
    console.print()
    console.print("[bold]Daygent[/bold]")
    console.print()
    console.print("Scanning repository...")
    console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(justify="left")
    table.add_column(justify="right")
    for label, count in type_counts.items():
        table.add_row(label, str(count))
    console.print(table)
    console.print()
    console.print(f"Discovered {len(graph.nodes)} nodes")
    console.print(f"Discovered {len(graph.edges)} edges")
    console.print()
    console.print("Graph saved to:")
    console.print()
    typer.echo(str(output))
    console.print()


def _count_type(nodes: list[Node], node_type: NodeType) -> int:
    """Count nodes of a canonical type."""
    return sum(1 for node in nodes if node.type == node_type)


def _count_metadata(nodes: list[Node], key: str) -> int:
    """Count nodes that carry a metadata flag."""
    return sum(1 for node in nodes if node.metadata.get(key))


def _candidate_payload(node: Node) -> dict[str, str | None]:
    """JSON/candidate row for an ambiguous or suggested node."""
    return {
        "id": node.id,
        "name": node.name,
        "type": node.type,
        "file_path": node.file_path,
    }


def _print_ambiguous(exc: AmbiguousNodeError, json_output: bool) -> None:
    """Print ambiguous-node candidates without picking one."""
    if json_output:
        _print_json(
            {
                "error": "ambiguous_node",
                "query": exc.query,
                "candidates": [_candidate_payload(node) for node in exc.candidates],
            }
        )
        return
    err_console.print(f"[red]Ambiguous node {exc.query!r}. Candidates:[/red]")
    for node in exc.candidates:
        location = node.file_path or ""
        err_console.print(f"  {node.id}  ({display_name(node)}, {node.type}, {location})")


def _print_unknown(exc: NodeNotFoundError, json_output: bool) -> None:
    """Print unknown-node error plus close suggestions."""
    if json_output:
        _print_json(
            {
                "error": "node_not_found",
                "query": exc.query,
                "suggestions": [_candidate_payload(node) for node in exc.suggestions],
            }
        )
        return
    err_console.print(f"[red]{exc}[/red]")
    if exc.suggestions:
        err_console.print("Did you mean:")
        for node in exc.suggestions:
            err_console.print(f"  {display_name(node)} ({node.id})")


def _print_impact_text(result) -> None:
    """Human impact report with Direct vs Downstream (transitive) sections."""
    label = display_name(result.origin)
    console.print(f"Impact: {label}")
    console.print()
    console.print("Direct:")
    if result.direct:
        for node in result.direct:
            console.print(f"  → {display_name(node)}")
    else:
        console.print("  (none)")
    console.print()
    console.print("Downstream:")
    if result.transitive:
        for node in result.transitive:
            console.print(f"  → {display_name(node)}")
    else:
        console.print("  (none)")


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
