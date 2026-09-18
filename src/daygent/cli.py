"""CLI entry. Typer lives here only — core modules must not import this file."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from daygent import __version__
from daygent.exceptions import DaygentConfigError
from daygent.graph.store import save_graph
from daygent.models import NodeType
from daygent.parsers import ParserRegistry, default_registry
from daygent.scanner import Scanner
from daygent.utils.logging import configure_logging

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
) -> None:
    """Display the saved graph (not yet implemented)."""
    _ = json_output, mermaid
    err_console.print(
        "Graph display is not implemented yet. Run `daygent scan` to write "
        ".daygent/graph.json."
    )
    raise typer.Exit(code=1)


@app.command()
def impact(
    node: str = typer.Argument(help="Node id or name to analyze."),
    depth: int | None = typer.Option(None, "--depth", help="Maximum downstream depth."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON impact report."),
) -> None:
    """Show downstream blast radius (not yet implemented)."""
    _ = node, depth, json_output
    err_console.print(
        "Impact analysis is not implemented yet. Run `daygent scan` first."
    )
    raise typer.Exit(code=1)


def _print_scan_summary(graph, output: Path) -> None:
    """Print the scan count table."""
    nodes = graph.nodes
    type_counts = {
        "Python files": _count_metadata(nodes, "python_file") or _count_type(nodes, NodeType.PYTHON_MODULE),
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
    console.print(f"Discovered {len(graph.edges)} dependencies")
    console.print()
    console.print("Graph saved to:")
    console.print()
    console.print(str(output))
    console.print()


def _count_type(nodes, node_type: NodeType) -> int:
    return sum(1 for node in nodes if node.type == node_type)


def _count_metadata(nodes, key: str) -> int:
    return sum(1 for node in nodes if node.metadata.get(key))


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
