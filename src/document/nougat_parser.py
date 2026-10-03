"""
nougat_parser.py — PDF → Nougat markdown → TEIDocument.

NougatParser wraps the facebook/nougat-base VisionEncoderDecoder model.
It renders PDF pages to images with PyMuPDF then runs Nougat page by page.
Output is passed to MarkdownScientificParser to produce a TEIDocument.

Design constraints:
  - Model is loaded ONCE per parser instance (lazy, on first parse call).
  - No bounding-box coordinates — Nougat outputs text, not coordinates.
  - parser_capabilities does NOT include COORDINATES.
  - Memory is released after each page (del image, empty CUDA cache).
  - NOUGAT_ENABLED=false disables model load entirely (returns empty TEIDocument).
  - All exceptions surface as FailureType values via NougatParseError.

Configuration (environment variables):
  NOUGAT_MODEL          facebook/nougat-base
  NOUGAT_MAX_PAGES      20
  NOUGAT_MAX_NEW_TOKENS 4096
  NOUGAT_DEVICE         auto  (cuda / cpu / auto)
  NOUGAT_ENABLED        true
"""

from __future__ import annotations

import dataclasses
import gc
import hashlib
import io
import logging
import os
import pickle
import time
from pathlib import Path
from src.document.markdown_parser import MarkdownScientificParser
from src.document.models import TEIDocument
from src.document.provenance import (
    NOUGAT_CAPABILITIES,
    ParserKind,
    ParserProvenance,
)

log = logging.getLogger(__name__)


# ── Configuration (env-var driven, resolved once at import) ───────────────────

_MODEL_NAME      = os.getenv("NOUGAT_MODEL",          "facebook/nougat-base")
_MAX_PAGES       = int(os.getenv("NOUGAT_MAX_PAGES",   "20"))
_MAX_NEW_TOKENS  = int(os.getenv("NOUGAT_MAX_NEW_TOKENS", "4096"))

# Region inference guard (2026-10-03 audit): Nougat collapses into repetition loops on
# region crops (146 of 4,674 regions, up to 24,000 characters). Generation stops as
# soon as the tail repeats, and the caller is told so the output can be rejected.
# The version is part of the L1 cache key: outputs cached before the guard existed
# must not be served again.
REGION_GUARD_VERSION = "guard3"
_LOOP_MIN_SPAN   = 60     # tokens the repeated run must cover
_LOOP_MAX_PERIOD = 200    # longest repeated unit looked for
_LOOP_REPEATS    = 3      # identical consecutive units → loop
_LOOP_EVERY      = 16     # check every N generated tokens


