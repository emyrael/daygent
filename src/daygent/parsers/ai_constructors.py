"""Table-driven LLM, embedding, and vector-store constructors. AST only."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import (
    make_embedding_model_id,
    make_llm_id,
    make_node_id,
    make_python_function_id,
    make_vector_collection_id,
    make_vector_store_id,
)
from daygent.parsers.ast_literals import (
    call_function_name,
    expression_text,
    literal_string,
    named_or_positional,
)
from daygent.parsers.base import ParseResult
from daygent.parsers.evidence import evidence_metadata

KIND_LLM = "llm"
KIND_EMBEDDING = "embedding"
KIND_VECTOR = "vector_store"

VECTOR_CLASS_METHODS = frozenset(
    {"from_documents", "from_texts", "load_local", "from_existing_index"}
)
# Only these class methods push documents into a collection, making the calling
# function the producer. Everything else (including a bare constructor) attaches
# to a collection that already exists, so the collection is an input.
VECTOR_WRITE_METHODS = frozenset({"from_documents", "from_texts"})


@dataclass(frozen=True)
class AiConstructorSpec:
    """One statically recognized constructor family."""

    name: str
    kind: str
    provider: str
    model_keys: tuple[str, ...] = ("model", "model_name")
    model_positional: bool = False
    collection_keys: tuple[str, ...] = ()


LLM_CONSTRUCTORS: tuple[AiConstructorSpec, ...] = (
    AiConstructorSpec("ChatOpenAI", KIND_LLM, "openai"),
    AiConstructorSpec("OpenAI", KIND_LLM, "openai"),
    AiConstructorSpec(
        "AzureOpenAI",
        KIND_LLM,
        "azure",
        model_keys=("model", "model_name", "azure_deployment", "deployment_name"),
    ),
    AiConstructorSpec("AzureChatOpenAI", KIND_LLM, "azure"),
    AiConstructorSpec("ChatAnthropic", KIND_LLM, "anthropic"),
    AiConstructorSpec("Anthropic", KIND_LLM, "anthropic"),
)

EMBEDDING_CONSTRUCTORS: tuple[AiConstructorSpec, ...] = (
    AiConstructorSpec("OpenAIEmbeddings", KIND_EMBEDDING, "openai"),
    AiConstructorSpec(
        "SentenceTransformer",
        KIND_EMBEDDING,
        "sentence_transformers",
        model_keys=("model_name_or_path", "model_name", "model"),
        model_positional=True,
    ),
    AiConstructorSpec(
        "HuggingFaceEmbeddings",
        KIND_EMBEDDING,
        "huggingface",
        model_keys=("model_name", "model"),
    ),
)

VECTOR_STORE_CONSTRUCTORS: tuple[AiConstructorSpec, ...] = (
    AiConstructorSpec(
        "PineconeVectorStore",
        KIND_VECTOR,
        "pinecone",
        collection_keys=("index_name", "index"),
    ),
    AiConstructorSpec(
        "Pinecone",
        KIND_VECTOR,
        "pinecone",
        collection_keys=("index_name", "index"),
    ),
    AiConstructorSpec(
        "PineconeClient",
        KIND_VECTOR,
        "pinecone",
        collection_keys=("index_name", "index"),
    ),
    AiConstructorSpec(
        "QdrantVectorStore",
        KIND_VECTOR,
        "qdrant",
        collection_keys=("collection_name", "collection"),
    ),
    AiConstructorSpec(
        "QdrantClient",
        KIND_VECTOR,
        "qdrant",
        collection_keys=("collection_name", "collection"),
    ),
    AiConstructorSpec(
        "Qdrant",
        KIND_VECTOR,
        "qdrant",
        collection_keys=("collection_name", "collection"),
    ),
    AiConstructorSpec(
        "WeaviateVectorStore",
        KIND_VECTOR,
        "weaviate",
        collection_keys=("index_name", "collection_name", "class_name"),
    ),
    AiConstructorSpec(
        "WeaviateClient",
        KIND_VECTOR,
        "weaviate",
        collection_keys=("index_name", "collection_name", "class_name"),
    ),
    AiConstructorSpec(
        "Weaviate",
        KIND_VECTOR,
        "weaviate",
        collection_keys=("index_name", "collection_name", "class_name"),
    ),
    AiConstructorSpec(
        "Chroma",
        KIND_VECTOR,
        "chroma",
        collection_keys=("collection_name", "name"),
    ),
    AiConstructorSpec(
        "ChromaVectorStore",
        KIND_VECTOR,
        "chroma",
        collection_keys=("collection_name", "name"),
    ),
    AiConstructorSpec(
        "PersistentClient",
        KIND_VECTOR,
        "chroma",
        collection_keys=("collection_name", "name"),
    ),
    AiConstructorSpec("FAISS", KIND_VECTOR, "faiss", collection_keys=("index_name",)),
    AiConstructorSpec(
        "FaissVectorStore",
        KIND_VECTOR,
        "faiss",
        collection_keys=("index_name",),
    ),
    AiConstructorSpec("Faiss", KIND_VECTOR, "faiss", collection_keys=("index_name",)),
    AiConstructorSpec(
        "PGVector",
        KIND_VECTOR,
        "pgvector",
        collection_keys=("collection_name", "name"),
    ),
    AiConstructorSpec(
        "PGVectorStore",
        KIND_VECTOR,
        "pgvector",
        collection_keys=("collection_name", "name"),
    ),
    AiConstructorSpec(
        "PostgresVectorStore",
        KIND_VECTOR,
        "pgvector",
        collection_keys=("collection_name", "name"),
    ),
)

CONSTRUCTORS_BY_NAME: dict[str, AiConstructorSpec] = {
    spec.name: spec
    for spec in (*LLM_CONSTRUCTORS, *EMBEDDING_CONSTRUCTORS, *VECTOR_STORE_CONSTRUCTORS)
}


def _resolve_spec(func: ast.expr) -> tuple[AiConstructorSpec | None, str | None]:
    """Match a Call target to a constructor row plus the class method used, if any."""
    name = call_function_name(func)
    if name and name in CONSTRUCTORS_BY_NAME:
        return CONSTRUCTORS_BY_NAME[name], None
    if (
        isinstance(func, ast.Attribute)
        and func.attr in VECTOR_CLASS_METHODS
        and isinstance(func.value, ast.Name)
    ):
        return CONSTRUCTORS_BY_NAME.get(func.value.id), func.attr
    return None, None


def _static_string(
    call: ast.Call, keys: tuple[str, ...], positional: bool
) -> tuple[str | None, ast.expr | None]:
    """Return (literal, expr) for a constructor identity argument."""
    position = 0 if positional else None
    node = named_or_positional(call, keys, position)
    if node is None:
        return None, None
    literal = literal_string(node)
    return literal, node


def _llm_node_id(provider: str, model: str | None, dynamic: bool) -> str:
    """Stable llm id. Dynamic models use a sentinel, never an env value."""
    if model and not dynamic:
        return make_llm_id(model, provider)
    if dynamic:
        return make_llm_id("dynamic", provider)
    return make_node_id(NodeType.LLM, provider)


def _embedding_node_id(provider: str, model: str | None, dynamic: bool) -> str:
    """Stable embedding_model id. Dynamic names are not guessed."""
    if model and not dynamic:
        return make_embedding_model_id(model)
    if dynamic:
        return make_embedding_model_id(f"{provider}:dynamic")
    return make_embedding_model_id(provider)


@dataclass
class ConstructorDetector:
    """Emit llm / embedding_model / vector_store nodes from constructor Calls."""

    module: str
    file_path: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _edge_keys: set[tuple[str, str, str]] = field(default_factory=set)
    class_stack: list[str] = field(default_factory=list)
    func_stack: list[str] = field(default_factory=list)

    def detect(self, tree: ast.AST) -> ParseResult:
        """Walk definitions so constructors can attach to enclosing functions."""
        self.visit(tree)
        return ParseResult(
            nodes=list(self.nodes.values()),
            edges=self.edges,
            warnings=self.warnings,
        )

    def visit(self, node: ast.AST) -> None:
        """Dispatch like NodeVisitor without inheriting generic_visit recursion bugs."""
        method = getattr(self, f"visit_{type(node).__name__}", self.generic_visit)
        method(node)

    def generic_visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        spec, method = _resolve_spec(node.func)
        if spec is not None:
            self._emit(spec, node, method)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def _current_function_id(self) -> str | None:
        if not self.func_stack:
            return None
        qualified = ".".join([*self.class_stack, *self.func_stack])
        return make_python_function_id(self.module, qualified)

    def _add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return node
        self.nodes[node.id] = existing.merge(node)
        return self.nodes[node.id]

    def _add_edge(self, edge: Edge) -> None:
        if edge.key in self._edge_keys:
            return
        self._edge_keys.add(edge.key)
        self.edges.append(edge)

    def _ensure_function(self) -> str | None:
        """Register the enclosing function node and return its id, if any."""
        func_id = self._current_function_id()
        if func_id is None:
            return None
        qualified = ".".join([*self.class_stack, *self.func_stack])
        self._add_node(
            Node(
                id=func_id,
                name=self.func_stack[-1],
                type=NodeType.PYTHON_FUNCTION,
                file_path=self.file_path,
                metadata={"qualified_name": qualified},
            )
        )
        return func_id

    def _link(
        self,
        other_id: str,
        edge_type: EdgeType,
        evidence: str,
        line: int | None,
        *,
        produced: bool,
    ) -> None:
        """Connect the enclosing function to `other_id` in the locked direction.

        A model or store the function merely uses is an input, so it points at
        the function. An artifact the function writes points away from it.
        """
        func_id = self._ensure_function()
        if func_id is None:
            return
        source, target = (func_id, other_id) if produced else (other_id, func_id)
        self._add_edge(
            Edge(
                source=source,
                target=target,
                type=edge_type,
                confidence=Confidence.MEDIUM,
                evidence=evidence,
                metadata=evidence_metadata(
                    file_path=self.file_path,
                    line_number=line if isinstance(line, int) else None,
                ),
            )
        )

    def _emit(self, spec: AiConstructorSpec, call: ast.Call, method: str | None) -> None:
        line = getattr(call, "lineno", None)
        if spec.kind == KIND_LLM:
            self._emit_llm(spec, call, line)
        elif spec.kind == KIND_EMBEDDING:
            self._emit_embedding(spec, call, line)
        else:
            self._emit_vector(spec, call, line, method)

    def _emit_llm(self, spec: AiConstructorSpec, call: ast.Call, line: int | None) -> None:
        model, expr = _static_string(call, spec.model_keys, spec.model_positional)
        dynamic = expr is not None and model is None
        node_id = _llm_node_id(spec.provider, model, dynamic)
        metadata: dict[str, object] = {
            "provider": spec.provider,
            "constructor": spec.name,
        }
        if model is not None:
            metadata["model"] = model
        if dynamic:
            metadata["dynamic"] = True
            metadata["model_expr"] = expression_text(expr)
        name = model or spec.name
        self._add_node(
            Node(
                id=node_id,
                name=name,
                type=NodeType.LLM,
                file_path=self.file_path,
                line_number=line if line and line > 0 else None,
                metadata=metadata,
            )
        )
        self._link(node_id, EdgeType.INVOKED_BY, "llm_constructor", line, produced=False)

    def _emit_embedding(
        self, spec: AiConstructorSpec, call: ast.Call, line: int | None
    ) -> None:
        model, expr = _static_string(call, spec.model_keys, spec.model_positional)
        dynamic = expr is not None and model is None
        node_id = _embedding_node_id(spec.provider, model, dynamic)
        metadata: dict[str, object] = {
            "provider": spec.provider,
            "constructor": spec.name,
        }
        if model is not None:
            metadata["model"] = model
        if dynamic:
            metadata["dynamic"] = True
            metadata["model_expr"] = expression_text(expr)
        self._add_node(
            Node(
                id=node_id,
                name=model or spec.name,
                type=NodeType.EMBEDDING_MODEL,
                file_path=self.file_path,
                line_number=line if line and line > 0 else None,
                metadata=metadata,
            )
        )
        self._link(node_id, EdgeType.EMBEDS_WITH, "embedding_constructor", line, produced=False)

    def _emit_vector(
        self, spec: AiConstructorSpec, call: ast.Call, line: int | None, method: str | None
    ) -> None:
        store_id = make_vector_store_id(spec.provider)
        store_meta: dict[str, object] = {
            "vendor": spec.provider,
            "constructor": spec.name,
        }
        collection, expr = _static_string(call, spec.collection_keys, False)
        if expr is not None and collection is None:
            store_meta["collection_dynamic"] = True
            store_meta["collection_expr"] = expression_text(expr)
        self._add_node(
            Node(
                id=store_id,
                name=spec.provider,
                type=NodeType.VECTOR_STORE,
                file_path=self.file_path,
                line_number=line if line and line > 0 else None,
                metadata=store_meta,
            )
        )
        # The store itself is infrastructure the function depends on either way.
        self._link(
            store_id,
            EdgeType.RETRIEVES_FROM,
            "vector_store_constructor",
            line,
            produced=False,
        )
        if collection is None:
            return
        writes = method in VECTOR_WRITE_METHODS
        collection_id = make_vector_collection_id(collection)
        self._add_node(
            Node(
                id=collection_id,
                name=collection,
                type=NodeType.VECTOR_COLLECTION,
                file_path=self.file_path,
                line_number=line if line and line > 0 else None,
                metadata={"vendor": spec.provider, "collection": collection},
            )
        )
        self._add_edge(
            Edge(
                source=store_id,
                target=collection_id,
                type=EdgeType.FEEDS,
                confidence=Confidence.HIGH,
                evidence="vector_store_constructor",
                metadata=evidence_metadata(
                    file_path=self.file_path,
                    line_number=line if isinstance(line, int) else None,
                    reference=collection,
                ),
            )
        )
        self._link(
            collection_id,
            EdgeType.FEEDS if writes else EdgeType.RETRIEVES_FROM,
            "vector_collection_write" if writes else "vector_collection_read",
            line,
            produced=writes,
        )
