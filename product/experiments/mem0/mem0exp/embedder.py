"""The deterministic local embedder both sides of the T066 comparison share.

There is no model provider in this environment (and the platform's F09 path must
not need one to *store* a confirmed memory), so the experiment fixes a tiny,
fully specified embedder instead of a vendor model:

    tokenise     lower-case ``[a-z0-9]+`` runs, drop 1-character tokens, strip a
                 trailing ``s`` from words longer than three characters
    hash         sha256(token) -> index = first 4 bytes mod DIMENSIONS,
                 sign = +1/-1 from byte 4 (signed hashing)
    normalise    L2-normalise the resulting vector

This is deliberately weak: it is a *lower bound* on retrieval quality that both
systems share, so any recall difference is attributable to the memory system, not
to one side secretly using a better model. ``LocalEmbeddingServer`` exposes the
same function through an OpenAI-compatible ``/v1/embeddings`` endpoint, which is
how mem0 is wired to it without monkeypatching mem0 internals.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSIONS = 64
MODEL_NAME = "t066-hashed-bow-v1"

_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens with a deliberately simple plural rule."""
    tokens: list[str] = []
    for word in _WORD.findall(text.lower()):
        if len(word) < 2:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        tokens.append(word)
    return tokens


def embed(text: str) -> list[float]:
    """Return the deterministic unit vector for ``text``."""
    vector = [0.0] * DIMENSIONS
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSIONS
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"vector length mismatch: {len(left)} != {len(right)}")
    return sum(a * b for a, b in zip(left, right))


def rank(query: str, candidates: dict[str, list[float]], top_k: int = 3) -> list[str]:
    """Rank candidate keys by cosine similarity; ties break on key for determinism."""
    query_vector = embed(query)
    scored = [(-cosine(query_vector, vector), key) for key, vector in candidates.items()]
    scored.sort()
    return [key for _score, key in scored[:top_k]]


def profile() -> dict:
    """The versioned description recorded in the evidence file."""
    return {
        "model": MODEL_NAME,
        "dimensions": DIMENSIONS,
        "algorithm": "signed sha256 hashing of lower-cased tokens, L2-normalised",
        "tokenizer": "lower-case [a-z0-9]+, length>=2, trailing 's' stripped for len>3",
        "local": True,
        "provider_calls": 0,
    }


class LocalEmbeddingServer:
    """A tiny OpenAI-compatible embeddings endpoint serving :func:`embed`.

    mem0 is configured with ``provider=openai`` and this server's ``base_url``, so
    the SDK's supported HTTP path is exercised and no mem0 internals are patched.
    It listens on 127.0.0.1 with an ephemeral port and counts requests so the
    evidence can show the embedder really was on the call path.
    """

    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.requests = 0

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("embedding server is not running")
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def __enter__(self) -> "LocalEmbeddingServer":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    inputs = payload.get("input", [])
                    if isinstance(inputs, str):
                        inputs = [inputs]
                    if not isinstance(inputs, list):
                        raise ValueError("input must be a string or list")
                    data = [
                        {"object": "embedding", "index": index, "embedding": embed(str(text))}
                        for index, text in enumerate(inputs)
                    ]
                    body = json.dumps(
                        {"object": "list", "data": data, "model": payload.get("model", MODEL_NAME)}
                    ).encode("utf-8")
                except Exception as error:  # pragma: no cover - defensive
                    body = json.dumps({"error": {"message": str(error)}}).encode("utf-8")
                    self.send_response(400)
                else:
                    owner.requests += 1
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:  # keep the evidence output quiet
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
