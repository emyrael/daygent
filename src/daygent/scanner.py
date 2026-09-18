"""Repository scanner. Orchestrates parsers; contains no technology-specific parse logic."""

from __future__ import annotations

from pathlib import Path

from daygent.config import DaygentConfig, load_config
from daygent.exceptions import DaygentConfigError
from daygent.graph.builder import ScanStats, build_graph
from daygent.models import Graph, ProjectMetadata
from daygent.parsers import ParseContext, ParserRegistry, ParseResult, default_registry
from daygent.utils.files import MAX_FILE_BYTES, iter_source_files
from daygent.utils.logging import get_logger

logger = get_logger()


class ScanReport:
    """In-memory result of a repository scan."""

    def __init__(
        self,
        graph: Graph,
        *,
        files_considered: int,
        output_path: Path,
        verbose_lines: list[str],
    ) -> None:
        self.graph = graph
        self.files_considered = files_considered
        self.output_path = output_path
        self.verbose_lines = verbose_lines


class Scanner:
    """Walk a tree, dispatch parsers, and build a graph.

    Never imports, execs, or evaluates scanned project code.
    """

    def __init__(self, registry: ParserRegistry | None = None) -> None:
        self.registry = registry or default_registry()

    def scan(
        self,
        root: Path,
        *,
        config: DaygentConfig | None = None,
        config_path: Path | None = None,
        verbose: bool = False,
    ) -> ScanReport:
        """Scan `root` and return a graph plus output path (not yet written)."""
        root = root.resolve()
        if not root.is_dir():
            raise DaygentConfigError(f"Scan path is not a directory: {root}")

        extra_warnings: list[str] = []
        verbose_lines: list[str] = []
        if config is None:
            config, config_warnings = load_config(root, config_path)
            extra_warnings.extend(config_warnings)
        elif config_path is not None:
            loaded, config_warnings = load_config(root, config_path)
            config = loaded
            extra_warnings.extend(config_warnings)

        context = ParseContext(root=root, config=config, verbose=verbose)
        results: list[ParseResult] = []
        files_considered = 0

        for path in iter_source_files(
            root,
            include=config.include,
            exclude=config.exclude,
            warnings=extra_warnings,
        ):
            files_considered += 1
            rel = _rel(path, root)
            try:
                size = path.stat().st_size
            except OSError as exc:
                warning = f"Warning: unable to read {rel}: {exc}"
                extra_warnings.append(warning)
                verbose_lines.append(warning)
                continue
            if size > MAX_FILE_BYTES:
                warning = f"Warning: skipped {rel} (larger than 10 MiB)"
                extra_warnings.append(warning)
                verbose_lines.append(warning)
                continue
            try:
                with path.open("rb"):
                    pass
            except OSError as exc:
                warning = f"Warning: unable to read {rel}: {exc}"
                extra_warnings.append(warning)
                verbose_lines.append(warning)
                continue

            parser = self.registry.choose(path, context)
            if parser is None:
                verbose_lines.append(f"No parser for {rel}")
                continue
            verbose_lines.append(f"Parsing {rel} with {parser.name}")
            try:
                result = parser.parse(path, context)
            except Exception as exc:  # noqa: BLE001 — isolate per-file parser failures
                warning = f"Warning: unable to parse {rel}"
                extra_warnings.append(warning)
                verbose_lines.append(f"{warning}: {exc}")
                logger.debug("Parser %s failed on %s", parser.name, rel, exc_info=True)
                continue
            results.append(result)

        graph = build_graph(
            results,
            ScanStats(
                files_scanned=files_considered,
                warnings=extra_warnings,
                project=ProjectMetadata(name=root.name, path=str(root)),
            ),
        )
        return ScanReport(
            graph,
            files_considered=files_considered,
            output_path=config.output_path(root),
            verbose_lines=verbose_lines,
        )


def _rel(path: Path, root: Path) -> str:
    """POSIX path relative to scan root, falling back to name."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name
