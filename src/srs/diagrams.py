"""
src/srs/diagrams.py — Requirements from figures (UML, flowcharts, architecture)
==============================================================================
Industrial SRS documents put part of the specification in pictures: use-case,
activity and sequence diagrams, flowcharts, context and deployment diagrams.
The readers emit every embedded picture as a Figure block; this module decides
what each picture says and turns that into requirements.

    blocks -> attach_captions -> noise filter -> interpret_figures -> DiagramStrategy

Modes (ExtractionOptions.diagram_mode, else $SRS_DIAGRAM_MODE, else "local"):

    mode     engines, in order                                   network
    off      none; figures are ignored                           no
    local    alt text (Word accessibility text) + OCR            no
             (Tesseract, when the binary is installed)
    vision   multimodal LLM (OpenAI, $SRS_VISION_MODEL,           yes
             default gpt-4o-mini); falls back to local

How a reading becomes requirements (DiagramStrategy):
  * statement with a requirement ID (printed in the figure or its caption)
      - ID already found in the text  -> no new requirement; the figure is
                                         attached to it as context (`figures`)
      - new ID                        -> requirement with that ID; several
                                         statements with the same ID are merged
  * statement without an ID
      - figure sits under a tagged requirement heading that exists -> context
      - structural diagram (architecture, component, deployment, class, ER,
        chart, UI mock-up): parts and connections, not behaviour -> context
      - otherwise                     -> requirement FIG-<n>.<k>, id_source
                                         "generated" (n = caption number)
  * plain labels (OCR "Customer", "Payment Gateway") are context only

Guarantees
  * Deterministic re-parsing: vision readings are cached on disk, keyed by the
    image hash, model, prompt version, caption and alt text, so the upload
    preview, the analysis run and the PR bot all see the same requirements
    ($SRS_DIAGRAM_CACHE_DIR, default ~/.cache/traceability/diagrams).
  * Grounded: the prompt forbids invented behaviour and invented IDs; IDs the
    model returns must match the document's ID pattern or they are dropped.
  * Bounded cost: figures in blocklisted sections (appendix, glossary, ...) are
    removed by the noise filter before interpretation, tiny images (icons,
    bullets, logos) are skipped, and at most `max_figures` are read.
  * Never raises: a failing engine falls back and the report says so.
  * The LLM never scores: it only transcribes the picture. Every diagram
    requirement is marked strategy "diagram" and shown in the preview.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Set, Tuple

from dotenv import load_dotenv

from src.srs.blocks import Block, DiagramReading, DiagramStatement, Figure, Paragraph
from src.srs.noise import clean_text, is_caption
from src.srs.normalise import canonical_id
from src.srs.options import ExtractionOptions
from src.srs.report import ExtractionReport
from src.srs.strategies import (
    META_ID_SOURCE,
    STRATEGY_DIAGRAM,
    Candidate,
    SectionContext,
    TagMatcher,
)

load_dotenv()
logger = logging.getLogger(__name__)

PROMPT_VERSION = "2"
DEFAULT_VISION_MODEL = "gpt-4o-mini"
MAX_STATEMENTS = 12
MAX_SIDE_PX = 2048            # vision APIs downscale beyond this anyway
MIN_SIDE_PX = 40
MIN_AREA_PX = 120 * 120

DIAGRAM_TYPES = ("use_case", "activity", "sequence", "class", "state", "component", "deployment",
                 "flowchart", "architecture", "er", "ui_mockup", "chart", "other")
# These show what the system is made of, not what it does: statements read from
# them without a requirement ID are kept as context, never as requirements.
STRUCTURAL_TYPES = frozenset({"architecture", "component", "deployment", "class", "er", "chart", "ui_mockup"})

_SHALL = re.compile(r"\b(shall|must)\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
_FIG_NUMBER = re.compile(r"^\s*(?:figure|fig\.?|diagram|exhibit|chart)\s*([0-9]+(?:[.\-][0-9]+)*)", re.IGNORECASE)
_CAPTION_LABEL = re.compile(r"^\s*(?:figure|fig\.?|diagram|exhibit|chart)\s*[0-9]+(?:[.\-][0-9]+)*\s*[:.\-–—]?\s*",
                            re.IGNORECASE)


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------

def attach_captions(blocks: Sequence[Block]) -> List[Block]:
    """Give each Figure the caption paragraph right after it (Word's default
    placement) or, failing that, right before it. The caption paragraph itself
    stays in the stream; the noise filter drops it as usual."""
    out = list(blocks)
    for i, block in enumerate(out):
        if not isinstance(block, Figure) or block.caption:
            continue
        for j in (i + 1, i - 1):
            if 0 <= j < len(out) and isinstance(out[j], Paragraph) and is_caption(out[j]):
                out[i] = replace(block, caption=out[j].text)
                break
    return out


def figure_label(figure: Figure) -> str:
    """FIG-<caption number>, else FIG-<ordinal in the document>."""
    m = _FIG_NUMBER.match(figure.caption or "")
    return f"FIG-{m.group(1)}" if m else f"FIG-{figure.locator.ordinal}"


def caption_text(caption: str) -> str:
    """Caption without its "Figure 2:" label."""
    return _CAPTION_LABEL.sub("", caption or "").strip()


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreparedImage:
    png: bytes
    width: int
    height: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.png).hexdigest()


def prepare_image(data: bytes) -> Tuple[Optional[PreparedImage], str]:
    """Decode any format Pillow reads (PNG, JPEG, GIF, BMP, TIFF; EMF/WMF on
    Windows), flatten transparency onto white, cap the size, re-encode as PNG.
    Returns (image, "") or (None, skip reason)."""
    if not data:
        return None, "image_unreadable"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                flat = Image.new("RGB", rgba.size, "white")
                flat.paste(rgba, mask=rgba.split()[-1])
            else:
                flat = im.convert("RGB")
    except Exception:  # noqa: BLE001 — unknown or vector-only format
        return None, "image_unreadable"
    w, h = flat.size
    if min(w, h) < MIN_SIDE_PX or w * h < MIN_AREA_PX:
        return None, "image_too_small"
    if max(w, h) > MAX_SIDE_PX:
        flat.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    buf = io.BytesIO()
    flat.save(buf, format="PNG", optimize=True)
    return PreparedImage(buf.getvalue(), *flat.size), ""


# ---------------------------------------------------------------------------
# Turning text into statements
# ---------------------------------------------------------------------------

def statements_from_text(text: str, tags: TagMatcher) -> Tuple[List[DiagramStatement], List[str]]:
    """Split free text (alt text, OCR output) into requirement statements
    (start with an ID, or contain shall/must) and leftover labels."""
    statements: List[DiagramStatement] = []
    labels: List[str] = []
    for chunk in re.split(r"\n\s*\n", text or ""):
        chunk = clean_text(chunk)
        if not chunk:
            continue
        for sentence in _SENTENCES.split(chunk):
            sentence = sentence.strip()
            if not sentence:
                continue
            m = tags.match_paragraph(sentence)
            if m and m[1]:
                statements.append(DiagramStatement(text=m[1], req_id=m[0]))
            elif _SHALL.search(sentence) and len(sentence.split()) >= 4:
                statements.append(DiagramStatement(text=sentence))
            else:
                labels.append(sentence)
    return statements, labels


def _dedupe(statements: Sequence[DiagramStatement]) -> Tuple[DiagramStatement, ...]:
    seen: Set[str] = set()
    out: List[DiagramStatement] = []
    for st in statements:
        key = re.sub(r"\W+", " ", st.text.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(st)
    return tuple(out[:MAX_STATEMENTS])


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class DiagramEngine(Protocol):
    name: str

    def read(self, figure: Figure, image: Optional[PreparedImage], section: str) -> Optional[DiagramReading]:
        """A reading, or None when this engine has nothing to say."""


class AltTextEngine:
    """Word accessibility text. Authors of accessible SRS documents often put
    the full meaning of a diagram there."""

    name = "alt_text"

    def __init__(self, tags: TagMatcher) -> None:
        self.tags = tags

    def read(self, figure: Figure, image: Optional[PreparedImage], section: str) -> Optional[DiagramReading]:
        if not figure.alt_text:
            return None
        statements, labels = statements_from_text(figure.alt_text, self.tags)
        return DiagramReading(engine=self.name, summary=" ".join(labels)[:400], statements=_dedupe(statements))


def tesseract_available() -> bool:
    try:
        import pytesseract
    except ImportError:
        return False
    cmd = os.getenv("TESSERACT_CMD", "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
        return Path(cmd).exists()
    return shutil.which(pytesseract.pytesseract.tesseract_cmd) is not None


class TesseractEngine:
    """OCR of the text drawn in the diagram (labels, notes, guard conditions).
    Needs the Tesseract binary; `pytesseract` is only the wrapper."""

    name = "ocr:tesseract"

    def __init__(self, tags: TagMatcher) -> None:
        self.tags = tags

    def read(self, figure: Figure, image: Optional[PreparedImage], section: str) -> Optional[DiagramReading]:
        if image is None:
            return None
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(image.png)) as im:
            gray = im.convert("L")
            if max(gray.size) < 1200:                         # small text OCRs badly
                gray = gray.resize((gray.width * 2, gray.height * 2))
            # psm 11 = sparse text: diagram labels are scattered, not a page of prose
            text = pytesseract.image_to_string(gray, config="--psm 11")
        statements, labels = statements_from_text(text, self.tags)
        if not statements and not labels:
            return None
        summary = "Text in the figure: " + "; ".join(dict.fromkeys(labels))[:380] if labels else ""
        return DiagramReading(engine=self.name, summary=summary, statements=_dedupe(statements))


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    return not v or v.startswith(("sk-your", "sk-xxx", "your-", "changeme", "change-me", "<", "paste"))


def default_vision_client() -> Optional[Any]:
    """OpenAI client when OPENAI_API_KEY is set, else None. Tests replace
    `vision_client_factory` instead of touching the network."""
    key = os.getenv("OPENAI_API_KEY", "")
    if _is_placeholder(key):
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai is not installed; diagrams cannot be read with a vision model.")
        return None
    return OpenAI(api_key=key, timeout=60.0, max_retries=1)


vision_client_factory: Callable[[], Optional[Any]] = default_vision_client

VISION_SYSTEM = ("You are a senior requirements engineer. You read one figure taken from a Software "
                 "Requirements Specification (SRS) and transcribe the requirements it specifies. You never "
                 "invent behaviour that is not drawn.")

VISION_PROMPT = """\
Figure caption: {caption}
Figure alt text: {alt}
SRS section: {section}

