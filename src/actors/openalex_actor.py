
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import requests
import ray

from src.config.settings import (
    OPEN_ALEX_API,
    OPEN_ALEX_EMAIL,
)

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / "paper_my" / "cache" / "openalex_verification_cache.json"


def _sim_to_confidence(sim: float) -> str:
    """Map a similarity score to a confidence label."""
    if sim >= 0.85:
        return "high"
    if sim >= 0.70:
        return "medium"
    return "low"


def _row_status(result: dict) -> str:
    """
    Derive a human-readable status string from a verify_citation result.

      verified        — DOI found, title similarity ≥ 0.85
      doi_mismatch    — DOI found, title similarity < 0.85
      doi_not_found   — HTTP error on DOI lookup
    """
    if not result.get("doi_valid"):
        return "doi_not_found"
    if result.get("title_ok") is False:
        return "doi_mismatch"
    if result.get("title_ok") is True:
        return "verified"
    return "doi_only"   # DOI resolved, no title provided for comparison


@ray.remote(max_concurrency=8, max_restarts=1)
class OpenAlexActor:

    def __init__(
            self,
            base_url="https://api.openalex.org",
            timeout=60,
            cache_path: Path | None = None,
    ):

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.api_key = OPEN_ALEX_API

        self.session = requests.Session()

        contact = OPEN_ALEX_EMAIL
        self.headers = {
            "User-Agent":
                f"GeoHydroAI/1.0 ({contact})" if contact else "GeoHydroAI/1.0"
        }

        # mailto/api_key only when configured — empty params confuse the API
        self.params = {}
        if self.api_key:
            self.params["api_key"] = self.api_key
        if contact:
            self.params["mailto"] = contact

        # Disk-backed DOI cache — keyed by clean_doi, value is the raw work dict.
        self._cache_path: Path = Path(cache_path) if cache_path else _DEFAULT_CACHE_PATH
        self._cache: dict = self._load_cache()

    # ---------------------------------------------------------
    # CACHE
    # ---------------------------------------------------------

    def _load_cache(self) -> dict:
        if self._cache_path.exists():
            try:
                return json.loads(self._cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---------------------------------------------------------
    # CORE REQUEST
    # ---------------------------------------------------------

    def _get(
            self,
            endpoint: str,
            params: dict | None = None,
    ) -> dict:

        merged_params = dict(self.params)

        if params:
            merged_params.update(params)

        response = self.session.get(
            f"{self.base_url}/{endpoint.lstrip('/')}",
            headers=self.headers,
            params=merged_params,
            timeout=self.timeout,
        )

        response.raise_for_status()

        return response.json()

    # ---------------------------------------------------------
    # FETCH PRIMITIVES — each makes exactly one HTTP request
    # ---------------------------------------------------------

    def get_work_by_doi(
            self,
            doi: str,
    ) -> dict:
        """Fetch a work by DOI.  Result is cached in *cache_path* by clean DOI."""
        clean = self._clean_doi(doi)
        if clean in self._cache:
            return self._cache[clean]
        result = self._get(f"works/doi:{clean}")
        self._cache[clean] = result
        self._save_cache()
        return result

    def get_work_by_id(
            self,
            openalex_id: str,
    ) -> dict:
        """Fetch a work by OpenAlex ID (W-prefixed or full URL)."""
        if openalex_id.startswith("https://openalex.org/"):
            openalex_id = openalex_id.split("/")[-1]

        return self._get(f"works/{openalex_id}")

    def get_author_by_id(
            self,
            author_id: str,
    ) -> dict:
        """Fetch an author record by OpenAlex author ID."""
        if author_id.startswith("https://openalex.org/"):
            author_id = author_id.split("/")[-1]

        return self._get(f"authors/{author_id}")

    # ---------------------------------------------------------
    # SEARCH
    # ---------------------------------------------------------

    def search_works(
            self,
            query: str,
            limit: int = 25,
    ) -> list:

        data = self._get(
            "works",
            params={
                "search": query,
                "per-page": limit,
            },
        )

        return data.get("results", [])

    # ---------------------------------------------------------
    # EXTRACT FUNCTIONS — operate on an already-fetched work dict
    # Call these instead of re-fetching by DOI.
    # ---------------------------------------------------------

    @staticmethod
    def extract_authors(work: dict) -> list:
        """Project authorships from a fetched work object."""
        result = []

        for auth in work.get("authorships", []):
            author = auth.get("author", {})

            result.append({
                "id":
                    author.get("id"),

                "name":
                    author.get("display_name"),

                "orcid":
                    author.get("orcid"),

                "author_position":
                    auth.get("author_position"),

                "is_corresponding":
                    auth.get("is_corresponding", False),

                "institutions": [
                    {
                        "id":           inst.get("id"),
                        "name":         inst.get("display_name"),
                        "country_code": inst.get("country_code"),
                    }
                    for inst in auth.get("institutions", [])
                ],
            })

        return result

    @staticmethod
    def extract_topics(work: dict) -> list:
        """Project topic scores from a fetched work object."""
        return [
            {
                "id":    t.get("id"),
                "name":  t.get("display_name"),
                "score": t.get("score"),
            }
            for t in work.get("topics", [])
        ]

    @staticmethod
    def extract_institutions(work: dict) -> list:
        """Deduplicated institution list across all authorships."""
        seen: set = set()
        institutions = []

        for auth in work.get("authorships", []):
            for inst in auth.get("institutions", []):
                iid = inst.get("id")

                if iid and iid not in seen:
                    seen.add(iid)
                    institutions.append({
                        "id":           iid,
                        "name":         inst.get("display_name"),
                        "country_code": inst.get("country_code"),
                        "type":         inst.get("type"),
                    })

        return institutions

    # ---------------------------------------------------------
    # NORMALIZED GRAPH ENTITY — single HTTP request per DOI
    # ---------------------------------------------------------

    def build_graph_entity(
            self,
            doi: str,
    ) -> dict:

        work = self.get_work_by_doi(doi)

        return {

            "openalex_id":
                work.get("id"),

            "doi":
                work.get("doi"),

            "title":
                work.get("title"),

            "publication_year":
                work.get("publication_year"),

            "cited_by_count":
                work.get("cited_by_count"),

            "authors":
                self.extract_authors(work),

            "topics":
                self.extract_topics(work),

            "institutions":
                self.extract_institutions(work),

            "referenced_works":
                work.get("referenced_works", []),

            "related_works":
                work.get("related_works", []),
        }

    # ---------------------------------------------------------
    # VERIFICATION — DOI, title, authors
    # ---------------------------------------------------------

    # -- internal helpers ------------------------------------

    @staticmethod
    def _clean_doi(doi: str) -> str:
        """Normalise a DOI to bare 10.xxxx/... form (lower-case, no prefix URL)."""
        doi = doi.strip().lower()
        doi = re.sub(r'^https?://doi\.org/', '', doi)
        doi = re.sub(r'^doi:', '', doi)
        return doi

    @staticmethod
    def _normalise_text(text: str) -> str:
        """Lower-case, strip accents, collapse whitespace — for fuzzy comparison."""
        text = unicodedata.normalize("NFD", text)
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")
        text = text.lower()
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """SequenceMatcher ratio on normalised titles (0.0–1.0)."""
        if not a or not b:
            return 0.0
        na = OpenAlexActor._normalise_text(a)
        nb = OpenAlexActor._normalise_text(b)
        return SequenceMatcher(None, na, nb).ratio()

    @staticmethod
    def _extract_last_name(full_name: str) -> str:
        """Return the last token before any comma, or the last token overall."""
        full_name = full_name.strip()
        if ',' in full_name:
            return full_name.split(',')[0].strip().lower()
        parts = full_name.split()
        return parts[-1].lower() if parts else full_name.lower()

    @staticmethod
    def _author_match_score(expected_names: list[str], openalex_authors: list[dict]) -> dict:
        """
        Compare a list of expected author name strings against OpenAlex author records.

        Matching logic (per expected name):
          1. Extract last name from expected string.
          2. Find any OpenAlex author whose display_name contains that last name.
          3. Mark as matched if found; unmatched otherwise.

        Returns:
            matched   — expected names successfully found in OpenAlex
            unmatched — expected names not found
            extra     — OpenAlex authors not mentioned in expected list
            score     — fraction of expected names that were matched (0.0–1.0)
        """
        oa_last_names = [
            OpenAlexActor._extract_last_name(a.get("name") or "")
            for a in openalex_authors
        ]

        matched, unmatched = [], []
        matched_oa_indices: set[int] = set()

        for name in expected_names:
            last = OpenAlexActor._extract_last_name(name)
            found_idx = next(
                (i for i, oa_last in enumerate(oa_last_names) if last and last in oa_last),
                None,
            )
            if found_idx is not None:
                matched.append(name)
                matched_oa_indices.add(found_idx)
            else:
                unmatched.append(name)

        extra = [
            openalex_authors[i].get("name")
            for i in range(len(openalex_authors))
            if i not in matched_oa_indices
        ]

        score = len(matched) / len(expected_names) if expected_names else 1.0

        return {
            "matched":   matched,
            "unmatched": unmatched,
            "extra":     extra,
            "score":     round(score, 3),
        }

    # -- public verification methods -------------------------

    def verify_doi(self, doi: str) -> dict:
        """
        Check whether a DOI resolves in OpenAlex.

        Returns:
            found          — True if OpenAlex returned a work
            canonical_doi  — DOI as stored by OpenAlex (may differ in casing/prefix)
            openalex_id    — OpenAlex work ID (W-prefixed)
            title          — title from OpenAlex
            year           — publication year
            cited_by_count — citation count
            error          — error message if found=False
        """
        cleaned = self._clean_doi(doi)
        try:
            work = self.get_work_by_doi(cleaned)
            return {
                "found":          True,
                "canonical_doi":  work.get("doi"),
                "openalex_id":    work.get("id"),
                "title":          work.get("title"),
                "year":           work.get("publication_year"),
                "cited_by_count": work.get("cited_by_count"),
                "error":          None,
            }
        except requests.HTTPError as exc:
            return {
                "found":          False,
                "canonical_doi":  None,
                "openalex_id":    None,
                "title":          None,
                "year":           None,
                "cited_by_count": None,
                "error":          str(exc),
            }

    def verify_title(
            self,
            title: str,
            *,
            doi: str | None = None,
            limit: int = 5,
    ) -> dict:
        """
        Verify a paper title against OpenAlex.

        If *doi* is given, fetches that work and compares the title directly —
        one HTTP request, most accurate.

        Without a DOI, searches OpenAlex by the title string and returns the
        best-matching result from the top *limit* candidates.

        Returns:
            matched       — True when best similarity ≥ 0.85
            similarity    — SequenceMatcher ratio against the best candidate (0.0–1.0)
            best_title    — title from OpenAlex for the best match
            best_doi      — DOI of the best match
            best_year     — publication year of the best match
            search_used   — True when title-search fallback was used
        """
        if doi:
            cleaned = self._clean_doi(doi)
            try:
                work = self.get_work_by_doi(cleaned)
                oa_title = work.get("title") or ""
                sim = self._title_similarity(title, oa_title)
                return {
                    "matched":     sim >= 0.85,
                    "similarity":  round(sim, 3),
                    "confidence":  _sim_to_confidence(sim),
                    "best_title":  oa_title,
                    "best_doi":    work.get("doi"),
                    "best_year":   work.get("publication_year"),
                    "search_used": False,
                }
            except requests.HTTPError:
                pass  # DOI not found — fall through to title search

        # Title-search fallback (no DOI supplied or DOI not found)
        candidates = self.search_works(title, limit=limit)
        best_sim, best_work = 0.0, None
        for candidate in candidates:
            sim = self._title_similarity(title, candidate.get("title") or "")
            if sim > best_sim:
                best_sim, best_work = sim, candidate

        if best_work is None:
            return {
                "matched":     False,
                "similarity":  0.0,
                "confidence":  "low",
                "best_title":  None,
                "best_doi":    None,
                "best_year":   None,
                "search_used": True,
            }

        # Title-search matches are capped at "medium" even when similarity is high,
        # because there is no confirmed DOI to anchor the result.
        raw_conf = _sim_to_confidence(best_sim)
        capped_conf = "medium" if raw_conf == "high" else raw_conf

        return {
            "matched":     best_sim >= 0.85,
            "similarity":  round(best_sim, 3),
            "confidence":  capped_conf,
            "best_title":  best_work.get("title"),
            "best_doi":    best_work.get("doi"),
            "best_year":   best_work.get("publication_year"),
            "search_used": True,
        }

    def verify_authors(
            self,
            doi: str,
            expected_names: list[str],
    ) -> dict:
        """
        Verify that *expected_names* appear among the authors of the work at *doi*.

        Returns the result of _author_match_score plus the full OpenAlex author list.
        Returns an error dict if the DOI cannot be fetched.
        """
        cleaned = self._clean_doi(doi)
        try:
            work = self.get_work_by_doi(cleaned)
        except requests.HTTPError as exc:
            return {
                "matched":   [],
                "unmatched": expected_names,
                "extra":     [],
                "score":     0.0,
                "openalex_authors": [],
                "error":     str(exc),
            }

        oa_authors = self.extract_authors(work)
        result     = self._author_match_score(expected_names, oa_authors)
        result["openalex_authors"] = [a.get("name") for a in oa_authors]
        result["error"] = None
        return result

    def verify_citation(
            self,
            doi: str,
            *,
            title: str | None = None,
            authors: list[str] | None = None,
    ) -> dict:
        """
        Combined one-pass verification: DOI + optional title + optional authors.

        Makes a single HTTP request when *doi* resolves successfully.

        Returns:
            doi_valid      — DOI found in OpenAlex
            title_match    — title similarity score (None if title not provided)
            title_ok       — True when similarity ≥ 0.85 (None if not checked)
            authors_score  — fraction of expected authors matched (None if not provided)
            authors_detail — matched/unmatched/extra lists (None if not provided)
            canonical_doi  — canonical DOI from OpenAlex
            openalex_id    — OpenAlex work ID
            year           — publication year
            error          — fetch error message, or None
        """
        cleaned = self._clean_doi(doi)
        try:
            work = self.get_work_by_doi(cleaned)
        except requests.HTTPError as exc:
            return {
                "doi_valid":      False,
                "title_match":    None,
                "title_ok":       None,
                "authors_score":  None,
                "authors_detail": None,
                "canonical_doi":  None,
                "openalex_id":    None,
                "year":           None,
                "error":          str(exc),
            }

        oa_title   = work.get("title") or ""
        oa_authors = self.extract_authors(work)

        title_sim = self._title_similarity(title, oa_title) if title else None
        author_result = (
            self._author_match_score(authors, oa_authors) if authors else None
        )

        return {
            "doi_valid":      True,
            "title_match":    round(title_sim, 3) if title_sim is not None else None,
            "title_ok":       title_sim >= 0.85 if title_sim is not None else None,
            "authors_score":  author_result["score"] if author_result else None,
            "authors_detail": author_result,
            "canonical_doi":  work.get("doi"),
            "openalex_id":    work.get("id"),
            "year":           work.get("publication_year"),
            "error":          None,
        }

    # ---------------------------------------------------------
    # BATCH VERIFICATION
    # ---------------------------------------------------------

    def verify_evidence_pack_references(
            self,
            evidence_pack_path: str | Path,
    ) -> "pd.DataFrame":
        """
        Verify every evidence item in a section_evidence_pack_v4.json file.

        For each item:
        - If DOI is present: calls verify_citation (DOI + title).
        - If DOI is absent but title is present: calls verify_title (title search, capped "medium").
        - If neither: marks as "no_identifier".

        Returns a DataFrame with columns:
            section_id | paper_id | doi | title | year
            doi_valid | title_score | title_confidence | authors_score | status
        """
        pack_raw = json.loads(Path(evidence_pack_path).read_text(encoding="utf-8"))
        pack = pack_raw if isinstance(pack_raw, list) else list(pack_raw.values())

        rows: list[dict] = []
        for section in pack:
            sid   = section.get("section_id", "unknown")
            items = section.get("evidence_items") or []
            for item in items:
                doi    = (item.get("doi") or "").strip()
                title  = (item.get("title") or "").strip()
                year   = item.get("year")
                pid    = item.get("paper_id", "")
                authors_raw = item.get("authors") or ""
                expected_names = [
                    a.strip() for a in re.split(r"[;,]", authors_raw) if a.strip()
                ][:3]   # first three authors is enough for matching

                row: dict = {
                    "section_id": sid,
                    "paper_id":   pid,
                    "doi":        doi or None,
                    "title":      title or None,
                    "year":       year,
                }

                if doi and doi.startswith("10."):
                    result = self.verify_citation(
                        doi, title=title or None,
                        authors=expected_names or None,
                    )
                    row.update({
                        "doi_valid":         result["doi_valid"],
                        "title_score":       result["title_match"],
                        "title_confidence":  _sim_to_confidence(result["title_match"] or 0)
                                             if result["doi_valid"] else "n/a",
                        "authors_score":     result["authors_score"],
                        "status":            _row_status(result),
                    })
                elif title:
                    result = self.verify_title(title)
                    row.update({
                        "doi_valid":         False,
                        "title_score":       result["similarity"],
                        "title_confidence":  result["confidence"],
                        "authors_score":     None,
                        "status":            "title_only_" + result["confidence"],
                    })
                else:
                    row.update({
                        "doi_valid":         False,
                        "title_score":       None,
                        "title_confidence":  None,
                        "authors_score":     None,
                        "status":            "no_identifier",
                    })

                rows.append(row)

        return pd.DataFrame(rows, columns=[
            "section_id", "paper_id", "doi", "title", "year",
            "doi_valid", "title_score", "title_confidence",
            "authors_score", "status",
        ])

    def verify_references_bibtex(
            self,
            bib_path: str | Path,
    ) -> "pd.DataFrame":
        """
        Verify references from a BibTeX file (.bib).

        Parses @article{...} entries; for each, extracts doi, title, author, year
        and runs the same DOI-first / title-fallback logic as
        verify_evidence_pack_references.

        Returns a DataFrame with columns:
            citation_key | doi | title | year
            doi_valid | title_score | title_confidence | authors_score | status
        """
        bib_text = Path(bib_path).read_text(encoding="utf-8", errors="replace")

        # Parse BibTeX entries with a minimal regex (handles most auto-generated .bib files)
        entry_pattern = re.compile(
            r'@\w+\{([^,]+),\s*(.*?)\n\}',
            re.DOTALL | re.IGNORECASE,
        )
        field_pattern = re.compile(
            r'(\w+)\s*=\s*\{([^}]*)\}',
            re.DOTALL,
        )

        rows: list[dict] = []
        for entry_match in entry_pattern.finditer(bib_text):
            key    = entry_match.group(1).strip()
            body   = entry_match.group(2)
            fields = {m.group(1).lower(): m.group(2).strip()
                      for m in field_pattern.finditer(body)}

            doi   = fields.get("doi", "").strip()
            title = fields.get("title", "").strip()
            year  = fields.get("year", "")
            author_raw = fields.get("author", "")
            expected_names = [
                a.strip() for a in re.split(r"[;,]| and ", author_raw) if a.strip()
            ][:3]

            row: dict = {
                "citation_key": key,
                "doi":          doi or None,
                "title":        title or None,
                "year":         year or None,
            }

            if doi and doi.startswith("10."):
                result = self.verify_citation(
                    doi, title=title or None,
                    authors=expected_names or None,
                )
                row.update({
                    "doi_valid":        result["doi_valid"],
                    "title_score":      result["title_match"],
                    "title_confidence": _sim_to_confidence(result["title_match"] or 0)
                                        if result["doi_valid"] else "n/a",
                    "authors_score":    result["authors_score"],
                    "status":           _row_status(result),
                })
            elif title:
                result = self.verify_title(title)
                row.update({
                    "doi_valid":        False,
                    "title_score":      result["similarity"],
                    "title_confidence": result["confidence"],
                    "authors_score":    None,
                    "status":           "title_only_" + result["confidence"],
                })
            else:
                row.update({
                    "doi_valid":        False,
                    "title_score":      None,
                    "title_confidence": None,
                    "authors_score":    None,
                    "status":           "no_identifier",
                })

            rows.append(row)

        return pd.DataFrame(rows, columns=[
            "citation_key", "doi", "title", "year",
            "doi_valid", "title_score", "title_confidence",
            "authors_score", "status",
        ])
