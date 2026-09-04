"""
Aegis — Export any chat message to PDF, DOCX, or XLSX.

Parses the message's markdown into a small block-level representation once
(parse_markdown_blocks), then three independent renderers consume that same
representation for each target format. Deliberately built on libraries
already vendored elsewhere in this app (markdown-it-py for parsing, pymupdf
for PDF, xlsxwriter for spreadsheets) plus python-docx — all pure-Python,
no compiled/native dependencies, to avoid repeating this session's earlier
PyInstaller packaging pain with anything that needs a system binary.

Scope: headings, paragraphs, bullet/ordered lists, blockquotes, fenced code
blocks, and GFM tables — everything a normal assistant response actually
uses. Inline bold/italic/code formatting is preserved in DOCX (python-docx
supports per-run styling directly) and in the table cells of XLSX; the PDF
renderer and non-table XLSX rows use plain concatenated text for those runs
— PyMuPDF's textbox API is built around one font per box, so mixing bold and
plain text within a single wrapped paragraph would need manual per-run
positioning that isn't worth the complexity for a chat export feature.
"""

from dataclasses import dataclass, field
from io import BytesIO
from typing import List, Union

from markdown_it import MarkdownIt


# ── Block-level markdown representation ────────────────────────────────────

@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass
class Heading:
    level: int
    runs: List[Run] = field(default_factory=list)


@dataclass
class Paragraph:
    runs: List[Run] = field(default_factory=list)


@dataclass
class ListBlock:
    ordered: bool
    items: List[List[Run]] = field(default_factory=list)


@dataclass
class CodeBlock:
    text: str


@dataclass
class Quote:
    runs: List[Run] = field(default_factory=list)


@dataclass
class Table:
    header: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)


Block = Union[Heading, Paragraph, ListBlock, CodeBlock, Quote, Table]


def _plain_text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def _parse_inline_runs(inline_token) -> List[Run]:
    """Walks an 'inline' token's .children into a flat run list with
    bold/italic/code flags — markdown-it-py's inline tokenizer emits
    strong_open/em_open/code_inline/text/softbreak as a flat sequence with
    explicit open/close markers rather than a nested tree, so a simple
    running-flag walk is all that's needed."""
    runs: List[Run] = []
    if not inline_token or not inline_token.children:
        if inline_token and inline_token.content:
            runs.append(Run(text=inline_token.content))
        return runs

    bold = italic = False
    for child in inline_token.children:
        if child.type == "strong_open":
            bold = True
        elif child.type == "strong_close":
            bold = False
        elif child.type == "em_open":
            italic = True
        elif child.type == "em_close":
            italic = False
        elif child.type == "code_inline":
            runs.append(Run(text=child.content, bold=bold, italic=italic, code=True))
        elif child.type == "text":
            if child.content:
                runs.append(Run(text=child.content, bold=bold, italic=italic))
        elif child.type in ("softbreak", "hardbreak"):
            runs.append(Run(text=" "))
        # link_open/link_close/image and anything else: the visible text
        # inside still arrives as its own "text" token, so no special
        # handling is needed to keep it — only the link styling is dropped.
    return runs


def parse_markdown_blocks(md_text: str) -> List[Block]:
    """Parses a markdown string (an assistant message's raw content) into a
    flat list of block-level elements, ready for any of the render_* functions
    below."""
    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(md_text or "")
    blocks: List[Block] = []
    i = 0
    n = len(tokens)

    while i < n:
        t = tokens[i]

        if t.type == "heading_open":
            level = int(t.tag[1]) if t.tag[1:].isdigit() else 1
            blocks.append(Heading(level=level, runs=_parse_inline_runs(tokens[i + 1])))
            i += 3
            continue

        if t.type == "paragraph_open":
            blocks.append(Paragraph(runs=_parse_inline_runs(tokens[i + 1])))
            i += 3
            continue

        if t.type in ("bullet_list_open", "ordered_list_open"):
            ordered = t.type == "ordered_list_open"
            close_type = "ordered_list_close" if ordered else "bullet_list_close"
            items: List[List[Run]] = []
            i += 1
            while i < n and tokens[i].type != close_type:
                if tokens[i].type == "list_item_open":
                    item_runs: List[Run] = []
                    i += 1
                    while i < n and tokens[i].type != "list_item_close":
                        if tokens[i].type == "inline":
                            item_runs.extend(_parse_inline_runs(tokens[i]))
                        i += 1
                    items.append(item_runs)
                i += 1
            blocks.append(ListBlock(ordered=ordered, items=items))
            i += 1
            continue

        if t.type == "blockquote_open":
            runs: List[Run] = []
            i += 1
            while i < n and tokens[i].type != "blockquote_close":
                if tokens[i].type == "inline":
                    runs.extend(_parse_inline_runs(tokens[i]))
                i += 1
            blocks.append(Quote(runs=runs))
            i += 1
            continue

        if t.type == "fence" or t.type == "code_block":
            blocks.append(CodeBlock(text=t.content.rstrip("\n")))
            i += 1
            continue

        if t.type == "table_open":
            header: List[str] = []
            rows: List[List[str]] = []
            in_head = False
            current_row: List[str] = []
            i += 1
            while i < n and tokens[i].type != "table_close":
                tt = tokens[i].type
                if tt == "thead_open":
                    in_head = True
                elif tt == "thead_close":
                    in_head = False
                elif tt == "tr_open":
                    current_row = []
                elif tt == "tr_close":
                    (header.extend(current_row) if in_head else rows.append(current_row))
                elif tt == "inline":
                    current_row.append(_plain_text(_parse_inline_runs(tokens[i])))
                i += 1
            blocks.append(Table(header=header, rows=rows))
            i += 1
            continue

        i += 1

    return blocks


