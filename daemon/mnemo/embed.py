"""Embedding: chunk node bodies and produce 384-dim vectors via MiniLM.

The :class:`Embedder` is intentionally lazy — the heavy ``SentenceTransformer``
load only happens on first ``embed_text`` / ``embed_batch`` call, so importing
this module is cheap.

Chunking strategy (``chunk_body``):

1. Split on markdown ``##`` / ``###`` heading boundaries (keep the heading line
   with its section).
2. If a section still exceeds ``max_tokens``, split on paragraph (blank-line)
   boundaries and pack consecutive paragraphs up to the budget, with one-block
   overlap between adjacent chunks for context continuity.
3. Token counting uses an approximation (1.333 * word count) by default, or
   the supplied ``tokenizer`` callable when one is provided. The MiniLM context
   window is 256 tokens; we set ``max_tokens=512`` to be safe with the rough
   counter (sentence-transformers will truncate internally if needed).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mnemo import paths
from mnemo.store import Node, Store

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_DIM = 384


class Embedder:
    """Lazy wrapper around a sentence-transformers model.

    The model is loaded on first encode call and cached for the process
    lifetime. Initial download (~22 MB for MiniLM-L6) happens to
    ``cache_dir`` (default: ``paths.cache_dir()``).
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        cache_dir: Path | None = None,
    ) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir or paths.cache_dir()
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            # Local import: keep top-of-module import cost low.
            import sentence_transformers

            self._cache_dir.mkdir(parents=True, exist_ok=True)
            log.info("loading embedding model %s", self.model_name)
            self._model = sentence_transformers.SentenceTransformer(
                self.model_name,
                cache_folder=str(self._cache_dir),
            )
        return self._model

    @property
    def dim(self) -> int:
        model = self._load()
        # ``get_embedding_dimension`` is the >=2.4 name; older releases used
        # ``get_sentence_embedding_dimension``. Support both.
        getter = (
            getattr(model, "get_embedding_dimension", None)
            or model.get_sentence_embedding_dimension
        )
        return int(getter())

    def embed_text(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        # normalize_embeddings=True so cosine similarity == dot product, and
        # so sqlite-vec's L2 distance is monotonic in cosine distance.
        vectors = model.encode(  # type: ignore[union-attr]
            texts,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return vectors.tolist()


# --- Chunking --------------------------------------------------------------


TokenCounter = Callable[[str], int]


def _approx_tokens(text: str) -> int:
    # 1 word ~ 1.333 tokens for English markdown.
    return int(len(text.split()) * 1.333) + 1


def _split_by_headings(body: str) -> list[str]:
    """Split markdown into sections by ``##`` and ``###`` headings.

    Each section starts with its heading line and runs until the next heading
    of equal or higher level. ``#`` (level-1) headings are not used as cut
    points: they're typically the document title and we keep the section body
    intact under them.
    """
    lines = body.splitlines(keepends=True)
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("## ", "### ")) and current:
            sections.append("".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("".join(current))
    return sections if sections else [body]


def chunk_body(
    body: str,
    *,
    max_tokens: int = 512,
    overlap_blocks: int = 1,
    count: TokenCounter | None = None,
) -> list[str]:
    """Split ``body`` into chunks of <= ``max_tokens`` tokens.

    ``overlap_blocks`` is the number of trailing paragraphs from the previous
    chunk to repeat at the start of the next when paragraph-packing kicks in.
    """
    counter = count or _approx_tokens
    body = body.strip()
    if not body:
        return []

    sections = _split_by_headings(body)
    chunks: list[str] = []
    for section in sections:
        section_text = section.strip()
        if not section_text:
            continue
        if counter(section_text) <= max_tokens:
            chunks.append(section_text)
            continue
        # Paragraph-pack within the section.
        paragraphs = [p for p in section_text.split("\n\n") if p.strip()]
        current: list[str] = []
        current_tokens = 0
        for p in paragraphs:
            p_tokens = counter(p)
            if current and current_tokens + p_tokens > max_tokens:
                chunks.append("\n\n".join(current))
                # Carry overlap into next chunk
                current = current[-overlap_blocks:] if overlap_blocks > 0 else []
                current_tokens = sum(counter(c) for c in current)
            current.append(p)
            current_tokens += p_tokens
        if current:
            chunks.append("\n\n".join(current))

    return chunks


# --- Node-level embedding --------------------------------------------------


def embed_node(store: Store, node: Node, embedder: Embedder) -> int:
    """Chunk + embed + persist for one node. Returns the chunk count.

    If the body is empty (rare; an entry with only frontmatter) we fall back to
    embedding the description so the node is still searchable.
    """
    chunks = chunk_body(node.body)
    if not chunks:
        fallback = node.description or node.name
        if not fallback:
            return 0
        chunks = [fallback]

    vectors = embedder.embed_batch(chunks)
    payload = [
        (idx, vec, text) for idx, (vec, text) in enumerate(zip(vectors, chunks, strict=True))
    ]
    store.upsert_chunks(node.id, payload)
    return len(chunks)


def embed_all_unembedded(store: Store, embedder: Embedder, *, batch_log_every: int = 25) -> int:
    """Embed every node in the store that doesn't yet have chunks.

    Returns the number of nodes embedded.
    """
    embedded = store.list_embedded_node_ids()
    nodes = store.list_nodes(limit=1_000_000)
    todo = [n for n in nodes if n.id not in embedded]
    for i, node in enumerate(todo, start=1):
        embed_node(store, node, embedder)
        if i % batch_log_every == 0:
            log.info("embedded %d / %d", i, len(todo))
    return len(todo)
