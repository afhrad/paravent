"""Compendium read routes.

Thin by design — CLAUDE.md keeps business logic out of routers. These endpoints exist to
prove the store is reachable and to back the search popups; the HTMX fragment routes and
the tag-renderer preview endpoint come later.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.dependencies import CompendiumDep
from app.store.errors import EntryNotFound

router = APIRouter(prefix="/api/compendium", tags=["compendium"])


class EntrySummary(BaseModel):
    """The shape the search popup renders for a hit.

    ``source`` is not optional in the UI: the corpus carries the same name in several
    books (Fire Bolt appears in both PHB and XPHB), so a result without its source is
    ambiguous.
    """

    key: str
    name: str
    source: str
    category: str


class SearchResponse(BaseModel):
    """A search result set."""

    query: str
    total: int
    results: list[EntrySummary] = Field(default_factory=list)


class CategoryInfo(BaseModel):
    """One category and how much of it is loaded."""

    name: str
    count: int
    sources: list[str]


class LoadStatus(BaseModel):
    """What the last compendium load produced."""

    total: int
    files_read: int
    loaded: dict[str, int]
    errors: list[str]
    unresolved_copies: list[str]
    unknown_copy_modes: list[str]
    ok: bool


def _summarise(category: str, model: Any) -> EntrySummary:
    """Reduce a model to the fields a search result needs."""
    return EntrySummary(
        key=str(getattr(model, "key", model.name)),
        name=str(model.name),
        source=str(getattr(model, "source", "")),
        category=category,
    )


@router.get("/status", response_model=LoadStatus)
async def load_status(compendium: CompendiumDep) -> LoadStatus:
    """Report what the last load produced, including anything it rejected."""
    report = compendium.report
    return LoadStatus(
        total=report.total,
        files_read=report.files_read,
        loaded=report.loaded,
        errors=report.errors,
        unresolved_copies=report.unresolved_copies,
        unknown_copy_modes=sorted(report.unknown_copy_modes),
        ok=report.ok,
    )


@router.get("/categories", response_model=list[CategoryInfo])
async def list_categories(compendium: CompendiumDep) -> list[CategoryInfo]:
    """Every loaded category with its entry count and source books."""
    return [
        CategoryInfo(
            name=name,
            count=compendium.count(name),
            sources=compendium.sources(name),
        )
        for name in compendium.categories()
    ]


@router.get("/search", response_model=SearchResponse)
async def search_everything(
    compendium: CompendiumDep,
    q: str = Query("", description="Substring to match against entry names"),
    limit: int = Query(5, ge=1, le=50, description="Maximum hits per category"),
) -> SearchResponse:
    """Search every category at once, for the global command palette."""
    hits = compendium.search_all(q, limit_per_category=limit)
    results = [_summarise(category, model) for category, models in hits.items() for model in models]
    return SearchResponse(query=q, total=len(results), results=results)


@router.get("/{category}", response_model=SearchResponse)
async def search_category(
    category: str,
    compendium: CompendiumDep,
    q: str = Query("", description="Substring to match against entry names"),
    limit: int = Query(25, ge=1, le=200),
) -> SearchResponse:
    """Search one category."""
    try:
        models = compendium.search(category, q, limit=limit)
    except EntryNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown category {category!r}"
        ) from exc
    return SearchResponse(
        query=q,
        total=len(models),
        results=[_summarise(category, m) for m in models],
    )


@router.get("/{category}/{key}")
async def get_entry(category: str, key: str, compendium: CompendiumDep) -> dict[str, Any]:
    """Fetch one fully resolved entry by its ``name|source`` key.

    Returns the raw model dump rather than a narrowed response schema: the statblock
    renderer needs every field the entry carries, including the ones we have not
    modelled explicitly.
    """
    try:
        model = compendium.get(category, key)
    except EntryNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return model.model_dump(mode="json", exclude_none=True)
