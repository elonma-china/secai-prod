"""Offline exports of saved Markdown. No HTML execution or external resource fetching."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from markdown_it import MarkdownIt


@dataclass
class Block:
    kind: str
    text: str = ''
    level: int = 0
    rows: list[list[str]] | None = None


def _inline(token) -> str:
    # HTML is disabled: raw tags remain literal text. Images contribute only their alt text.
    parts: list[str] = []
    links: list[tuple[str, int]] = []
    for child in token.children or []:
        if child.type == 'link_open':
            links.append((child.attrGet('href') or '', len(parts)))
        elif child.type == 'link_close' and links:
            href, start = links.pop()
            if href and href != ''.join(parts[start:]):
                parts.append(f' ({href})')
        elif child.type in ('text', 'code_inline', 'image', 'html_inline'):
            parts.append(child.content)
        elif child.type in ('softbreak', 'hardbreak'):
            parts.append('\n')
    return ''.join(parts)


def blocks(content: str) -> list[Block]:
    tokens = MarkdownIt('commonmark', {'html': False}).enable('table').parse(content)
    result: list[Block] = []
    i, list_depth = 0, 0
    while i < len(tokens):
        token = tokens[i]
        if token.type in ('bullet_list_open', 'ordered_list_open'):
            list_depth += 1
        elif token.type in ('bullet_list_close', 'ordered_list_close'):
            list_depth -= 1
        elif token.type == 'table_open':
            rows: list[list[str]] = []
            i += 1
            while tokens[i].type != 'table_close':
                if tokens[i].type == 'tr_open':
                    rows.append([])
                elif tokens[i].type == 'inline':
                    rows[-1].append(_inline(tokens[i]))
                i += 1
            result.append(Block('table', rows=rows))
        elif token.type == 'heading_open':
            result.append(Block('heading', _inline(tokens[i + 1]), int(token.tag[1:])))
            i += 2
        elif token.type == 'paragraph_open':
            result.append(Block('paragraph', ('- ' if list_depth else '') + _inline(tokens[i + 1])))
            i += 2
        elif token.type in ('fence', 'code_block'):
            result.append(Block('code', token.content.rstrip('\n')))
        elif token.type == 'hr':
            result.append(Block('paragraph', '---'))
        i += 1
    return result


def plain_text(parsed: list[Block]) -> bytes:
    parts = ['\n'.join('\t'.join(row) for row in block.rows or [])
             if block.kind == 'table' else block.text for block in parsed]
    return ('\n\n'.join(parts) + '\n').encode('utf-8')


def docx_document(parsed: list[Block]) -> bytes:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.shared import Inches, Pt
    document = Document()
    for section in document.sections:
        section.top_margin = section.bottom_margin = Inches(.7)
    style = document.styles['Normal']
    style.font.name, style.font.size = 'DejaVu Sans', Pt(10)
    for block in parsed:
        if block.kind == 'heading':
            document.add_heading(block.text, level=min(block.level, 6))
        elif block.kind == 'table':
            rows = block.rows or []
            if not rows:
                continue
            table = document.add_table(rows=0, cols=max(map(len, rows)))
            table.style = 'Table Grid'
            for values in rows:
                cells = table.add_row().cells
                for cell, value in zip(cells, values):
                    cell.text = value
            # Word repeats the first row when a table spans multiple pages.
            table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        else:
            paragraph = document.add_paragraph(block.text)
            if block.kind == 'code':
                for run in paragraph.runs:
                    run.font.name, run.font.size = 'DejaVu Sans Mono', Pt(9)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_FONT_LOCK = Lock()


def _pdf_fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    with _FONT_LOCK:
        if 'ReportSans' not in pdfmetrics.getRegisteredFontNames():
            root = Path('/usr/share/fonts/truetype/dejavu')
            pdfmetrics.registerFont(TTFont('ReportSans', str(root / 'DejaVuSans.ttf')))
            pdfmetrics.registerFont(TTFont('ReportSansBold', str(root / 'DejaVuSans-Bold.ttf')))
            pdfmetrics.registerFontFamily('ReportSans', normal='ReportSans', bold='ReportSansBold')


def pdf_document(parsed: list[Block]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle
    _pdf_fonts()
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=42, rightMargin=42,
                                 topMargin=42, bottomMargin=42, title='Báo cáo')
    normal = ParagraphStyle('body', fontName='ReportSans', fontSize=10, leading=15,
                            alignment=TA_LEFT, spaceAfter=8, splitLongWords=True)
    cell_style = ParagraphStyle('cell', parent=normal, fontSize=9, leading=13, spaceAfter=0)

    def paragraph(text, style=normal):
        return Paragraph(escape(text).replace('\n', '<br/>'), style)

    story = []
    for block in parsed:
        if block.kind == 'heading':
            heading = ParagraphStyle('heading', parent=normal, fontName='ReportSansBold',
                                     fontSize=max(11, 19 - block.level * 2),
                                     leading=23 - block.level, spaceBefore=10, keepWithNext=True)
            story.append(paragraph(block.text, heading))
        elif block.kind == 'table':
            rows = block.rows or []
            if not rows:
                continue
            columns = max(map(len, rows))
            data = [[paragraph(value, cell_style) for value in row] + [''] * (columns - len(row))
                    for row in rows]
            width = document.width / columns
            # Prefer whole rows. Permit in-row splitting only for a cell taller than
            # a page, otherwise ReportLab may repeat a header in the same page.
            oversized = any(max(cell.wrap(width - 12, document.height)[1]
                                if isinstance(cell, Paragraph) else 0 for cell in row)
                            > document.height - 50 for row in data[1:])
            table = LongTable(data, colWidths=[width] * columns,
                              repeatRows=1, splitByRow=1, splitInRow=int(oversized), hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8EEF4')),
                ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#ADB8C5')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.extend([table, Spacer(1, 10)])
        else:
            # Line-by-line code wraps safely even for a code block longer than a page.
            if block.kind == 'code':
                for line in block.text.split('\n'):
                    story.append(paragraph(line or ' '))
            else:
                story.append(paragraph(block.text))

    def footer(canvas, doc):
        canvas.setFont('ReportSans', 8)
        canvas.drawRightString(A4[0] - 42, 24, str(doc.page))
    document.build(story or [paragraph(' ')], onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def render(content: str, format: str) -> bytes:
    parsed = blocks(content)
    return {'txt': plain_text, 'docx': docx_document, 'pdf': pdf_document}[format](parsed)
