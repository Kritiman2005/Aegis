"""
Aegis — Database Engine Catalog

The Marketplace's Databases category (app.api.marketplace_databases) lists
this catalog directly; the workflow canvas's database/vector node config
panels read it too, to offer the same engine choice inline. See this
package's __init__ for the overall design.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class EngineSpec:
    id: str
    name: str
    category: str  # "relational" | "vector"
    description: str
    pip_package: Optional[str]  # None => built-in, runs in-process, no download
    placeholder_syntax: str     # named-parameter style shown in the config UI


RELATIONAL_ENGINES: List[EngineSpec] = [
    EngineSpec(
        id="sqlite",
        name="SQLite",
        category="relational",
        description="Lightweight embedded SQL database. Already built into Aegis — nothing to download.",
        pip_package=None,
        placeholder_syntax=":name",
    ),
    EngineSpec(
        id="duckdb",
        name="DuckDB",
        category="relational",
        description="Embedded analytical (OLAP) SQL database, single file. Good fit for aggregation-heavy queries.",
        pip_package="duckdb",
        placeholder_syntax="$name",
    ),
]

VECTOR_ENGINES: List[EngineSpec] = [
    EngineSpec(
        id="qdrant",
        name="Qdrant",
        category="vector",
        description="Embedded vector database — the same engine Aegis's own document search already runs on.",
        pip_package=None,
        placeholder_syntax="n/a",
    ),
    EngineSpec(
        id="lancedb",
        name="LanceDB",
        category="vector",
        description="Embedded, file-based vector database built on Apache Arrow.",
        pip_package="lancedb",
        placeholder_syntax="n/a",
    ),
    EngineSpec(
        id="chromadb",
        name="Chroma",
        category="vector",
        description="Embedded vector database with a simple Python-native API.",
        pip_package="chromadb",
        placeholder_syntax="n/a",
    ),
]

ENGINES: List[EngineSpec] = RELATIONAL_ENGINES + VECTOR_ENGINES
_BY_ID = {e.id: e for e in ENGINES}


def get_engine(engine_id: str) -> Optional[EngineSpec]:
    return _BY_ID.get(engine_id)


def list_engines() -> List[EngineSpec]:
    return list(ENGINES)
