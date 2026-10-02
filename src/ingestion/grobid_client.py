"""
grobid_client.py — GROBID HTTP client with full coordinate + enrichment params.

Implements:
  - All required processFulltextDocument parameters (spec-compliant)
  - Correct teiCoordinates as repeated form fields (list of tuples)
  - HTTP 204 detection (empty parse — non-retriable)
  - HTTP 500 body parsing for structured GROBID error codes
  - HTTP 503 backoff per GROBID docs (5-10s wait)
  - Exponential backoff with per-error-class delays
  - Health check via /api/isalive

GROBID error policy (from official docs):
  200  → success
  204  → parsed but empty → skip (non-retriable)
  500  → parse error; body contains [ERROR_CODE] → classify and act
  503  → all engines busy → retry after 5-10s
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from src.ingestion.failure_types import FailureType, classify_500_body
from src.ingestion.models import GROBIDResponse

log = logging.getLogger(__name__)

# ── GROBID endpoints ──────────────────────────────────────────────────────────

GROBID_BASE            = "http://localhost:8070"
GROBID_FULLTEXT        = f"{GROBID_BASE}/api/processFulltextDocument"
GROBID_HEADER_NAMES    = f"{GROBID_BASE}/api/processHeaderNames"
GROBID_CITATION_NAMES  = f"{GROBID_BASE}/api/processCitationNames"
GROBID_AFFILIATIONS    = f"{GROBID_BASE}/api/processAffiliations"
GROBID_CITATION        = f"{GROBID_BASE}/api/processCitation"
GROBID_CITATION_LIST   = f"{GROBID_BASE}/api/processCitationList"
GROBID_REF_ANNOTATIONS = f"{GROBID_BASE}/api/referenceAnnotations"
GROBID_ANNOTATE_PDF    = f"{GROBID_BASE}/api/annotatePDF"      # experimental
GROBID_HEALTH          = f"{GROBID_BASE}/api/isalive"
GROBID_READY           = f"{GROBID_BASE}/api/health"

# ── Request parameters ────────────────────────────────────────────────────────
# teiCoordinates must be repeated — use list of tuples, NOT a dict.
# Preserving coordinates for figures/tables/formulas/refs/sentences/authors
# is mandatory for layout-aware KG downstream.

#: Header consolidation asks an external service (CrossRef, or biblio-glutton when
#: one is configured) to correct the header metadata. When that service is
#: unreachable from the container, GROBID does not fail — it blocks until its own
#: internal timeout, turning a 19-second parse into one that outlives any client
#: timeout and dies as a broken pipe. Measured 2026-09-17: 18.9 s with
#: consolidation off against >600 s with it on, for the same PDF.
#:
#: It stays on by default, because the existing corpus was built with it and it
#: genuinely improves headers. Set GROBID_CONSOLIDATE_HEADER=0 when the service is
#: unreachable, or when the metadata already comes from elsewhere — as it does for
#: harvested papers, whose header is known from OpenAlex before the PDF is fetched.
CONSOLIDATE_HEADER = os.getenv("GROBID_CONSOLIDATE_HEADER", "1")

GROBID_FORM_PARAMS: list[tuple[str, str]] = [
    ("consolidateHeader",    CONSOLIDATE_HEADER),
    ("consolidateCitations", "0"),   # skip — too slow for batch ingestion
    ("includeRawCitations",  "1"),   # raw ref strings for citation graph
    ("includeRawAffiliations", "1"), # raw affiliation strings
    ("segmentSentences",     "1"),   # sentence-level boundaries for RAG
    ("generateIDs",          "1"),   # stable TEI element IDs
    ("teiCoordinates",       "persName"),
    ("teiCoordinates",       "figure"),
    ("teiCoordinates",       "table"),
    ("teiCoordinates",       "ref"),
    ("teiCoordinates",       "biblStruct"),
    ("teiCoordinates",       "formula"),
    ("teiCoordinates",       "s"),   # sentence coordinates
]

# ── Timing constants ──────────────────────────────────────────────────────────

REQUEST_TIMEOUT    = 180    # seconds; large PDFs need up to 3 minutes
HEALTH_TIMEOUT     = 10
MAX_RETRIES        = 3

# Backoff bases per error class (seconds), per GROBID docs:
#   503 → "suggest 5–10 seconds"
#   500 → transient; shorter backoff
#   TIMEOUT → longer backoff
_BACKOFF: dict[FailureType, tuple[float, float]] = {
    # (base_sec, cap_sec)
    FailureType.HTTP_503:        (8.0,  60.0),
    FailureType.HTTP_500:        (4.0,  30.0),
    FailureType.TIMEOUT:         (10.0, 90.0),
    FailureType.CONNECTION_ERROR:(5.0,  30.0),
    FailureType.GROBID_TIMEOUT:  (15.0, 90.0),
}
_DEFAULT_BACKOFF = (3.0, 20.0)


class GROBIDClient:
    """
    Stateful GROBID HTTP client.

    One instance per pipeline run. Uses a single requests.Session with
    no internal connection-pool concurrency (pool_maxsize=1) because we
    run the pipeline sequentially. Reusing the session gives HTTP
    keep-alive for free.
    """

    def __init__(self, max_retries: int = MAX_RETRIES) -> None:
        self._max_retries = max_retries
        self._session     = self._build_session()

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        s.mount("http://", adapter)
        return s

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Health check ──────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        try:
            r = self._session.get(GROBID_HEALTH, timeout=HEALTH_TIMEOUT)
            return r.status_code == 200
        except Exception as exc:
            log.error("GROBID health check failed: %s", exc)
            return False

    # ── Name parsers ──────────────────────────────────────────────────────────

    def parse_names(self, raw: str, max_retries: int | None = None) -> str | None:
        """
        Parse raw author names from a paper header via /api/processHeaderNames.

        Returns a TEI XML fragment (one or more <persName> elements), or None.
        """
        return self._call_text_endpoint(GROBID_HEADER_NAMES, "names", raw, max_retries)

    def parse_citation_names(self, raw: str, max_retries: int | None = None) -> str | None:
        """
        Parse raw author names from a bibliographic reference via /api/processCitationNames.

        Handles initials, middle names, and comma/ampersand separated lists
        common in reference sections (e.g. "J. Doe, B. M. Jackson and A. Lee").

        Returns a TEI XML fragment (one or more <persName> elements), or None.
        """
        return self._call_text_endpoint(GROBID_CITATION_NAMES, "names", raw, max_retries)

    def parse_affiliations(self, raw: str, max_retries: int | None = None) -> str | None:
        """
        Parse a raw affiliation/address string via /api/processAffiliations.

        Returns a TEI XML fragment (one or more <affiliation> elements with
        <orgName> and <address> children), or None.

        Example input:  "Stanford University, California, USA"
        Example output: <affiliation><orgName type="institution">Stanford University</orgName>
                          <address><region>California</region>
                          <country key="US">USA</country></address></affiliation>
        """
        return self._call_text_endpoint(GROBID_AFFILIATIONS, "affiliations", raw, max_retries)

    def parse_citation(
        self,
        raw: str,
        consolidate: int = 0,
        include_raw: bool = False,
        bibtex: bool = False,
        max_retries: int | None = None,
    ) -> str | None:
        """
        Parse a single raw bibliographic reference via /api/processCitation.

        Parameters
        ----------
        raw:         Raw citation string, e.g. "Graff, Expert. Opin. Ther. Targets (2002) 6(1): 103-113"
        consolidate: 0 = no consolidation (default, fast)
                     1 = full CrossRef/PubMed consolidation, injects all extra metadata
                     2 = consolidate for DOI only
        include_raw: Embed the original raw string inside the TEI output.
        bibtex:      Return BibTeX instead of TEI XML.

        Returns a <biblStruct> TEI fragment, a BibTeX entry string, or None.
        """
        extra: dict[str, str] = {
            "consolidateCitations": str(consolidate),
            "includeRawCitations":  "1" if include_raw else "0",
        }
        headers = {"Accept": "application/x-bibtex"} if bibtex else None
        return self._call_text_endpoint(
            GROBID_CITATION, "citations", raw, max_retries,
            extra_data=extra, headers=headers,
        )

    def parse_citation_list(
        self,
        refs: list[str],
        consolidate: int = 0,
        include_raw: bool = False,
        bibtex: bool = False,
        max_retries: int | None = None,
    ) -> str | None:
        """
        Parse a batch of raw reference strings via /api/processCitationList.

        Each string in `refs` becomes a separate repeated `citations` form
        field (list-of-tuples, same pattern as teiCoordinates).  GROBID
        returns one <biblStruct> per input string in a single TEI response.

        Parameters
        ----------
        refs:        List of raw reference strings.
        consolidate: 0 = none (default), 1 = full CrossRef, 2 = DOI only.
        include_raw: Embed original strings as <note type="raw_reference">.
        bibtex:      Return BibTeX instead of TEI XML.

        Returns the full response body as a string, or None.
        """
        if not refs:
            return None

        data: list[tuple[str, str]] = [("citations", r) for r in refs]
        data.append(("consolidateCitations", str(consolidate)))
        data.append(("includeRawCitations",  "1" if include_raw else "0"))
        headers = {"Accept": "application/x-bibtex"} if bibtex else None
        retries  = max_retries if max_retries is not None else self._max_retries

        for attempt in range(1, retries + 1):
            try:
                resp = self._session.post(
                    GROBID_CITATION_LIST,
                    data=data,
                    headers=headers,
                    timeout=HEALTH_TIMEOUT,
                )
            except requests.exceptions.Timeout:
                log.warning("processCitationList | TIMEOUT on attempt %d", attempt)
                if attempt < retries:
                    time.sleep(1.5)
                continue
            except requests.exceptions.ConnectionError as exc:
                log.error("processCitationList | CONNECTION_ERROR: %s", exc)
                return None

            if resp.status_code == 200:
                text = resp.text.strip()
                return text if text else None
            if resp.status_code == 204:
                return None
            if resp.status_code == 503:
                log.debug("processCitationList | 503, attempt %d/%d", attempt, retries)
                if attempt < retries:
                    time.sleep(1.5)
                continue
            if resp.status_code == 400:
                log.warning("processCitationList | 400 Bad Request")
                return None
            log.warning("processCitationList | HTTP %d on attempt %d", resp.status_code, attempt)
            if attempt < retries:
                time.sleep(1.5)

        return None

    def reference_annotations(
        self,
        pdf_path: Path,
        consolidate: int = 0,
        include_raw: bool = False,
        include_figures_tables: bool = False,
        max_retries: int | None = None,
    ) -> dict | None:
        """
        Extract reference annotations with PDF coordinates via /api/referenceAnnotations.

        Uploads a PDF and returns a JSON dict with:
          - "refBibs":     list of full bibliographic reference annotation objects
          - "refMarkers":  list of in-text citation callout annotations, each
                           carrying page + bounding-box coords and a link to
                           its full reference in refBibs
          - "formulas" / "formulaMarkers": formula annotations (if present)
          - "pages":       page-level metadata

        Coordinates reference the original PDF page space and are ready for
        overlay rendering or layout-aware KG edges.

        Parameters
        ----------
        pdf_path:              PDF file to process.
        consolidate:           0/1/2 — same semantics as processCitation.
        include_raw:           Include raw reference strings in output.
        include_figures_tables: Also annotate figure/table callouts.

        Returns the parsed JSON dict, or None on failure / no content.

        Note: GROBID docs recommend 3-6s 503 retry wait for this endpoint.
        We use 4.5s (midpoint).
        """
        retries = max_retries if max_retries is not None else self._max_retries

        for attempt in range(1, retries + 1):
            log.info("%s | referenceAnnotations attempt %d/%d", pdf_path.name, attempt, retries)
            try:
                with open(pdf_path, "rb") as fh:
                    resp = self._session.post(
                        GROBID_REF_ANNOTATIONS,
                        files=[("input", (pdf_path.name, fh, "application/pdf"))],
                        data={
                            "consolidateCitations":  str(consolidate),
                            "includeRawCitations":   "1" if include_raw else "0",
                            "includeFiguresTables":  "1" if include_figures_tables else "0",
                        },
                        timeout=REQUEST_TIMEOUT,
                    )
            except requests.exceptions.Timeout:
                log.warning("%s | referenceAnnotations TIMEOUT on attempt %d", pdf_path.name, attempt)
                if attempt < retries:
                    time.sleep(4.5)
                continue
            except requests.exceptions.ConnectionError as exc:
                log.error("%s | referenceAnnotations CONNECTION_ERROR: %s", pdf_path.name, exc)
                return None

            if resp.status_code == 200:
                try:
                    return resp.json()
                except json.JSONDecodeError as exc:
                    log.error("%s | referenceAnnotations JSON decode error: %s", pdf_path.name, exc)
                    return None
            if resp.status_code == 204:
                return None
            if resp.status_code == 503:
                log.debug("%s | referenceAnnotations 503, attempt %d/%d", pdf_path.name, attempt, retries)
                if attempt < retries:
                    time.sleep(4.5)
                continue
            if resp.status_code == 400:
                log.warning("%s | referenceAnnotations 400 Bad Request", pdf_path.name)
                return None
            log.warning("%s | referenceAnnotations HTTP %d on attempt %d", pdf_path.name, resp.status_code, attempt)
            if attempt < retries:
                time.sleep(4.5)

        return None

    def annotate_pdf(
        self,
        pdf_path: Path,
        consolidate: int = 0,
        max_retries: int | None = None,
    ) -> bytes | None:
        """
        Augment a PDF with embedded reference hyperlinks via /api/annotatePDF.

        **EXPERIMENTAL** — GROBID docs warn this service modifies the source
        PDF and may be deprecated in favour of /api/referenceAnnotations.
        Prefer reference_annotations() for production use; only call this when
        you specifically need an annotated PDF file as output.

        Parameters
        ----------
        pdf_path:    Input PDF.
        consolidate: 0 = none, 1 = full CrossRef, 2 = DOI only.

        Returns the annotated PDF as raw bytes (write directly to a .pdf file),
        or None on 204 / error.

        Note: GROBID docs recommend 5-10s 503 retry wait; we use 7.5s.
        """
        retries = max_retries if max_retries is not None else self._max_retries

        for attempt in range(1, retries + 1):
            log.info("%s | annotatePDF attempt %d/%d", pdf_path.name, attempt, retries)
            try:
                with open(pdf_path, "rb") as fh:
                    resp = self._session.post(
                        GROBID_ANNOTATE_PDF,
                        files=[("input", (pdf_path.name, fh, "application/pdf"))],
                        data={"consolidateCitations": str(consolidate)},
                        timeout=REQUEST_TIMEOUT,
                    )
            except requests.exceptions.Timeout:
                log.warning("%s | annotatePDF TIMEOUT on attempt %d", pdf_path.name, attempt)
                if attempt < retries:
                    time.sleep(7.5)
                continue
            except requests.exceptions.ConnectionError as exc:
                log.error("%s | annotatePDF CONNECTION_ERROR: %s", pdf_path.name, exc)
                return None

            if resp.status_code == 200:
                return resp.content if resp.content else None
            if resp.status_code == 204:
                return None
            if resp.status_code == 503:
                log.debug("%s | annotatePDF 503, attempt %d/%d", pdf_path.name, attempt, retries)
                if attempt < retries:
                    time.sleep(7.5)
                continue
            if resp.status_code == 400:
                log.warning("%s | annotatePDF 400 Bad Request", pdf_path.name)
                return None
            log.warning("%s | annotatePDF HTTP %d on attempt %d", pdf_path.name, resp.status_code, attempt)
            if attempt < retries:
                time.sleep(7.5)

        return None

    def _call_text_endpoint(
        self,
        url: str,
        field: str,
        raw: str,
        max_retries: int | None,
        extra_data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str | None:
        """
        Shared implementation for GROBID lightweight text-parsing endpoints.

        All endpoints (processHeaderNames, processCitationNames,
        processAffiliations, processCitation) share identical HTTP semantics
        and the same GROBID-recommended 503 retry wait (~1s).  We use 1.5s
        for a small buffer.
        """
        retries  = max_retries if max_retries is not None else self._max_retries
        endpoint = url.rsplit("/", 1)[-1]
        data     = {field: raw, **(extra_data or {})}

        for attempt in range(1, retries + 1):
            try:
                resp = self._session.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=HEALTH_TIMEOUT,
                )
            except requests.exceptions.Timeout:
                log.warning("%s | TIMEOUT on attempt %d", endpoint, attempt)
                if attempt < retries:
                    time.sleep(1.5)
                continue
            except requests.exceptions.ConnectionError as exc:
                log.error("%s | CONNECTION_ERROR: %s", endpoint, exc)
                return None

            if resp.status_code == 200:
                text = resp.text.strip()
                return text if text else None

            if resp.status_code == 204:
                return None

            if resp.status_code == 503:
                log.debug("%s | 503, attempt %d/%d", endpoint, attempt, retries)
                if attempt < retries:
                    time.sleep(1.5)
                continue

            if resp.status_code == 400:
                log.warning("%s | 400 Bad Request for input: %.80r", endpoint, raw)
                return None

            log.warning("%s | HTTP %d on attempt %d", endpoint, resp.status_code, attempt)
            if attempt < retries:
                time.sleep(1.5)

        return None

    # ── Main API call ─────────────────────────────────────────────────────────

    def process_pdf(self, pdf_path: Path) -> GROBIDResponse:
        """
        Submit a PDF to GROBID /api/processFulltextDocument with all required
        parameters. Retries up to max_retries times with per-error-class backoff.

        Returns a GROBIDResponse — never raises.
        """
        t_start      = time.perf_counter()
        last_failure = FailureType.UNKNOWN

        for attempt in range(1, self._max_retries + 1):
            log.info("%s | GROBID attempt %d/%d", pdf_path.name, attempt, self._max_retries)
            try:
                resp, failure = self._single_request(pdf_path)
            except Exception as exc:
                log.exception("%s | unexpected error on attempt %d: %s", pdf_path.name, attempt, exc)
                failure = FailureType.UNKNOWN
                resp    = None

            if failure is None and resp is not None:
                # ── Success ───────────────────────────────────────────────────
                elapsed = time.perf_counter() - t_start
                return GROBIDResponse(
                    success=True,
                    xml_text=resp.text,
                    status_code=resp.status_code,
                    elapsed_sec=elapsed,
                    attempts=attempt,
                )

            last_failure = failure or FailureType.UNKNOWN

            if not last_failure.is_retriable():
                log.info(
                    "%s | non-retriable failure: %s — giving up after attempt %d",
                    pdf_path.name, last_failure, attempt,
                )
                break

            if attempt < self._max_retries:
                self._sleep(last_failure, attempt)

        elapsed = time.perf_counter() - t_start
        return GROBIDResponse(
            success=False,
            xml_text="",
            status_code=0,
            elapsed_sec=elapsed,
            attempts=attempt,
            failure_type=last_failure,
        )

    # ── Single HTTP attempt ───────────────────────────────────────────────────

    def _single_request(
        self, pdf_path: Path
    ) -> tuple[requests.Response | None, FailureType | None]:
        """
        Execute one HTTP POST. Returns (response, None) on success,
        (None, FailureType) on any error.
        """
        try:
            with open(pdf_path, "rb") as fh:
                resp = self._session.post(
                    GROBID_FULLTEXT,
                    files=[("input", (pdf_path.name, fh, "application/pdf"))],
                    data=GROBID_FORM_PARAMS,
                    timeout=REQUEST_TIMEOUT,
                )
        except requests.exceptions.Timeout:
            log.warning("%s | TIMEOUT", pdf_path.name)
            return None, FailureType.TIMEOUT
        except requests.exceptions.ConnectionError as exc:
            log.error("%s | CONNECTION_ERROR | %s", pdf_path.name, exc)
            return None, FailureType.CONNECTION_ERROR

        code = resp.status_code

        if code == 200:
            if not resp.text.strip():
                log.warning("%s | 200 but empty body", pdf_path.name)
                return None, FailureType.EMPTY_TEI
            return resp, None

        if code == 204:
            log.info("%s | 204 No Content — GROBID found nothing", pdf_path.name)
            return None, FailureType.HTTP_204

        if code == 503:
            log.warning("%s | 503 — GROBID engine pool exhausted", pdf_path.name)
            return None, FailureType.HTTP_503

        if code == 500:
            body    = resp.text or ""
            failure = classify_500_body(body)
            log.warning("%s | 500 → %s | %.120s", pdf_path.name, failure, body.replace("\n", " "))
            return None, failure

        log.warning("%s | HTTP %d", pdf_path.name, code)
        return None, FailureType.HTTP_OTHER

    # ── Backoff ───────────────────────────────────────────────────────────────

    def _sleep(self, failure: FailureType, attempt: int) -> None:
        base, cap = _BACKOFF.get(failure, _DEFAULT_BACKOFF)
        delay     = min(base * (2 ** (attempt - 1)) + random.uniform(0, 1), cap)
        log.debug("backoff %.1fs for %s (attempt %d)", delay, failure, attempt)
        time.sleep(delay)