Return ONLY a JSON object:
{{
  "diagram_type": one of {types},
  "summary": "at most 2 sentences: what the figure models",
  "requirements": [
    {{"id": "requirement ID shown in the figure or caption, else null",
      "title": "short name, at most 8 words",
      "statement": "The system shall ..."}}
  ]
}}

Rules:
- One entry per distinct behaviour, business rule, constraint, decision/guard, limit or interaction the
  figure specifies (use cases, activity steps and branches, messages between components, state changes).
- Each statement is ONE sentence starting "The system shall" (or "The system must"). Copy names, field names,
  endpoints, roles, numbers and limits from the figure verbatim.
- If the figure or its caption shows a requirement ID (for example FR-3, REQ-09, NFR-2), put it in "id"
  exactly as written for the statements it covers. Never make up an ID.
- Structural diagrams (system context, architecture, component, deployment, class, ER) that only show parts
  and connections specify no behaviour: return "requirements": [] unless the figure writes out an explicit
  rule, limit or requirement ID.
- Logos, photos, screenshots and decorative images specify nothing: return "requirements": [].
- At most {max_n} entries.
"""


class VisionEngine:
    """Multimodal LLM that understands diagram semantics, not just its text:
    swim lanes, decision guards, message order, multiplicities."""

    def __init__(self, tags: TagMatcher, client: Optional[Any] = None, model: Optional[str] = None,
                 cache_dir: Optional[Path] = None) -> None:
        self.tags = tags
        self.client = client
        self.model = model or os.getenv("SRS_VISION_MODEL") or DEFAULT_VISION_MODEL
        self.name = f"vision:{self.model}"
        self.cache_dir = cache_dir or Path(os.getenv("SRS_DIAGRAM_CACHE_DIR")
                                           or Path.home() / ".cache" / "traceability" / "diagrams")
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self.client is not None

    def _cache_key(self, figure: Figure, image: PreparedImage) -> str:
        h = hashlib.sha256()
        for part in (image.sha256, self.model, PROMPT_VERSION, figure.caption, figure.alt_text):
            h.update(part.encode("utf-8") + b"\0")
        return h.hexdigest()

    def _cached(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads((self.cache_dir / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _store(self, key: str, payload: Dict[str, Any]) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except OSError as exc:                 # read-only home in CI: still works, just not cached
            logger.debug("Diagram cache not writable: %s", exc)

    def _ask(self, figure: Figure, image: PreparedImage, section: str) -> Dict[str, Any]:
        prompt = VISION_PROMPT.format(caption=figure.caption or "(none)", alt=figure.alt_text or "(none)",
                                      section=section or "(unknown)", types=", ".join(DIAGRAM_TYPES),
                                      max_n=MAX_STATEMENTS)
        data_url = "data:image/png;base64," + base64.b64encode(image.png).decode("ascii")
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=1200,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": VISION_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ]},
            ],
        )
        return json.loads(response.choices[0].message.content or "{}")

    def read(self, figure: Figure, image: Optional[PreparedImage], section: str) -> Optional[DiagramReading]:
        if image is None or self.client is None:
            return None
        key = self._cache_key(figure, image)
        payload = self._cached(key)
        if payload is None:
            try:
                payload = self._ask(figure, image, section)
            except Exception as exc:  # noqa: BLE001 — network, quota, bad JSON: fall back to local
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning("Vision reading of %s failed: %s", figure.locator, self.last_error)
                return None
            self._store(key, payload)
        return self.parse(payload)

    def parse(self, payload: Dict[str, Any]) -> DiagramReading:
        """Validate the model's JSON. Unknown types become "other"; IDs that do
        not match the document's ID pattern are dropped (never invented)."""
        dtype = str(payload.get("diagram_type") or "other").strip().lower()
        statements: List[DiagramStatement] = []
        for item in payload.get("requirements") or []:
            if not isinstance(item, dict):
                continue
            text = clean_text(str(item.get("statement") or ""))
            if len(text.split()) < 4:
                continue
            raw_id = clean_text(str(item.get("id") or ""))
            if raw_id.lower() in ("null", "none", "n/a") or not self.tags.is_id(raw_id):
                raw_id = ""
            statements.append(DiagramStatement(text=text, req_id=raw_id,
                                               title=clean_text(str(item.get("title") or ""))))
        return DiagramReading(engine=self.name, diagram_type=dtype if dtype in DIAGRAM_TYPES else "other",
                              summary=clean_text(str(payload.get("summary") or ""))[:400],
                              statements=_dedupe(statements))