def loop_start(seq: list[int]) -> int | None:
    """Index where the trailing run of identical units begins (keep seq[:index]),
    or None when the sequence does not end in a loop. Like the Nougat authors'
    own inference, the prefix before the collapse is kept and the loop dropped."""
    n = len(seq)
    for period in range(1, min(_LOOP_MAX_PERIOD, n // _LOOP_REPEATS) + 1):
        reps = max(_LOOP_REPEATS, -(-_LOOP_MIN_SPAN // period))
        if period * reps > n:
            continue
        unit = seq[n - period:]
        if all(seq[n - (k + 1) * period: n - k * period] == unit for k in range(1, reps)):
            k = reps
            while n - (k + 1) * period >= 0 and seq[n - (k + 1) * period: n - k * period] == unit:
                k += 1
            return n - k * period
    return None


def _is_looping(seq: list[int]) -> bool:
    """True when the sequence ends in ≥ _LOOP_REPEATS identical consecutive units
    covering ≥ _LOOP_MIN_SPAN tokens. guard1 counted a tail anywhere in the window
    and stopped legitimate pages on a LaTeX term written three times."""
    n = len(seq)
    for period in range(1, min(_LOOP_MAX_PERIOD, n // _LOOP_REPEATS) + 1):
        reps = max(_LOOP_REPEATS, -(-_LOOP_MIN_SPAN // period))
        if period * reps > n:
            continue
        unit = seq[n - period:]
        if all(seq[n - (k + 1) * period: n - k * period] == unit for k in range(1, reps)):
            return True
    return False


class _LoopStop:
    """transformers StoppingCriteria: stop when generation ends in a run of identical
    consecutive units (see _is_looping)."""

    def __init__(self, prompt_len: int) -> None:
        self.prompt_len = prompt_len
        self.tripped = False

    def __call__(self, input_ids, scores=None, **kwargs):
        import torch
        n = input_ids.shape[1] - self.prompt_len
        if not self.tripped and n >= _LOOP_MIN_SPAN and n % _LOOP_EVERY == 0:
            span = _LOOP_MAX_PERIOD * _LOOP_REPEATS
            self.tripped = _is_looping(input_ids[0, -span:].tolist())
        return torch.full((input_ids.shape[0],), self.tripped, dtype=torch.bool,
                          device=input_ids.device)


_DEVICE_PREF     = os.getenv("NOUGAT_DEVICE",          "auto")
_ENABLED         = os.getenv("NOUGAT_ENABLED",          "true").lower() not in {"0", "false", "no"}

DPI = 150     # render resolution; 150dpi is a good Nougat operating point

# L1 per-crop inference cache — avoids re-running 8s GPU inference on repeated
# pipeline runs over the same corpus.  Key: SHA256(model:version:image_bytes).
# Sharded 2-level (key[:2]/key.pkl) to stay under typical inode limits at 200k+ crops.
_L1_CACHE_DIR = Path(os.getenv(
    "NOUGAT_L1_CACHE_DIR",
    str(Path(__file__).resolve().parents[2] / "data" / "nougat_cache" / "l1"),
))


# ── Exceptions ────────────────────────────────────────────────────────────────

class NougatParseError(RuntimeError):
    """Raised (and caught by HybridParser / ParserRouter) on unrecoverable errors."""
    def __init__(self, msg: str, failure_kind: str = "NOUGAT_INFERENCE_ERROR"):
        super().__init__(msg)
        self.failure_kind = failure_kind


# ── Parser ────────────────────────────────────────────────────────────────────

class NougatParser:
    """
    PDF → Nougat markdown → TEIDocument.

    Satisfies the DocumentParser protocol.
    Thread-safety: one instance per thread/actor; the underlying model is NOT
    shared across threads (PyTorch tensors are not thread-safe by default).
    """

    parser_name:    str = "nougat"
    parser_version: str = _MODEL_NAME

    def __init__(
        self,
        model_name:     str = _MODEL_NAME,
        max_pages:      int = _MAX_PAGES,
        max_new_tokens: int = _MAX_NEW_TOKENS,
        device:         str = _DEVICE_PREF,
    ) -> None:
        self._model_name      = model_name
        self._max_pages       = max_pages
        self._max_new_tokens  = max_new_tokens
        self._device_pref     = device
        self._processor       = None   # lazy
        self._model           = None   # lazy
        self._markdown_parser = MarkdownScientificParser()

    # ── DocumentParser protocol ───────────────────────────────────────────────

    def parse_text(self, source: str, paper_id: str) -> TEIDocument:
        """Parse Nougat markdown text directly (no model inference required)."""
        return self._markdown_parser.parse_text(source, paper_id)

    def parse_file(self, path: Path, paper_id: str) -> TEIDocument:
        """
        Dispatch on file extension:
          .pdf        → full Nougat inference (render + model)
          .mmd / .md  → MarkdownScientificParser (no model)
        """
        suffix = path.suffix.lower()
        if suffix in {".mmd", ".md", ".txt"}:
            return self._markdown_parser.parse_file(path, paper_id)
        return self.parse_pdf(path, paper_id)

    # ── Main PDF path ─────────────────────────────────────────────────────────

    def parse_pdf(self, pdf_path: Path, paper_id: str) -> TEIDocument:
        """
        Render PDF pages to images, run Nougat inference, return TEIDocument.

        Steps:
          1. Open PDF with fitz (PyMuPDF).
          2. Render each page (up to max_pages) at DPI to a PIL Image.
          3. Process image with NougatProcessor → pixel_values tensor.
          4. model.generate() → token ids → decode → post_process_generation.
          5. Concatenate page outputs → full markdown string.
          6. MarkdownScientificParser → TEIDocument with Nougat provenance.
        """
        if not _ENABLED:
            raise NougatParseError(
                f"Nougat disabled (NOUGAT_ENABLED=false) for {pdf_path.name}",
                failure_kind="NOUGAT_INFERENCE_ERROR",
            )

        self._ensure_model_loaded()

        import fitz  # PyMuPDF — guaranteed available (used by pdf_triage.py)
        from PIL import Image

        t0 = time.perf_counter()
        page_outputs: list[str] = []

        try:
            doc_fitz = fitz.open(str(pdf_path))
            # Redirect MuPDF C-level errors to our logger instead of raw stderr.
            # Accumulated warnings are available via fitz.TOOLS.mupdf_warnings().
            fitz.TOOLS.mupdf_display_errors(False)
        except Exception as exc:
            raise NougatParseError(
                f"fitz could not open {pdf_path.name}: {exc}",
                failure_kind="NOUGAT_INFERENCE_ERROR",
            ) from exc

        total_pages = doc_fitz.page_count   # save before close
        n_pages = min(total_pages, self._max_pages)
        log.info("[nougat] %s | %d pages (cap=%d)", paper_id, total_pages, n_pages)

        page_num = 0   # keep in scope for MemoryError handler
        try:
            for page_num in range(n_pages):
                page_text = self._process_page(doc_fitz, page_num, paper_id)
                if page_text:
                    page_outputs.append(page_text)
                # Drain MuPDF warnings into our logger (prevents stderr spam)
                mupdf_warns = fitz.TOOLS.mupdf_warnings()
                if mupdf_warns:
                    log.debug(
                        "[nougat] %s page %d mupdf: %s",
                        paper_id, page_num, mupdf_warns[:300],
                    )
                gc.collect()

        except MemoryError as exc:
            raise NougatParseError(
                f"OOM on page {page_num} of {pdf_path.name}",
                failure_kind="NOUGAT_OOM",
            ) from exc
        finally:
            doc_fitz.close()

        elapsed = time.perf_counter() - t0

        if not page_outputs:
            raise NougatParseError(
                f"Nougat produced no output for {pdf_path.name}",
                failure_kind="NOUGAT_EMPTY_OUTPUT",
            )

        full_markdown = "\n\n".join(page_outputs)
        log.info(
            "[nougat] %s | done | pages=%d/%d  markdown_len=%d  %.1fs",
            paper_id, len(page_outputs), total_pages, len(full_markdown), elapsed,
        )

        doc = self._markdown_parser.parse_text(full_markdown, paper_id)

        prov = ParserProvenance(
            parser_name    = self.parser_name,
            parser_version = self._model_name,
            source_format  = "pdf_image",
            confidence     = 0.80,
            capabilities   = NOUGAT_CAPABILITIES,
            elapsed_sec    = elapsed,
            notes          = f"pages={len(page_outputs)}/{total_pages}",
        )
        # TEIDocument is a frozen dataclass — use dataclasses.replace() to
        # attach Nougat provenance and source path to the markdown_parser output.
        return dataclasses.replace(
            doc,
            parser_provenance = [prov],
            source_paths      = list(doc.source_paths) + [str(pdf_path)],
        )

    # ── Per-page inference ────────────────────────────────────────────────────

    def _process_page(self, doc_fitz, page_num: int, paper_id: str) -> str:
        """Render one page and run Nougat inference. Returns decoded markdown."""
        import torch
        from PIL import Image, ImageStat

        page = doc_fitz[page_num]
        pix  = page.get_pixmap(dpi=DPI)
        img  = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        # Skip near-blank pages — corrupted PDF streams often render white.
        # Sending a blank image to Nougat wastes GPU time and produces hallucinated
        # repetitive tokens; mean luminance > 250/255 is a reliable blank signal.
        mean_lum = ImageStat.Stat(img.convert("L")).mean[0]
        if mean_lum > 250:
            log.debug("[nougat] %s page %d skipped (blank, lum=%.1f)", paper_id, page_num, mean_lum)
            return ""

        device = self._model.device

        # Processor: image → pixel_values
        encoding = self._processor(
            img,
            return_tensors="pt",
            **self._image_kwargs(),
        )
        pixel_values = encoding.pixel_values.to(device)

        with torch.inference_mode():
            outputs = self._model.generate(
                pixel_values,
                min_length=1,
                max_new_tokens=self._max_new_tokens,
                bad_words_ids=[[self._processor.tokenizer.unk_token_id]],
            )

        sequence = self._processor.batch_decode(outputs, skip_special_tokens=True)[0]
        sequence = self._processor.post_process_generation(sequence, fix_markdown=False)

        # Aggressively free tensor memory
        del pixel_values, outputs, encoding
        if device.type == "cuda":
            torch.cuda.empty_cache()

        return sequence

    # ── Lazy model loading ────────────────────────────────────────────────────

    def _ensure_model_loaded(self) -> None:
        if self._model is not None:
            return

        try:
            from transformers import VisionEncoderDecoderModel, NougatProcessor
            import torch
        except ImportError as exc:
            raise NougatParseError(
                f"transformers or torch not installed: {exc}",
                failure_kind="NOUGAT_MODEL_LOAD_ERROR",
            ) from exc

        device_map = self._resolve_device_map()
        log.info("[nougat] loading %s | device_map=%s", self._model_name, device_map)
        t0 = time.perf_counter()

        try:
            processor_kwargs = {
                "do_crop_margin": False,
            }
            self._processor = NougatProcessor.from_pretrained(
                self._model_name,
                **processor_kwargs,
            )
            ip = self._processor.image_processor
            bool_defaults = {
                            "do_crop_margin": False,
                            "do_thumbnail": False,
                            "do_align_long_axis": False,
                            "do_rescale": True,
                            "do_normalize": True,
                            "do_pad": True,
                            "do_resize": True,
                        }
            for field, default in bool_defaults.items():
                if hasattr(ip, field):
                    value = getattr(ip, field)
                    if value is None:
                        setattr(ip, field, default)
            # Generic scan: any do_* attribute still None after the explicit list
            # triggers TypeError in newer Nougat model versions not covered above.
            for attr in vars(ip):
                if attr.startswith("do_") and attr not in bool_defaults and getattr(ip, attr) is None:
                    setattr(ip, attr, False)
            if hasattr(self._processor, "image_processor"):
                self._processor.image_processor.do_crop_margin = False
            if hasattr(self._processor.image_processor, "size"):
                self._processor.image_processor.size = dict(
                    self._processor.image_processor.size)
            if self._device_pref == "auto":
                self._device = torch.device(
                    "cuda" if torch.cuda.is_available() else "cpu"
                )
            elif self._device_pref.startswith("cuda"):
                self._device = torch.device(self._device_pref)
            else:
                self._device = torch.device("cpu")
            self._model = VisionEncoderDecoderModel.from_pretrained(
                self._model_name,
            )
            self._model.to(self._device)
            self._model.eval()
        except Exception as exc:
            self._processor = None
            self._model     = None
            raise NougatParseError(
                f"Failed to load {self._model_name}: {exc}",
                failure_kind="NOUGAT_MODEL_LOAD_ERROR",
            ) from exc

        log.info(
            "[nougat] model ready | device=%s  %.1fs",
            next(self._model.parameters()).device,
            time.perf_counter() - t0,
        )

    def _resolve_device_map(self) -> str:
        """Translate NOUGAT_DEVICE pref to device_map string for HF."""
        if self._device_pref == "auto":
            return "auto"
        if self._device_pref.startswith("cuda"):
            return "cuda"
        return "cpu"

    def model_info(self) -> dict:
        return {
            "model_name":     self._model_name,
            "max_pages":      self._max_pages,
            "max_new_tokens": self._max_new_tokens,
            "device_pref":    self._device_pref,
            "loaded":         self._model is not None,
            "enabled":        _ENABLED,
        }


    #: Preprocessing settings read back off the loaded image processor. Every one
    #: of these must be supplied together — see `_image_kwargs`.
    _IMAGE_KWARG_FIELDS = (
        "do_crop_margin", "do_thumbnail", "do_align_long_axis",
        "do_resize", "size", "resample",
        "do_rescale", "rescale_factor",
        "do_normalize", "image_mean", "image_std",
        "do_pad",
    )

    def _image_kwargs(self) -> dict:
        """The complete preprocessing kwarg set, taken from the processor itself.

        transformers 5.x validates the *call* kwargs against a TypedDict whose
        defaults are all None, and does not fall back to the image processor's
        attributes while doing so. Passing one flag (`do_crop_margin=False`) and
        letting the rest default therefore fails with "Field 'do_thumbnail'
        expected bool, got NoneType" — and passing none of them fails the same way
        on `do_crop_margin`. Each flag also drags in its companions: `do_rescale`
        needs `rescale_factor`, `do_normalize` needs `image_mean`/`image_std`,
        `do_resize` needs `size` and `resample`.

        So the set is rebuilt from the processor's own configuration, which keeps
        the model's values rather than inventing any, with two adjustments:
        margin cropping stays off (crops are already tight), and `size` is
        converted from SizeDict to a plain dict, which is what the validator
        accepts.
        """
        ip = self._processor.image_processor
        kwargs = {}
        for field in self._IMAGE_KWARG_FIELDS:
            value = getattr(ip, field, None)
            if value is not None:
                kwargs[field] = value

        kwargs["do_crop_margin"] = False

        size = kwargs.get("size")
        if size is not None and not isinstance(size, dict):
            kwargs["size"] = {k: v for k, v in dict(size).items() if v is not None}
        return kwargs

    def parse_image(
            self,
            image,
            paper_id: str,
            max_new_tokens: int | None = None,
    ) -> TEIDocument:
        """
        Run Nougat directly on PIL image crop, with L1 disk cache.

        Cache key: SHA256("{model_name}:{parser_version}:{png_bytes}").
        Cache hit returns immediately without loading the model.
        """
        # ── Compute cache key from model identity + image content ─────────────
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        img_bytes = buf.getvalue()
        del buf

        budget    = int(max_new_tokens or self._max_new_tokens)
        key_src   = (f"{self._model_name}:{self.parser_version}:{REGION_GUARD_VERSION}:"
                     f"{budget}:").encode() + img_bytes
        cache_key = hashlib.sha256(key_src).hexdigest()
        cache_path = _L1_CACHE_DIR / cache_key[:2] / f"{cache_key}.pkl"

        # ── Cache read (skips model load entirely on hit) ─────────────────────
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as fh:
                    doc = pickle.load(fh)
                log.debug("[nougat-cache] HIT  %s…", cache_key[:12])
                return doc
            except Exception as exc:
                log.warning("[nougat-cache] corrupt entry %s…: %s — re-inferring",
                            cache_key[:12], exc)
                cache_path.unlink(missing_ok=True)

        # ── Cache miss: run inference ─────────────────────────────────────────
        self._ensure_model_loaded()

        import torch

        pixel_values = self._processor(
            images=image,
            return_tensors="pt",
            **self._image_kwargs(),
        ).pixel_values

        pixel_values = pixel_values.to(self._model.device)

        from transformers import StoppingCriteriaList
        guard = _LoopStop(prompt_len=1)
        with torch.inference_mode():
            outputs = self._model.generate(
                pixel_values,
                min_length=1,
                max_new_tokens=budget,
                bad_words_ids=[
                    [self._processor.tokenizer.unk_token_id]
                ],
                stopping_criteria=StoppingCriteriaList([guard]),
            )
        self.last_guard_tripped = guard.tripped
        self.last_hit_budget = (outputs.shape[1] - 1) >= budget and not guard.tripped
        if guard.tripped:
            cut = loop_start(outputs[0].tolist())
            if cut is not None:
                outputs = outputs[:, :cut]

        decoded = self._processor.batch_decode(
            outputs,
            skip_special_tokens=True,
        )[0]

        markdown = self._processor.post_process_generation(
            decoded,
            fix_markdown=False,
        )

        doc = self._markdown_parser.parse_text(
            markdown,
            paper_id=paper_id,
        )

        doc = dataclasses.replace(
            doc,
            markdown_text=markdown,
        )

        del pixel_values, outputs

        if self._model.device.type == "cuda":
            torch.cuda.empty_cache()

        # ── Cache write (atomic, non-fatal) ───────────────────────────────────
        _tmp = cache_path.with_suffix(".pkl.tmp")
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(_tmp, "wb") as fh:
                pickle.dump(doc, fh, protocol=4)
            _tmp.rename(cache_path)
            log.debug("[nougat-cache] MISS written %s…", cache_key[:12])
        except Exception as exc:
            log.warning("[nougat-cache] write failed: %s", exc)
            _tmp.unlink(missing_ok=True)

        return doc