# ── DOCX ─────────────────────────────────────────────────────────────────

def render_docx(blocks: List[Block], title: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    if title:
        doc.add_heading(title, level=0)

    for b in blocks:
        if isinstance(b, Heading):
            doc.add_heading(_plain_text(b.runs), level=min(max(b.level, 1), 9))
        elif isinstance(b, Paragraph):
            p = doc.add_paragraph()
            for r in b.runs:
                run = p.add_run(r.text)
                run.bold = r.bold
                run.italic = r.italic
                if r.code:
                    run.font.name = "Courier New"
        elif isinstance(b, ListBlock):
            style = "List Number" if b.ordered else "List Bullet"
            for item_runs in b.items:
                p = doc.add_paragraph(style=style)
                for r in item_runs:
                    run = p.add_run(r.text)
                    run.bold = r.bold
                    run.italic = r.italic
        elif isinstance(b, Quote):
            p = doc.add_paragraph(style="Intense Quote")
            p.add_run(_plain_text(b.runs))
        elif isinstance(b, CodeBlock):
            p = doc.add_paragraph()
            run = p.add_run(b.text)
            run.font.name = "Courier New"
            run.font.size = Pt(9.5)
        elif isinstance(b, Table):
            cols = len(b.header) if b.header else (len(b.rows[0]) if b.rows else 0)
            if cols == 0:
                continue
            table = doc.add_table(rows=0, cols=cols)
            table.style = "Light Grid Accent 1"
            if b.header:
                row_cells = table.add_row().cells
                for idx, cell_text in enumerate(b.header):
                    row_cells[idx].text = cell_text
                    for para in row_cells[idx].paragraphs:
                        for run in para.runs:
                            run.bold = True
            for row in b.rows:
                row_cells = table.add_row().cells
                for idx, cell_text in enumerate(row):
                    if idx < cols:
                        row_cells[idx].text = cell_text

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── PDF (PyMuPDF) ───────────────────────────────────────────────────────

# PyMuPDF's built-in "helv"/"hebo" fonts (Base-14 Helvetica, WinAnsiEncoding)
# silently drop characters outside Latin-1 — they render as a blank/"?" glyph
# instead of raising an error, so this is easy to miss until the PDF is
# actually opened. Confirmed against this app's own assistant text, which
# uses em-dashes constantly (as seen throughout this very session). DOCX and
# XLSX have no such limitation (verified directly), so this only applies here.
_PDF_UNICODE_FALLBACKS = {
    "—": "--", "–": "-",       # em dash, en dash
    "‘": "'", "’": "'",         # curly single quotes
    "“": '"', "”": '"',         # curly double quotes
    "…": "...",                       # ellipsis
    "→": "->", "←": "<-",       # arrows
    "•": "-",                         # bullet (kept for safety even
                                            # though render_pdf uses its own
                                            # ASCII "-" prefix already)
    " ": " ",                         # non-breaking space
}


def _pdf_safe_text(text: str) -> str:
    for src, dst in _PDF_UNICODE_FALLBACKS.items():
        text = text.replace(src, dst)
    # Anything still outside Latin-1 (genuine emoji, non-Latin scripts) has
    # no good ASCII fallback — drop it rather than let PyMuPDF silently
    # render a blank glyph in its place.
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def render_pdf(blocks: List[Block], title: str) -> bytes:
    import fitz  # PyMuPDF

    PAGE_W, PAGE_H = 595, 842  # A4 in points
    MARGIN = 50
    LINE_H = 14
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y = MARGIN

    def _estimate_lines(text: str, chars_per_line: int) -> int:
        """
        Sums a wrapped-line estimate per explicit line instead of treating
        the whole string as one wrap run — a CodeBlock's embedded '\\n's
        otherwise get "absorbed" into a single width-based estimate that
        never accounts for the forced breaks, undercounting real line count
        (a 2-line code snippet with short lines estimated as fitting on
        one line, confirmed against a real render).
        """
        return sum(max(1, -(-len(line) // chars_per_line)) for line in text.split("\n")) or 1

    def write_block(text: str, fontsize: float, bold: bool, extra_gap: float = 6, indent: float = 0):
        nonlocal y
        text = _pdf_safe_text(text)
        if not text.strip():
            y += extra_gap
            return
        rect_w = PAGE_W - 2 * MARGIN - indent
        # Rough line estimate for pagination (PyMuPDF doesn't pre-measure
        # wrapped height cheaply) — an average character width heuristic.
        chars_per_line = max(int(rect_w / (fontsize * 0.5)), 10)
        est_lines = _estimate_lines(text, chars_per_line)
        # fontsize*1.45+6, not the old fontsize+3 — empirically verified
        # (via insert_textbox's own returned unused-space value, negative on
        # overflow) against real single- and multi-line renders from 9pt to
        # 18pt: fontsize+3 undershot by 2-11pt depending on size, and
        # insert_textbox does NOT clip on overflow, it silently renders
        # NOTHING for that call — the actual bug behind text going missing
        # from real PDF exports, not just a cosmetic overlap.
        needed = est_lines * (fontsize * 1.45 + 6) + extra_gap
        if y + needed > PAGE_H - MARGIN:
            new_page()
        rect = fitz.Rect(MARGIN + indent, y, PAGE_W - MARGIN, PAGE_H - MARGIN)
        font = "helv" if not bold else "hebo"
        page.insert_textbox(rect, text, fontsize=fontsize, fontname=font, align=0)
        y += needed

    def write_table(table: Table):
        """
        Draws an actual bordered grid — cell borders, a shaded header row,
        one insert_textbox per cell — instead of one line of "a | b | c"
        per row (what this used to do: readable but didn't look like a
        table at all next to a real DOCX/XLSX export of the same content).
        Equal-width columns spanning the page's content width; row height
        sized per row from the same wrapped-line-count estimate write_block
        uses, so a long cell doesn't get clipped.
        """
        nonlocal y
        all_rows = ([table.header] if table.header else []) + table.rows
        if not all_rows:
            return
        n_cols = max(len(r) for r in all_rows)
        table_w = PAGE_W - 2 * MARGIN
        col_w = table_w / n_cols
        cell_pad = 4
        fontsize = 9.5

        for row_idx, row in enumerate(all_rows):
            is_header = bool(table.header) and row_idx == 0
            cells = [_pdf_safe_text(c) for c in row] + [""] * (n_cols - len(row))

            chars_per_line = max(int((col_w - 2 * cell_pad) / (fontsize * 0.5)), 6)
            max_lines = max(_estimate_lines(c, chars_per_line) if c else 1 for c in cells)
            # Same fontsize*1.45+6 formula as write_block, tuned the same way
            # — see its comment. A first version of this function used
            # fontsize+3 and rendered every cell border correctly with NO
            # text at all inside: insert_textbox doesn't clip on overflow,
            # it silently renders nothing for that call.
            row_h = max_lines * (fontsize * 1.45 + 6) + 2 * cell_pad

            if y + row_h > PAGE_H - MARGIN:
                new_page()

            row_rect = fitz.Rect(MARGIN, y, MARGIN + table_w, y + row_h)
            page.draw_rect(row_rect, color=(0.6, 0.6, 0.6), fill=(0.92, 0.92, 0.92) if is_header else None, width=0.6)

            for col_idx, cell_text in enumerate(cells):
                cx = MARGIN + col_idx * col_w
                if col_idx > 0:
                    page.draw_line(fitz.Point(cx, y), fitz.Point(cx, y + row_h), color=(0.6, 0.6, 0.6), width=0.6)
                cell_rect = fitz.Rect(cx + cell_pad, y + cell_pad, cx + col_w - cell_pad, y + row_h - cell_pad)
                page.insert_textbox(
                    cell_rect, cell_text, fontsize=fontsize,
                    fontname="hebo" if is_header else "helv", align=0,
                )
            y += row_h
        y += 8

    if title:
        write_block(title, 18, True, extra_gap=14)

    for b in blocks:
        if isinstance(b, Heading):
            size = {1: 16, 2: 14, 3: 12.5}.get(b.level, 11.5)
            write_block(_plain_text(b.runs), size, True, extra_gap=8)
        elif isinstance(b, Paragraph):
            write_block(_plain_text(b.runs), 10.5, False, extra_gap=8)
        elif isinstance(b, ListBlock):
            for idx, item_runs in enumerate(b.items):
                prefix = f"{idx + 1}. " if b.ordered else "•  "
                write_block(prefix + _plain_text(item_runs), 10.5, False, extra_gap=3, indent=14)
        elif isinstance(b, Quote):
            write_block("“" + _plain_text(b.runs) + "”", 10.5, False, extra_gap=8, indent=14)
        elif isinstance(b, CodeBlock):
            write_block(b.text, 9, False, extra_gap=10, indent=8)
        elif isinstance(b, Table):
            write_table(b)

    return doc.tobytes()


# ── XLSX ─────────────────────────────────────────────────────────────────

def render_xlsx(blocks: List[Block], title: str) -> bytes:
    import xlsxwriter

    buf = BytesIO()
    workbook = xlsxwriter.Workbook(buf, {"in_memory": True})
    sheet = workbook.add_worksheet("Export")
    bold = workbook.add_format({"bold": True})
    header_fmt = workbook.add_format({"bold": True, "bg_color": "#F1F1F2"})
    wrap = workbook.add_format({"text_wrap": True, "valign": "top"})

    row = 0
    if title:
        sheet.write(row, 0, title, bold)
        row += 2

    def write_text_row(text: str, fmt=None):
        nonlocal row
        sheet.write(row, 0, text, fmt or wrap)
        row += 1

    max_col_used = 1
    for b in blocks:
        if isinstance(b, Heading):
            write_text_row(_plain_text(b.runs), bold)
            row += 1
        elif isinstance(b, Paragraph):
            write_text_row(_plain_text(b.runs))
        elif isinstance(b, ListBlock):
            for idx, item_runs in enumerate(b.items):
                prefix = f"{idx + 1}. " if b.ordered else "- "
                write_text_row(prefix + _plain_text(item_runs))
            row += 1
        elif isinstance(b, Quote):
            write_text_row(_plain_text(b.runs))
        elif isinstance(b, CodeBlock):
            write_text_row(b.text)
            row += 1
        elif isinstance(b, Table):
            # The one block type that actually belongs in a spreadsheet —
            # rendered as a real grid instead of one cell per line.
            if b.header:
                for c, cell in enumerate(b.header):
                    sheet.write(row, c, cell, header_fmt)
                max_col_used = max(max_col_used, len(b.header))
                row += 1
            for data_row in b.rows:
                for c, cell in enumerate(data_row):
                    sheet.write(row, c, cell)
                max_col_used = max(max_col_used, len(data_row))
                row += 1
            row += 1

    sheet.set_column(0, 0, 90)
    if max_col_used > 1:
        sheet.set_column(1, max_col_used - 1, 20)

    workbook.close()
    return buf.getvalue()


_RENDERERS = {"pdf": render_pdf, "docx": render_docx, "xlsx": render_xlsx}

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def export_markdown(content: str, fmt: str, title: str = "") -> bytes:
    """Single entry point: markdown text + a format name -> file bytes."""
    if fmt not in _RENDERERS:
        raise ValueError(f"Unsupported export format: {fmt}")
    blocks = parse_markdown_blocks(content)
    # Content that already opens with its own top-level heading doesn't need
    # a second, separate title rendered above it — every renderer already
    # just skips the title block entirely when `title` is empty, so dropping
    # it here means the content's own heading is what carries the title
    # instead of both appearing back to back (confirmed by rendering a real
    # export: "# Quarterly Report" as the first line plus title="Quarterly
    # Report" produced the heading twice).
    if blocks and isinstance(blocks[0], Heading) and blocks[0].level == 1:
        title = ""
    return _RENDERERS[fmt](blocks, title)