def _merge(readings: Sequence[DiagramReading]) -> Optional[DiagramReading]:
    readings = [r for r in readings if r is not None and (r.statements or r.summary)]
    if not readings:
        return None
    return DiagramReading(
        engine="+".join(r.engine for r in readings),
        diagram_type=next((r.diagram_type for r in readings if r.diagram_type != "other"), "other"),
        summary=" ".join(r.summary for r in readings if r.summary)[:400],
        statements=_dedupe([s for r in readings for s in r.statements]),
    )


# ---------------------------------------------------------------------------
# Interpretation pass
# ---------------------------------------------------------------------------

def interpret_figures(
    blocks: Sequence[Block],
    options: ExtractionOptions,
    report: ExtractionReport,
    *,
    engines: Optional[Sequence[DiagramEngine]] = None,
) -> List[Block]:
    """Fill `Figure.reading` for every figure that survived the noise filter.
    Figures with nothing readable are removed (skip reason "image")."""
    mode = options.effective_diagram_mode
    if not any(isinstance(b, Figure) for b in blocks):
        return list(blocks)
    if mode == "off":
        report.skip("figure_ignored", sum(isinstance(b, Figure) for b in blocks))
        return [b for b in blocks if not isinstance(b, Figure)]

    tags = TagMatcher(options.effective_id_pattern)
    vision: Optional[VisionEngine] = None
    if engines is None:
        local: List[DiagramEngine] = [AltTextEngine(tags)]
        if tesseract_available():
            local.append(TesseractEngine(tags))
        if mode == "vision":
            vision = VisionEngine(tags, client=vision_client_factory())
            if not vision.available:
                report.warn("SRS_DIAGRAM_MODE=vision but no OPENAI_API_KEY is configured; figures were read "
                            "from alt text" + (" and OCR" if len(local) > 1 else "") + " only.")
                vision = None
    else:
        local = list(engines)
    ocr_missing = engines is None and not any(isinstance(e, TesseractEngine) for e in local)

    ctx = SectionContext(blocks)
    out: List[Block] = []
    seen = unread = 0
    for idx, block in enumerate(blocks):
        if not isinstance(block, Figure):
            out.append(block)
            continue
        seen += 1
        if seen > options.max_figures:
            report.skip("figure_over_limit")
            continue
        image, reason = prepare_image(block.image)
        if image is None and not block.alt_text:
            report.skip(reason)
            continue
        section = " > ".join(ctx.path(idx))
        reading = vision.read(block, image, section) if vision is not None else None
        if reading is None:
            reading = _merge([e.read(block, image, section) for e in local])
        if reading is None:
            unread += 1
            report.skip("image")
            continue
        out.append(replace(block, reading=reading))

    if seen > options.max_figures:
        report.warn(f"Only the first {options.max_figures} of {seen} figures were read (max_figures).")
    if vision is not None and vision.last_error:
        report.warn(f"The vision model could not read some figures ({vision.last_error}); "
                    "alt text / OCR was used for them.")
    if unread and mode == "local" and ocr_missing:
        report.warn(f"{unread} figure(s) had no alt text and could not be read: OCR (Tesseract) is not "
                    "installed. Set SRS_DIAGRAM_MODE=vision to read diagrams with a vision model.")
    return out


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class DiagramStrategy:
    """Turn figure readings into requirement candidates (see module docstring).

    Returns (candidates, figure_refs) where figure_refs maps canonical IDs to
    the figures that produced or illustrate them."""

    name = STRATEGY_DIAGRAM

    def __init__(self, tags: TagMatcher) -> None:
        self.tags = tags

    def _owner(self, ctx: SectionContext, idx: int, known: Set[str]) -> str:
        """The requirement whose tagged heading encloses this figure, if any."""
        for _lvl, text in reversed(ctx.levelled_path(idx)):
            m = self.tags.match_heading(text)
            if m and canonical_id(m[0]) in known:
                return canonical_id(m[0])
        return ""

    def extract(
        self,
        blocks: Sequence[Block],
        ctx: SectionContext,
        report: ExtractionReport,
        known_ids: Set[str],
    ) -> Tuple[List[Candidate], Dict[str, List[Dict[str, Any]]]]:
        known = set(known_ids)
        out: List[Candidate] = []
        refs: Dict[str, List[Dict[str, Any]]] = {}
        n_generated = 0

        for idx, block in enumerate(blocks):
            if not isinstance(block, Figure) or block.reading is None:
                continue
            reading = block.reading
            label = figure_label(block)
            path = ctx.path(idx)
            ref = {"figure": label, "caption": block.caption, "locator": str(block.locator),
                   "engine": reading.engine, "diagram_type": reading.diagram_type, "summary": reading.summary}
            owner = self._owner(ctx, idx, known)

            by_id: Dict[str, List[DiagramStatement]] = {}
            loose: List[DiagramStatement] = []
            for st in reading.statements:
                if st.req_id:
                    by_id.setdefault(canonical_id(st.req_id), []).append(st)
                else:
                    loose.append(st)
            # A caption such as "Figure 2: REQ-09 checkout 2FA" names the requirement
            # the whole figure specifies.
            caption_id = self.tags.match_heading(caption_text(block.caption))
            if caption_id and loose and not owner:
                by_id.setdefault(canonical_id(caption_id[0]), []).extend(loose)
                loose = []

            created: List[str] = []
            illustrates: List[str] = []
            for k, (cid, sts) in enumerate(by_id.items()):
                if cid in known:
                    illustrates.append(cid)
                    continue
                known.add(cid)
                created.append(cid)
                out.append(Candidate(
                    raw_id=sts[0].req_id or cid, strategy=self.name, position=(idx, k), locator=str(block.locator),
                    title=next((s.title for s in sts if s.title), ""),
                    description=" ".join(s.text.rstrip(".") + "." for s in sts),
                    section=path, metadata={META_ID_SOURCE: "document"},
                ))
            structural = reading.diagram_type in STRUCTURAL_TYPES
            if loose and owner:
                illustrates.append(owner)
            elif loose and not structural:
                for k, st in enumerate(loose, start=1):
                    cid = f"{label}.{k}"
                    created.append(cid)
                    n_generated += 1
                    out.append(Candidate(
                        raw_id=cid, strategy=self.name, position=(idx, len(by_id) + k), locator=str(block.locator),
                        title=st.title, description=st.text, section=path,
                        metadata={META_ID_SOURCE: "generated"},
                    ))
            if not created and not illustrates and owner and reading.summary:
                illustrates.append(owner)

            for cid in dict.fromkeys(created + illustrates):
                refs.setdefault(cid, []).append(ref)
            entry = {**ref, "section": " > ".join(path), "requirements": created,
                     "illustrates": list(dict.fromkeys(illustrates))}
            if loose and structural and not owner:
                entry["context_only"] = [s.text for s in loose]
            report.figures.append(entry)

        if n_generated:
            report.warn(f"{n_generated} requirement(s) read from figures have no ID in the document and were "
                        "given generated IDs (FIG-<figure>.<n>). Put the requirement ID in the figure or its "
                        "caption for stable traceability.")
        report.found(self.name, len(out))
        return out, refs


__all__ = [
    "AltTextEngine",
    "DiagramStrategy",
    "PreparedImage",
    "TesseractEngine",
    "VisionEngine",
    "attach_captions",
    "figure_label",
    "interpret_figures",
    "prepare_image",
    "statements_from_text",
    "tesseract_available",
]
