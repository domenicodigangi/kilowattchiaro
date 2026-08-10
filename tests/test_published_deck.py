"""The published deck must never carry the spoken script.

`docs/deck.html` is a *generated* artifact: it is built from the presenter
deck by stripping the speaker notes and the embedded talk track. The
presenter copy and the published copy differ only in what is invisible on
screen, which is exactly why they are easy to mix up — copy the wrong file
and the whole narration ships with the slides, silently.

This test is the tripwire. It runs in CI on every push, so the mistake is
caught by a red build rather than by a reader.
"""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
DECK_HTML = DOCS / "deck.html"
DECK_PDF = DOCS / "deck.pdf"

# Structures that exist only in the presenter deck.
PRESENTER_STRUCTURES = [
    ('speaker notes', 'class="notes"'),
    ('talk-track payload', 'id="talktrack"'),
    ('notes bar', "notesbar"),
    ("presenter popup", "kwc-presenter"),
]

# Markers that appear only inside speaker notes or the embedded script —
# never in the text printed on a slide.
SCRIPT_MARKERS = [
    ("timing marker", "⏱"),
    ("timing marker", "&#9201;"),
    ("cumulative time", "cum "),
    ("checkpoint", "CHECKPOINT"),
    ("staging direction", "do not read"),
    ("script pointer", "talk-track"),
]


@pytest.fixture(scope="module")
def deck_text() -> str:
    assert DECK_HTML.is_file(), f"{DECK_HTML} is missing"
    return DECK_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def deck_scan(deck_text: str) -> str:
    """Deck text with embedded images removed.

    The slides carry base64 image payloads. Random glyph runs inside them
    match short words by chance, so they are stripped before scanning —
    otherwise this test fails on a coin flip.
    """
    scan = re.sub(r"data:image/[^\"']+", "", deck_text)
    return re.sub(r"[A-Za-z0-9+/]{60,}={0,2}", "", scan)


@pytest.mark.parametrize("label,needle", PRESENTER_STRUCTURES, ids=lambda v: v)
def test_no_presenter_structures(deck_text: str, label: str, needle: str) -> None:
    assert needle not in deck_text, (
        f"{label} found in docs/deck.html — this looks like the presenter "
        f"deck, which carries the entire spoken script. Publish the file "
        f"built by build-share-deck.py instead."
    )


@pytest.mark.parametrize("label,needle", SCRIPT_MARKERS, ids=lambda v: v)
def test_no_script_markers(deck_scan: str, label: str, needle: str) -> None:
    assert needle not in deck_scan, f"{label} ({needle!r}) leaked into docs/deck.html"


def test_deck_is_the_generated_artifact(deck_text: str) -> None:
    assert "build-share-deck.py" in deck_text, (
        "docs/deck.html has no generator banner — it was not produced by "
        "build-share-deck.py, so nothing guarantees the script was stripped."
    )


def test_pdf_present_and_plausible() -> None:
    assert DECK_PDF.is_file(), f"{DECK_PDF} is missing"
    assert DECK_PDF.read_bytes()[:5] == b"%PDF-", "docs/deck.pdf is not a PDF"
    # The real export is ~877 KB; a truncated or placeholder file is not.
    assert DECK_PDF.stat().st_size > 200_000, "docs/deck.pdf looks truncated"


def test_pdf_carries_no_spoken_script() -> None:
    """Deeper check where pypdf is available; skipped in the lean CI env."""
    pypdf = pytest.importorskip("pypdf")
    text = "\n".join(
        page.extract_text() or "" for page in pypdf.PdfReader(str(DECK_PDF)).pages
    )
    for label, needle in SCRIPT_MARKERS:
        assert needle not in text, f"{label} ({needle!r}) leaked into docs/deck.pdf"
