import io
import os
import json
import uuid
import urllib.request
import traceback
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

from supabase import create_client, Client

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import qrcode

from PIL import Image, ImageDraw, ImageFont, features
from reportlab.lib.utils import ImageReader

import requests

import pypdfium2 as pdfium
import cv2
import numpy as np

# =====================================================================
# APP
# =====================================================================

app = FastAPI(
    title="School OMR & Question Engine"
)


# =====================================================================
# 1. DEVANAGARI FONT SETUP
# =====================================================================

FONT_NAME = "NotoDevanagari"
FONT_BOLD = "NotoDevanagari-Bold"

# High-resolution rasterization for Hindi text.
FONT_RENDER_SCALE = 6


def setup_fonts():
    font_dir = "/tmp/fonts"
    os.makedirs(font_dir, exist_ok=True)

    font_path_reg = os.path.join(
        font_dir,
        "NotoSansDevanagari-Regular.ttf"
    )

    font_path_bld = os.path.join(
        font_dir,
        "NotoSansDevanagari-Bold.ttf"
    )

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    url_reg = (
        "https://raw.githubusercontent.com/googlefonts/"
        "noto-fonts/main/hinted/ttf/"
        "NotoSansDevanagari/"
        "NotoSansDevanagari-Regular.ttf"
    )

    url_bld = (
        "https://raw.githubusercontent.com/googlefonts/"
        "noto-fonts/main/hinted/ttf/"
        "NotoSansDevanagari/"
        "NotoSansDevanagari-Bold.ttf"
    )

    # ---------------------------------------------------------------
    # REGULAR FONT
    # ---------------------------------------------------------------

    if (
        not os.path.exists(font_path_reg)
        or os.path.getsize(font_path_reg) < 10000
    ):
        req = urllib.request.Request(
            url_reg,
            headers=headers
        )

        with urllib.request.urlopen(
            req,
            timeout=60
        ) as resp:

            with open(
                font_path_reg,
                "wb"
            ) as f:

                f.write(resp.read())

    # ---------------------------------------------------------------
    # BOLD FONT
    # ---------------------------------------------------------------

    if (
        not os.path.exists(font_path_bld)
        or os.path.getsize(font_path_bld) < 10000
    ):
        req = urllib.request.Request(
            url_bld,
            headers=headers
        )

        with urllib.request.urlopen(
            req,
            timeout=60
        ) as resp:

            with open(
                font_path_bld,
                "wb"
            ) as f:

                f.write(resp.read())

    # ---------------------------------------------------------------
    # Keep ReportLab fonts for the existing PDF engine.
    # Hindi itself will be rendered by Pillow + RAQM.
    # ---------------------------------------------------------------

    pdfmetrics.registerFont(
        TTFont(
            FONT_NAME,
            font_path_reg,
            shapable=True
        )
    )

    pdfmetrics.registerFont(
        TTFont(
            FONT_BOLD,
            font_path_bld,
            shapable=True
        )
    )

    return (
        font_path_reg,
        font_path_bld
    )


try:

    FONT_REG_PATH, FONT_BOLD_PATH = setup_fonts()

    print(
        "Devanagari Fonts Loaded Successfully"
    )

    # RAQM is required for correct complex-script layout.
    if not features.check("raqm"):

        raise RuntimeError(
            "Pillow RAQM support is not available. "
            "Add libfribidi0 to Render apt-packages."
        )

    print(
        "Pillow RAQM Hindi shaping is available"
    )

except Exception as e:

    print(
        f"Font Setup Error: {e}"
    )

    raise


# =====================================================================
# 2. HINDI TEXT RENDERER
#
# IMPORTANT:
# No manual Unicode reordering.
# No regex moving of 'ि'.
#
# Pillow + RAQM handles:
# - Devanagari matras
# - conjuncts
# - glyph substitution
# - glyph positioning
# =====================================================================

def draw_hindi_text(
    c,
    x,
    y,
    text,
    font_size,
    bold=False
):

    if text is None:
        return

    text = str(text)

    if not text:
        return

    font_path = (
        FONT_BOLD_PATH
        if bold
        else FONT_REG_PATH
    )

    font_px = max(
        1,
        int(
            round(
                font_size
                * FONT_RENDER_SCALE
            )
        )
    )

    font = ImageFont.truetype(
        font_path,
        font_px
    )

    # ---------------------------------------------------------------
    # Measure text using RAQM shaping
    # ---------------------------------------------------------------

    dummy = Image.new(
        "RGBA",
        (10, 10),
        (255, 255, 255, 0)
    )

    draw = ImageDraw.Draw(
        dummy
    )

    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font,
        anchor="ls",
        direction="ltr",
        language="hi"
    )

    pad = (
        2
        * FONT_RENDER_SCALE
    )

    image_width = max(
        1,
        bbox[2]
        - bbox[0]
        + (2 * pad)
    )

    image_height = max(
        1,
        bbox[3]
        - bbox[1]
        + (2 * pad)
    )

    # ---------------------------------------------------------------
    # Transparent text image
    # ---------------------------------------------------------------

    img = Image.new(
        "RGBA",
        (
            image_width,
            image_height
        ),
        (255, 255, 255, 0)
    )

    draw = ImageDraw.Draw(
        img
    )

    baseline_x = (
        pad
        - bbox[0]
    )

    baseline_y = (
        pad
        - bbox[1]
    )

    # ---------------------------------------------------------------
    # REAL DEVANAGARI SHAPING
    # ---------------------------------------------------------------

    draw.text(
        (
            baseline_x,
            baseline_y
        ),
        text,
        font=font,
        fill=(0, 0, 0, 255),
        anchor="ls",
        direction="ltr",
        language="hi"
    )

    # ---------------------------------------------------------------
    # Place the shaped text on the existing ReportLab canvas.
    # The baseline is preserved.
    # ---------------------------------------------------------------

    c.drawImage(
        ImageReader(img),
        x - (
            baseline_x
            / FONT_RENDER_SCALE
        ),
        y - (
            (
                image_height
                - baseline_y
            )
            / FONT_RENDER_SCALE
        ),
        width=(
            image_width
            / FONT_RENDER_SCALE
        ),
        height=(
            image_height
            / FONT_RENDER_SCALE
        ),
        mask="auto"
    )


# =====================================================================
# 3. SUPABASE AND SECURITY
# =====================================================================

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://your-project.supabase.co"
)

SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    "your-service-role-key"
)

API_SECRET_KEY = os.getenv(
    "APP_API_SECRET_KEY",
    "MySecureSchoolOmrKey_2026_Secure"
)

# Existing working bucket
BUCKET_NAME = os.getenv(
    "SUPABASE_BUCKET_NAME",
    "omr-sheets"
)


API_KEY_NAME = "x-api-key"

api_key_header = APIKeyHeader(
    name=API_KEY_NAME,
    auto_error=False
)


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


# =====================================================================
# 4. CORS
# =====================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=[
        "POST",
        "OPTIONS"
    ],
    allow_headers=["*"],
)


# =====================================================================
# 5. API KEY SECURITY
# =====================================================================

async def verify_api_key(
    api_key: str = Security(
        api_key_header
    )
):

    if (
        not api_key
        or api_key != API_SECRET_KEY
    ):

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "अनधिकृत पहुँच (Unauthorized): "
                "मान्य x-api-key आवश्यक है।"
            )
        )

    return api_key


# =====================================================================
# 6. HYBRID PDF GENERATOR
# =====================================================================

def generate_hybrid_omr_pdf(payload: dict) -> bytes:
    """Generate the existing hybrid OMR PDF with a corrected, dynamic footer.

    Uses the same surrounding globals as the existing application:
    io, json, qrcode, canvas, A4, colors, Image, ImageDraw, ImageFont,
    datetime, FONT_BOLD_PATH, FONT_REG_PATH, FONT_RENDER_SCALE,
    and draw_hindi_text, pdfmetrics.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # ------------------------------------------------------------------
    # LOCAL LAYOUT HELPERS (kept inside this function)
    # ------------------------------------------------------------------
    def _is_devanagari_char(ch):
        code = ord(ch)
        return (0x0900 <= code <= 0x097F) or (0xA8E0 <= code <= 0xA8FF)

    def _split_script_runs(text):
        """Split mixed text into Devanagari and Latin/ASCII runs."""
        text = str(text or "")
        if not text:
            return []
        runs = []
        current = text[0]
        current_is_hindi = _is_devanagari_char(text[0])
        for ch in text[1:]:
            is_hindi = _is_devanagari_char(ch)
            if is_hindi == current_is_hindi:
                current += ch
            else:
                runs.append((current, current_is_hindi))
                current = ch
                current_is_hindi = is_hindi
        runs.append((current, current_is_hindi))
        return runs

    _hindi_measure_cache = {}

    def _measure_hindi_run(text, font_size, bold=False):
        """Measure Hindi with the existing Noto/RAQM font; cache only measurements."""
        text = str(text or "")
        if not text:
            return 0.0
        cache_key = (text, float(font_size), bool(bold))
        if cache_key in _hindi_measure_cache:
            return _hindi_measure_cache[cache_key]
        font_path = FONT_BOLD_PATH if bold else FONT_REG_PATH
        font_px = max(1, int(round(font_size * FONT_RENDER_SCALE)))
        font = ImageFont.truetype(font_path, font_px)
        dummy = Image.new("RGBA", (10, 10), (255, 255, 255, 0))
        d = ImageDraw.Draw(dummy)
        bbox = d.textbbox(
            (0, 0), text, font=font, anchor="ls",
            direction="ltr", language="hi"
        )
        measured = max(0.0, (bbox[2] - bbox[0]) / FONT_RENDER_SCALE)
        _hindi_measure_cache[cache_key] = measured
        return measured

    def _has_latin_letters(text):
        return any(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in str(text or ""))

    def measure_text(text, font_size, bold=False):
        """Measure mixed text by script; pure Hindi keeps the original measurement path."""
        text = str(text or "")
        if not _has_latin_letters(text):
            return _measure_hindi_run(text, font_size, bold)
        total_width = 0.0
        latin_font = "Helvetica-Bold" if bold else "Helvetica"
        for run, is_hindi in _split_script_runs(text):
            if is_hindi:
                total_width += _measure_hindi_run(run, font_size, bold)
            else:
                total_width += pdfmetrics.stringWidth(run, latin_font, font_size)
        return total_width

    def draw_text_auto(c, x, y, text, font_size, bold=False):
        """Use unchanged Hindi rendering for Hindi-only text and Helvetica for Latin text."""
        if text is None:
            return
        text = str(text)
        # CRITICAL: pure Hindi strings go through the exact original renderer in
        # one call, preserving its shaping, matras, conjuncts, spacing and baseline.
        if not _has_latin_letters(text):
            draw_hindi_text(c, x, y, text, font_size, bold=bold)
            return _measure_hindi_run(text, font_size, bold)

        cursor_x = x
        latin_font = "Helvetica-Bold" if bold else "Helvetica"
        for run, is_hindi in _split_script_runs(text):
            if not run:
                continue
            if is_hindi:
                # IMPORTANT: use the user's existing Pillow + RAQM + Noto renderer.
                draw_hindi_text(c, cursor_x, y, run, font_size, bold=bold)
                cursor_x += _measure_hindi_run(run, font_size, bold)
            else:
                c.setFont(latin_font, font_size)
                c.drawString(cursor_x, y, run)
                cursor_x += pdfmetrics.stringWidth(run, latin_font, font_size)
        return cursor_x - x

    def wrap_text(text, max_width, font_size, bold=False):
        text = str(text or "")
        if not text.strip():
            return [""]

        words = text.split()
        lines = []
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if measure_text(candidate, font_size, bold) <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)

            if measure_text(word, font_size, bold) <= max_width:
                current = word
            else:
                piece = ""
                for ch in word:
                    test_piece = piece + ch
                    if (
                        piece
                        and measure_text(test_piece, font_size, bold) > max_width
                    ):
                        lines.append(piece)
                        piece = ch
                    else:
                        piece = test_piece
                current = piece

        if current:
            lines.append(current)
        return lines if lines else [""]

    def draw_wrapped(
        text, x, y, max_width, font_size, bold=False, line_height=None
    ):
        lines = wrap_text(text, max_width, font_size, bold)
        if line_height is None:
            line_height = font_size * 1.08
        for line_index, line in enumerate(lines):
            draw_text_auto(
                c, x, y - line_index * line_height,
                line, font_size, bold=bold
            )
        return len(lines)

    def draw_option(label, text, x, y, max_width, font_size, line_height):
        draw_text_auto(c, x, y, f"({label})", font_size, bold=False)
        wrapped = wrap_text(text, max_width - 13, font_size, True)
        for line_index, line in enumerate(wrapped):
            draw_text_auto(
                c, x + 12, y - line_index * line_height,
                line, font_size, bold=True
            )
        return len(wrapped)

    def get_question(idx):
        q_data = (
            raw_questions[idx]
            if idx < len(raw_questions) and isinstance(raw_questions[idx], dict)
            else {}
        )
        q_text = str(
            q_data.get("question_text")
            or q_data.get("question")
            or f"प्रश्न संख्या {idx + 1}"
        )
        options = [
            str(q_data.get("opt_a") or q_data.get("option_a") or "विकल्प A"),
            str(q_data.get("opt_b") or q_data.get("option_b") or "विकल्प B"),
            str(q_data.get("opt_c") or q_data.get("option_c") or "विकल्प C"),
            str(q_data.get("opt_d") or q_data.get("option_d") or "विकल्प D"),
        ]
        return q_text, options

    def _answer_text(value):
        """Extract a scalar answer from common API/database answer objects."""
        if isinstance(value, dict):
            for key in (
                "correct_answer", "correct_option", "correct_option_label",
                "answer", "label", "option", "value", "text"
            ):
                if key in value and value[key] is not None:
                    return value[key]
            return None
        return value

    def _normalize_answer(value, options):
        """Return A/B/C/D for an answer label, number, or matching option text."""
        value = _answer_text(value)
        if value is None:
            return None
        text = " ".join(str(value).strip().split())
        if not text:
            return None

        # If the correct answer is the actual option text, map it to its label.
        folded = text.casefold()
        for letter, option_text in zip("ABCD", options):
            if folded == " ".join(str(option_text).strip().split()).casefold():
                return letter

        compact = (
            text.upper().replace(" ", "").replace("_", "")
            .replace("-", "").replace("(", "").replace(")", "")
            .replace(".", "")
        )
        aliases = {
            "A": {"A", "OPTIONA", "OPTA", "विकल्पA", "विकल्पए", "ए"},
            "B": {"B", "OPTIONB", "OPTB", "विकल्पB", "विकल्पबी", "बी"},
            "C": {"C", "OPTIONC", "OPTC", "विकल्पC", "विकल्पसी", "सी"},
            "D": {"D", "OPTIOND", "OPTD", "विकल्पD", "विकल्पडी", "डी"},
        }
        for letter, accepted in aliases.items():
            if compact in accepted:
                return letter

        # Numeric option values are treated as 1=A, 2=B, 3=C, 4=D.
        if compact in {"1", "2", "3", "4"}:
            return "ABCD"[int(compact) - 1]
        return None

    def _external_answer_at(source, idx, q_data):
        """Read a top-level correct_answers/answer_key value for one question."""
        if source is None:
            return None
        if isinstance(source, (list, tuple)):
            if idx >= len(source):
                return None
            item = source[idx]
            if isinstance(item, dict):
                # Support answer-key entries such as {question_no: 1, answer: "B"}.
                return _answer_text(item)
            return item
        if isinstance(source, dict):
            qid = q_data.get("question_id", q_data.get("id"))
            candidate_keys = []
            if qid is not None:
                candidate_keys.extend((str(qid), qid))
            candidate_keys.extend((str(idx + 1), idx + 1, str(idx), idx))
            for key in candidate_keys:
                if key in source:
                    return _answer_text(source[key])
            # Also accept a single answer object, not just a numbered mapping.
            return _answer_text(source)
        if isinstance(source, str):
            text = source.strip()
            if not text:
                return None
            # Comma/semicolon/newline/space-separated answer keys.
            if any(sep in text for sep in (",", ";", "\n", "\r", " ")):
                parts = text.replace(";", ",").replace("\n", ",").replace("\r", ",")
                parts = [part.strip() for part in parts.split(",")]
                if len(parts) == 1:
                    parts = text.split()
                parts = [part for part in parts if part]
                return parts[idx] if idx < len(parts) else None
            # A compact key such as "BACDAB..." is read one character per item.
            if len(text) >= total_q and all(ch.upper() in "ABCD1234" for ch in text[:total_q]):
                return text[idx]
            if total_q == 1:
                return text
            return None
        return None

    # ------------------------------------------------------------------
    # CORNER SCANNER MARKERS (unchanged)
    # ------------------------------------------------------------------
    m_size = 14
    c.setFillColor(colors.black)
    c.rect(18, height - 18 - m_size, m_size, m_size, fill=1, stroke=0)
    c.rect(width - 18 - m_size, height - 18 - m_size,
           m_size, m_size, fill=1, stroke=0)
    c.rect(18, 18, m_size, m_size, fill=1, stroke=0)
    c.rect(width - 18 - m_size, 18, m_size, m_size, fill=1, stroke=0)

    # ------------------------------------------------------------------
    # BASIC DATA (unchanged)
    # ------------------------------------------------------------------
    school_name = str(payload.get("school_name", "SCHOOL ASSESSMENT TEST"))
    class_name = str(payload.get("class_name", ""))
    section = str(payload.get("section", ""))
    subject = str(payload.get("subject", ""))
    difficulty = str(payload.get("difficulty_level", payload.get("difficulty", "")))
    chapter = str(payload.get("chapter", payload.get("chapter_name", "")))
    skill = str(payload.get("skill_tested", payload.get("skill", "")))
    assignment_id = str(payload.get("assignment_id", "T01"))

    # QR metadata: use the requested exam date when supplied; otherwise keep
    # the previous behavior and use today's date. The visible test detail and
    # QR therefore always refer to the same exam date.
    raw_exam_date = (
        payload.get("exam_date")
        or payload.get("print_date")
        or payload.get("test_date")
        or payload.get("date")
    )
    if raw_exam_date is None or not str(raw_exam_date).strip():
        _today = datetime.now()
        exam_date_qr = _today.strftime("%Y-%m-%d")
        exam_date_display = _today.strftime("%d-%b-%Y")
    elif hasattr(raw_exam_date, "strftime"):
        exam_date_qr = raw_exam_date.strftime("%Y-%m-%d")
        exam_date_display = raw_exam_date.strftime("%d-%b-%Y")
    else:
        exam_date_qr = str(raw_exam_date).strip()
        exam_date_display = exam_date_qr
        try:
            _parsed_exam_date = datetime.strptime(exam_date_qr, "%Y-%m-%d")
            exam_date_display = _parsed_exam_date.strftime("%d-%b-%Y")
        except (TypeError, ValueError):
            pass

    _roll_no = payload.get("roll_no")
    if _roll_no is None:
        _roll_no = payload.get("roll_number")
    if _roll_no is None:
        _roll_no = payload.get("student_roll_no")
    if _roll_no is None:
        _roll_no = payload.get("student_roll_number")
    roll_no_value = "" if _roll_no is None else str(_roll_no).strip()

    # ------------------------------------------------------------------
    # QUESTIONS INPUT (unchanged behavior)
    # ------------------------------------------------------------------
    raw_questions = payload.get("questions", [])
    if isinstance(raw_questions, str):
        try:
            raw_questions = json.loads(raw_questions)
        except Exception:
            raw_questions = []
    if not isinstance(raw_questions, list):
        raw_questions = []

    total_q = (
        len(raw_questions)
        if raw_questions
        else int(payload.get("total_questions", 10) or 10)
    )
    if total_q < 1:
        total_q = 1
    if total_q > 40:
        total_q = 40

    # ------------------------------------------------------------------
    # QR CODE — Assignment ID ONLY.
    # The scanner reads this ID, looks up the matching assignment/answer key
    # on the server, then reads the student's roll-number and marked bubbles
    # from the OMR image. Do not put the correct answers in the QR code.
    # ------------------------------------------------------------------
    if not assignment_id or not assignment_id.strip():
        raise ValueError("assignment_id is required for OMR scanning and result matching.")

    qr_data = assignment_id.strip()

    # ------------------------------------------------------------------
    # COMPACT HEADER (only header geometry/content layout changed)
    # Left: school name, then class/section/subject.
    # Right: student name line, then roll-number boxes.
    # All header text uses the same draw_text_auto() renderer as the body.
    # ------------------------------------------------------------------
    is_40_block = total_q > 20
    strip_y = 142 if is_40_block else 205

    header_school_y = height - 30
    header_meta_y = height - 43
    divider_y = height - 72
    instruction_y = height - 66

    left_x = 42
    right_x = width - 205
    left_max_width = right_x - left_x - 14

    # Keep the school name on one readable line by shrinking only as needed.
    school_font = 12.0 if not is_40_block else 10.5
    min_school_font = 7.0
    while school_font > min_school_font and measure_text(school_name, school_font, True) > left_max_width:
        school_font = max(min_school_font, school_font - 0.25)
    if measure_text(school_name, school_font, True) > left_max_width:
        school_name_lines = wrap_text(school_name, left_max_width, school_font, True)
        school_name = school_name_lines[0]

    draw_text_auto(c, left_x, header_school_y, school_name, school_font, bold=True)

    header_meta = []
    if class_name:
        header_meta.append(f"कक्षा: {class_name}")
    if section:
        header_meta.append(f"सेक्शन: {section}")
    if subject:
        header_meta.append(f"विषय: {subject}")
    meta_text = "  |  ".join(header_meta)
    meta_font = 7.2 if not is_40_block else 6.4
    while meta_font > 5.0 and measure_text(meta_text, meta_font, False) > left_max_width:
        meta_font = max(5.0, meta_font - 0.2)
    draw_text_auto(c, left_x, header_meta_y, meta_text, meta_font, bold=False)

    # Right-side student name, above the roll-number boxes.
    right_width = width - 40 - right_x
    label_font = 7.2 if not is_40_block else 6.4
    draw_text_auto(c, right_x, header_school_y, "नाम:", label_font, bold=True)
    name_line_start = right_x + 25
    c.setLineWidth(0.65 if is_40_block else 0.7)
    c.line(name_line_start, header_school_y - 1, width - 40, header_school_y - 1)

    roll_label_y = height - 47
    draw_text_auto(c, right_x, roll_label_y, "रोल नंबर:", label_font, bold=True)
    box_size = 8.0 if is_40_block else 9.0
    box_gap = 2.0 if is_40_block else 2.5
    box_start_x = right_x + 48
    box_y = roll_label_y + (label_font * 0.35) - (box_size / 2.0)
    max_box_right = width - 40
    for b in range(4):
        bx = box_start_x + b * (box_size + box_gap)
        if bx + box_size <= max_box_right:
            c.rect(bx, box_y, box_size, box_size, fill=0, stroke=1)

    # Keep the existing instruction, rendered with the same Hindi font helper.
    instruction = "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई ओएमआर पट्टी में नीले/काले पेन से गोला भरकर दें।"
    instruction_font = 6.8 if not is_40_block else 5.9
    instruction_max_width = width - 84
    while instruction_font > 5.0 and measure_text(instruction, instruction_font, False) > instruction_max_width:
        instruction_font = max(5.0, instruction_font - 0.2)
    draw_text_auto(c, left_x, instruction_y, instruction, instruction_font, bold=False)

    # ------------------------------------------------------------------
    # HEADER DIVIDER (position preserved)
    # ------------------------------------------------------------------
    c.setLineWidth(0.8)
    c.line(40, divider_y, width - 40, divider_y)

    # ------------------------------------------------------------------
    # QUESTION AREA (same original layouts and auto-fit rules)
    # ------------------------------------------------------------------
    question_top = height - (78 if is_40_block else 84)
    question_bottom = strip_y + 5
    available_height = question_top - question_bottom

    if total_q <= 20:
        rows = (total_q + 1) // 2
        row_height = available_height / rows
        col1_x = 42
        col2_x = width / 2.0 + 7
        col_width = width / 2.0 - 49
        option_gap = 8
        option_width = (col_width - option_gap) / 2.0
        question_font = 8.0 if total_q <= 10 else 6.5
        option_font = 6.7 if total_q <= 10 else 5.2

        for idx in range(total_q):
            if idx < rows:
                x, row = col1_x, idx
            else:
                x, row = col2_x, idx - rows
            top_y = question_top - row * row_height
            q_text, options = get_question(idx)
            q_font, o_font = question_font, option_font

            for _ in range(25):
                q_lines = wrap_text(q_text, col_width - 15, q_font, True)
                option_lines = [
                    wrap_text(options[j], option_width - 14, o_font, True)
                    for j in range(4)
                ]
                q_lh = q_font * 1.10
                o_lh = o_font * 1.08
                q_height = len(q_lines) * q_lh
                first_option_height = max(len(option_lines[0]), len(option_lines[1])) * o_lh
                second_option_height = max(len(option_lines[2]), len(option_lines[3])) * o_lh
                required_height = q_height + first_option_height + second_option_height + 4
                if required_height <= row_height - 3:
                    break
                q_font = max(4.8, q_font - 0.15)
                o_font = max(4.4, o_font - 0.12)

            y = top_y - 2
            draw_text_auto(c, x, y, f"{idx + 1}.", q_font, bold=True)
            for line_index, line in enumerate(q_lines):
                draw_text_auto(c, x + 13, y - line_index * q_lh, line, q_font, bold=True)
            y -= len(q_lines) * q_lh + 1

            a_x = x
            b_x = x + option_width + option_gap
            draw_option("A", options[0], a_x, y, option_width, o_font, o_lh)
            draw_option("B", options[1], b_x, y, option_width, o_font, o_lh)
            first_option_lines = max(len(option_lines[0]), len(option_lines[1]))
            y2 = y - first_option_lines * o_lh - 1
            draw_option("C", options[2], a_x, y2, option_width, o_font, o_lh)
            draw_option("D", options[3], b_x, y2, option_width, o_font, o_lh)

    else:
        rows = 20
        row_height = available_height / rows
        col1_x, col2_x = 40, 302
        col_width = 248
        option_gap = 7
        option_width = (col_width - option_gap) / 2.0
        base_question_font = 6.0
        base_option_font = 5.0
        min_question_font = 4.8
        min_option_font = 4.2

        for idx in range(total_q):
            if idx < 20:
                x, row = col1_x, idx
            else:
                x, row = col2_x, idx - 20
            top_y = question_top - row * row_height
            q_text, options = get_question(idx)
            q_font, o_font = base_question_font, base_option_font

            for _ in range(35):
                q_lines = wrap_text(q_text, col_width - 15, q_font, True)
                option_lines = [
                    wrap_text(options[j], option_width - 14, o_font, True)
                    for j in range(4)
                ]
                q_lh = q_font * 1.05
                o_lh = o_font * 1.05
                q_height = len(q_lines) * q_lh
                ab_height = max(len(option_lines[0]), len(option_lines[1])) * o_lh
                cd_height = max(len(option_lines[2]), len(option_lines[3])) * o_lh
                required_height = q_height + ab_height + cd_height + 4
                if required_height <= row_height - 2:
                    break
                q_font = max(min_question_font, q_font - 0.10)
                o_font = max(min_option_font, o_font - 0.08)

            # Recalculate the final vertical demand after auto-fit. If extremely
            # long text cannot fit at a readable font size, fail explicitly
            # rather than silently overlap text or produce an unreadable OMR.
            q_lh = q_font * 1.0
            o_lh = o_font * 1.0
            final_q_height = len(q_lines) * q_lh
            final_ab_height = max(len(option_lines[0]), len(option_lines[1])) * o_lh
            final_cd_height = max(len(option_lines[2]), len(option_lines[3])) * o_lh
            if final_q_height + final_ab_height + final_cd_height + 2.5 > row_height:
                raise ValueError(
                    f"Question {idx + 1} or its options are too long for the readable "
                    "40-question single-page layout. Shorten this question/options or "
                    "use the 20-question format."
                )

            y = top_y - 1
            draw_text_auto(c, x, y, f"{idx + 1}.", q_font, bold=True)
            for line_index, line in enumerate(q_lines):
                draw_text_auto(c, x + 11, y - line_index * q_lh, line, q_font, bold=True)
            y -= len(q_lines) * q_lh + 1.2

            a_x = x
            b_x = x + option_width + option_gap
            draw_option("A", options[0], a_x, y, option_width, o_font, o_lh)
            draw_option("B", options[1], b_x, y, option_width, o_font, o_lh)
            ab_lines = max(len(option_lines[0]), len(option_lines[1]))
            y2 = y - ab_lines * o_lh - 1
            draw_option("C", options[2], a_x, y2, option_width, o_font, o_lh)
            draw_option("D", options[3], b_x, y2, option_width, o_font, o_lh)

    # ------------------------------------------------------------------
    # BOTTOM OMR SECTION — responsive 10 / 20 / 40 question layout
    # ------------------------------------------------------------------
    c.setLineWidth(1.2)
    c.line(30, strip_y, width - 30, strip_y)

    # Test details retain all metadata from the existing working function.
    details_x = 42
    if is_40_block:
        details_title_size = 6.1
        details_font_size = 5.2
        details_gap = 5.6
        details_title_y = strip_y - 11
        details_y = strip_y - 22
        details_max_width = 74
    else:
        details_title_size = 7.3
        details_font_size = 5.7
        details_gap = 5.8
        details_title_y = 192
        details_y = 182
        details_max_width = 78

    draw_text_auto(
        c, details_x, details_title_y, "TEST DETAILS",
        details_title_size, bold=True
    )

    detail_values = [
        ("Class/Sec", " ".join(v for v in (class_name, section) if v)),
        ("Subject", subject),
        ("Date", exam_date_display),
        ("Difficulty", difficulty or "-"),
        ("Chapter", chapter or "-"),
        ("Skill", skill or "-"),
        ("ID", assignment_id),
    ]

    for label, value in detail_values:
        text_line = f"{label}: {value}"
        line_font = details_font_size
        while line_font > 3.8 and measure_text(text_line, line_font, False) > details_max_width:
            line_font = max(3.8, line_font - 0.2)
        draw_text_auto(c, details_x, details_y, text_line, line_font, bold=False)
        details_y -= details_gap

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    if is_40_block:
        # Same footer position; slightly larger 40-question QR improves scanning.
        c.drawInlineImage(qr_img, 40, 18, 56, 56)
    else:
        c.drawInlineImage(qr_img, 45, 76, 56, 56)

    # Two-digit roll-number bubble grid. In 40-question mode the footer is
    # vertically compressed, but the bubbles remain separated and printable.
    if is_40_block:
        roll_title_x = 124
        roll_title_y = strip_y - 11
        roll_start_y = strip_y - 33
        roll_gap_y = 7.0
        roll_radius = 3.0
        roll_font_size = 4.6
        roll_x_positions = (132, 154)
        roll_title_size = 6.1
    else:
        roll_title_x = 125
        roll_title_y = 192
        roll_start_y = 172
        roll_gap_y = 11.0
        roll_radius = 4.0
        roll_font_size = 5.5
        roll_x_positions = (135, 159)
        roll_title_size = 8.5

    c.setFont("Helvetica-Bold", roll_title_size)
    c.drawString(roll_title_x, roll_title_y, "ROLL NO")
    for col_idx, bx in enumerate(roll_x_positions):
        for num in range(10):
            by = roll_start_y - num * roll_gap_y
            c.circle(bx, by, roll_radius, stroke=1, fill=0)
            c.setFont("Helvetica", roll_font_size)
            c.drawCentredString(bx, by - (1.7 if is_40_block else 2), str(num))

    # Answer bubble columns:
    # 10 questions -> 2 columns x 5 rows.
    # 20 questions -> 4 columns x 5 rows.
    # 40 questions -> 4 columns x 10 rows.
    if is_40_block:
        answer_title_x = 204
        answer_title_y = strip_y - 11
        answer_col_starts = (204, 291, 378, 465)
        answer_columns = 4
        questions_per_answer_column = 10
        answer_start_y = strip_y - 30
        answer_row_pitch = 8.0
        bubble_radius = 3.6
        option_x_start = 17.0
        option_x_gap = 12.0
        answer_q_font = 5.1
        option_font_size = 4.45
        option_label_baseline = 1.55
        answer_title_size = 6.1
    elif total_q <= 10:
        answer_title_x = 210
        answer_title_y = 192
        answer_col_starts = (210, 380)
        answer_columns = 2
        questions_per_answer_column = 5
        answer_start_y = 172
        answer_row_pitch = 21.0
        bubble_radius = 5.5
        option_x_start = 25.0
        option_x_gap = 18.0
        answer_q_font = 7.5
        option_font_size = 5.5
        option_label_baseline = 2.0
        answer_title_size = 8.5
    else:
        answer_title_x = 210
        answer_title_y = 192
        answer_col_starts = (210, 300, 390, 480)
        answer_columns = 4
        questions_per_answer_column = 5
        answer_start_y = 172
        answer_row_pitch = 21.0
        bubble_radius = 4.2
        option_x_start = 20.0
        option_x_gap = 15.0
        answer_q_font = 6.5
        option_font_size = 4.7
        option_label_baseline = 1.5
        answer_title_size = 7.3

    draw_text_auto(
        c, answer_title_x, answer_title_y,
        "ANSWER STRIP (Mark One Option Only)",
        answer_title_size, bold=True
    )

    for q_i in range(total_q):
        col_index = q_i // questions_per_answer_column
        row_index = q_i % questions_per_answer_column
        q_x = answer_col_starts[col_index]
        row_y = answer_start_y - row_index * answer_row_pitch

        c.setFont("Helvetica-Bold", answer_q_font)
        c.drawString(q_x, row_y - (1.4 if is_40_block else 0), f"Q{q_i + 1:02d}")

        for option_idx, option_label in enumerate(("A", "B", "C", "D")):
            bx = q_x + option_x_start + option_idx * option_x_gap
            by = row_y
            c.circle(bx, by, bubble_radius, stroke=1, fill=0)
            c.setFont("Helvetica", option_font_size)
            c.drawCentredString(bx, by - option_label_baseline, option_label)

    # ------------------------------------------------------------------
    # FINISH PDF
    # ------------------------------------------------------------------
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()










# =====================================================================
# 7. SUPABASE STORAGE UPLOAD
# =====================================================================

def upload_to_supabase(
    pdf_bytes: bytes,
    file_name: str
) -> str:

    base_url = (
        SUPABASE_URL
        .strip()
        .rstrip("/")
    )

    bucket = (
        BUCKET_NAME
        .strip()
        .strip("/")
    )

    storage_path = (
        f"generated_omrs/{file_name}"
    )

    # ---------------------------------------------------------------
    # Upload PDF
    # ---------------------------------------------------------------

    upload_url = (
        f"{base_url}"
        f"/storage/v1/object/"
        f"{bucket}/"
        f"{storage_path}"
    )

    headers = {
        "Authorization":
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",

        "apikey":
            SUPABASE_SERVICE_ROLE_KEY,

        "Content-Type":
            "application/pdf",

        "x-upsert":
            "true"
    }

    upload_res = requests.post(
        upload_url,
        data=pdf_bytes,
        headers=headers,
        timeout=60
    )

    if upload_res.status_code not in (
        200,
        201
    ):

        print(
            f"Supabase Upload Failed "
            f"({upload_res.status_code}): "
            f"{upload_res.text}"
        )

        raise ValueError(
            f"Upload failed: "
            f"{upload_res.text}"
        )

    # ---------------------------------------------------------------
    # Signed URL
    # ---------------------------------------------------------------

    sign_url = (
        f"{base_url}"
        f"/storage/v1/object/sign/"
        f"{bucket}/"
        f"{storage_path}"
    )

    sign_headers = {
        "Authorization":
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",

        "apikey":
            SUPABASE_SERVICE_ROLE_KEY,

        "Content-Type":
            "application/json"
    }

    sign_payload = {
        "expiresIn": 600
    }

    sign_res = requests.post(
        sign_url,
        json=sign_payload,
        headers=sign_headers,
        timeout=60
    )

    if sign_res.status_code not in (
        200,
        201
    ):

        print(
            f"Signed URL Failed "
            f"({sign_res.status_code}): "
            f"{sign_res.text}"
        )

        raise ValueError(
            f"Signed URL failed: "
            f"{sign_res.text}"
        )

    sign_data = sign_res.json()

    signed_url_path = (
        sign_data.get(
            "signedURL"
        )
        or sign_data.get(
            "signedUrl"
        )
    )

    if not signed_url_path:

        raise ValueError(
            f"Invalid signed URL: "
            f"{sign_data}"
        )

    if signed_url_path.startswith(
        "http"
    ):

        return signed_url_path

    if signed_url_path.startswith(
        "/storage/v1"
    ):

        return (
            f"{base_url}"
            f"{signed_url_path}"
        )

    return (
        f"{base_url}"
        f"/storage/v1"
        f"{signed_url_path}"
    )

# -----------------------------------------------------------
        # ALIGN OMR READ RESULT
        # -----------------------------------------------------------

        # ------------------ HELPER 1: काले कोनों से पेज सीधा करना ------------------
def align_omr_sheet(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    corners = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        if len(approx) == 4:
            area = cv2.contourArea(c)
            if (img.shape[0] * img.shape[1] * 0.0005) < area < (img.shape[0] * img.shape[1] * 0.02):
                x, y, w, h = cv2.boundingRect(approx)
                if 0.8 <= float(w) / h <= 1.2:
                    M = cv2.moments(c)
                    if M["m00"] != 0:
                        corners.append([int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])])

    if len(corners) == 4:
        pts = np.array(corners, dtype="float32")
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1)
        rect = np.zeros((4, 2), dtype="float32")
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]

        target_w, target_h = 1200, 1600
        dst = np.array([[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]], dtype="float32")
        matrix = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(img, matrix, (target_w, target_h))

    return cv2.resize(img, (1200, 1600))


# ------------------ HELPER 2: निचले OMR स्ट्रिप से उत्तर पढ़ना ------------------
def read_answers_from_sheet(sheet_img, total_q=20):
    gray = cv2.cvtColor(sheet_img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)

    h, w = sheet_img.shape[:2]
    omr_roi = thresh[int(h * 0.78):int(h * 0.95), int(w * 0.40):int(w * 0.85)]
    detected_answers = {}

    cols_count = 4 if total_q > 10 else 2
    q_per_col = 5
    bubble_w = omr_roi.shape[1] / cols_count
    bubble_h = omr_roi.shape[0] / q_per_col

    q_idx = 1
    for c_i in range(cols_count):
        for r_i in range(q_per_col):
            if q_idx > total_q:
                break
            box_x = int(c_i * bubble_w)
            box_y = int(r_i * bubble_h)
            box_w = int(bubble_w)
            box_h = int(bubble_h)

            question_box = omr_roi[box_y:box_y+box_h, box_x:box_x+box_w]
            opt_w = box_w / 4.0
            density = []

            for opt_idx in range(4):
                ox = int(opt_idx * opt_w)
                opt_cell = question_box[:, ox:int(ox + opt_w)]
                density.append(cv2.countNonZero(opt_cell))

            max_val = max(density)
            if max_val > (box_h * opt_w * 0.22):
                best_opt = ["A", "B", "C", "D"][density.index(max_val)]
                detected_answers[str(q_idx)] = best_opt
            else:
                detected_answers[str(q_idx)] = None

            q_idx += 1

    return detected_answers





# =====================================================================
# 8. MAIN API ENDPOINT
# =====================================================================

@app.post(
    "/generate-omr-pdf",
    dependencies=[
        Security(
            verify_api_key
        )
    ]
)
async def generate_omr_pdf(
    request: Request
):

    try:

        data = await request.json()

        # -----------------------------------------------------------
        # Preserve compatibility with existing FlutterFlow.
        #
        # Accept:
        # { ... }
        #
        # OR:
        # {"data": {...}}
        # -----------------------------------------------------------

        if (
            isinstance(data, dict)
            and isinstance(
                data.get("data"),
                dict
            )
        ):

            payload = data["data"]

        else:

            payload = data

        if not isinstance(
            payload,
            dict
        ):

            raise ValueError(
                "Request body must be a JSON object."
            )


        


        
        # -----------------------------------------------------------
        # GENERATE PDF
        # -----------------------------------------------------------
# Action चेक करें: अगर FlutterFlow से कुछ न भेजा जाए तो पुराना PDF जनरेशन ही चलेगा
        action = payload.get("action", "generate_pdf")

        # ==================== MODE 1: पुरानी PDF जनरेशन (100% सुरक्षित) ====================
        if action == "generate_pdf":
            pdf_bytes = generate_hybrid_omr_pdf(payload)

            today_date = datetime.now().strftime("%d-%m-%Y")
            unique_token = str(uuid.uuid4())[:8]
            file_name = f"OMR_Exam_{today_date}_{unique_token}.pdf"

            download_url = upload_to_supabase(pdf_bytes, file_name)

            return {
                "success": True,
                "download_url": download_url,
                "file_name": file_name
            }

        # ==================== MODE 2: OMR मूल्यांकन (1 से 100 पन्ने ऑटो रीड) ====================
        elif action == "evaluate_omr":
            file_path = payload.get("file_path") or payload.get("file_url")
            answer_key = payload.get("answer_key", {})
            total_q = int(payload.get("total_questions", 20))
            assignment_id = payload.get("assignment_id")
            chapter_no = payload.get("chapter_no", 1)

            if not file_path:
                raise HTTPException(status_code=400, detail="file_path is required")

            # 1. Supabase बकेट (answer_sheets) से PDF डाउनलोड करें
            if str(file_path).startswith("http"):
                resp = requests.get(file_path)
                file_bytes = resp.content
            else:
                # यदि केवल फाइल का नाम भेजा गया हो
                clean_name = str(file_path).split("/")[-1]
                file_bytes = supabase.storage.from_("answer_sheets").download(clean_name)

            # 2. PDF लोड करें (1 पेज हो या 100 पेज)
            pdf = pdfium.PdfDocument(file_bytes)
            total_pages = len(pdf)

            all_evaluations = []

            for page_idx in range(total_pages):
                page = pdf[page_idx]
                pil_img = page.render(scale=2.0).to_pil()
                img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

                # इमेज सीधा करना व OMR स्ट्रिप से उत्तर पढ़ना
                aligned_img = align_omr_sheet(img)
                student_answers = read_answers_from_sheet(aligned_img, total_q)

                # स्कोर और अंकों की गणना
                score = 0
                for q in range(1, total_q + 1):
                    s_ans = student_answers.get(str(q))
                    c_ans = answer_key.get(str(q))
                    if c_ans and s_ans == c_ans:
                        score += 1

                # ज़ोन तय करना (Red, Yellow, Green)
                percentage = (score / total_q) * 100 if total_q > 0 else 0
                if percentage >= 75:
                    zone = "green"
                elif percentage >= 40:
                    zone = "yellow"
                else:
                    zone = "red"

                # आपकी Supabase की 'test_evaluations' टेबल के अनुसार रिकॉर्ड तैयार करना
                eval_row = {
                    "assignment_id": assignment_id,
                    "chapter_no": chapter_no,
                    "score": score,
                    "total_marks": total_q,
                    "zone": zone,
                    "raw_answers": student_answers,
                    "roll_no": page_idx + 1  # या बबल्स/QR से रीड किया गया रोल नंबर
                }
                all_evaluations.append(eval_row)

            # 3. सीधे test_evaluations टेबल में बल्क इन्सर्ट करना
            supabase.table("test_evaluations").insert(all_evaluations).execute()

            return {
                "success": True,
                "total_sheets_processed": total_pages,
                "evaluations": all_evaluations
            }

        else:
            raise HTTPException(status_code=400, detail="Invalid action")


    except HTTPException:

        raise

    except Exception as e:

        print(
            f"Error: {str(e)}"
        )

        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )



# ==================== ScanOMR ENDPOINT ====================
@app.post("/scan-omr")  # नोट: अगर FlutterFlow में एंडपॉइंट का नाम अलग है तो वही नाम रखें
async def scan_omr_endpoint(request: Request):
    try:
        data = await request.json()
        
        payload = data.get("data", data) if isinstance(data, dict) else {}
        upload_session_id = payload.get("upload_session_id") or str(uuid.uuid4())
        # FlutterFlow से आने वाला image_url
        image_url = payload.get("image_url")
        if not image_url:
            raise HTTPException(status_code=400, detail="image_url is required")

        # 1. Supabase Storage से फ़ाइल लोड करना
        resp = requests.get(image_url)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch uploaded PDF")
        
        file_bytes = resp.content

        # 2. PDF लोड करना (चाहे 1 पेज हो या 100 पेज)
        pdf = pdfium.PdfDocument(file_bytes)
        total_pages = len(pdf)

        all_evaluations = []

        for page_idx in range(total_pages):
            page = pdf[page_idx]
            pil_img = page.render(scale=2.0).to_pil()
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

            # पेज सीधा करके बबल्स पढ़ना
            aligned_img = align_omr_sheet(img)
            student_answers = read_answers_from_sheet(aligned_img, total_q=20)

            # छात्र द्वारा भरे गए कुल प्रश्नों की संख्या (Score)
            attempted = sum(1 for v in student_answers.values() if v is not None)
            total_marks = 20

            # ज़ोन तय करना
            pct = (attempted / total_marks) * 100
            zone = "green" if pct >= 75 else ("yellow" if pct >= 40 else "red")

            # आपकी test_evaluations टेबल का ढांचा
            eval_row = {
                "student_id": payload.get("student_id"),
                "assignment_id": payload.get("assignment_id") or "c0000000-0000-0000-0000-000000000001",
                "chapter_no": 1,
                "test_type": "regular",
                "score": attempted,
                "total_marks": total_marks,
                "zone": zone,
                "raw_answers": student_answers,
                "roll_no": page_idx + 1,
                "upload_session_id": upload_session_id 
            }

             # 2. डुप्लीकेट चेक करना
        replace_existing = payload.get("replace_existing", False)
        assignment_id = payload.get("assignment_id")

        is_updated = False
        if assignment_id:
            existing = supabase.table("test_evaluations") \
                .select("id") \
                .eq("assignment_id", assignment_id) \
                .eq("roll_no", page_idx + 1) \
                .execute()

            # यदि रिकॉर्ड पहले से मौजूद है और replace_existing False है
            if existing.data and not replace_existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"पेज {page_idx + 1} (Roll No: {page_idx + 1}) पहले से अपलोड है!"
                )
            
            # यदि रिकॉर्ड पहले से मौजूद है और रिप्लेस करना है
            elif existing.data and replace_existing:
                supabase.table("test_evaluations") \
                    .update(eval_row) \
                    .eq("assignment_id", assignment_id) \
                    .eq("roll_no", page_idx + 1) \
                    .execute()
                is_updated = True

        # अगर नया रिकॉर्ड है (अपडेट नहीं हुआ), तभी इंसर्ट लिस्ट में जोड़ें
        if not is_updated:
            all_evaluations.append(eval_row)


    

        # 3. सीधे आपकी Supabase की 'test_evaluations' टेबल में सेव करना
        supabase.table("test_evaluations").insert(all_evaluations).execute()

        # FlutterFlow के लिए Success रिस्पॉन्स (apiResult -> Succeeded = True)
        return {
            "success": True,
            "total_processed": total_pages,
            "upload_session_id": upload_session_id,
            "data": all_evaluations
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"OMR Error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))





# =====================================================================
# 9. HEALTH CHECK
# =====================================================================

@app.get("/")
async def health_check():

    return {
        "status": "ok",
        "service": "School OMR & Question Engine",
        "pdf_engine": "ReportLab",
        "hindi_renderer": (
            "Pillow + RAQM + "
            "Noto Sans Devanagari"
        ),
        "bucket": BUCKET_NAME
    }


# =====================================================================
# 10. LOCAL RUN
# =====================================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )










# ==============================================================================
# PDF Download Endpoint (Upload Session ID Based)
# ==============================================================================
import io
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

@app.get("/download-result-pdf")
async def download_result_pdf(session_id: str, file_name: str = None):
    # 1. Supabase से upload_session_id के अनुसार सभी रिकॉर्ड निकालना
    res = supabase.table("test_evaluations") \
        .select("*") \
        .eq("upload_session_id", session_id) \
        .order("roll_no") \
        .execute()

    evaluations = res.data
    if not evaluations:
        raise HTTPException(
            status_code=404, 
            detail=f"Upload session '{session_id}' के लिए कोई रिज़ल्ट नहीं मिला।"
        )

    # 2. PDF बफ़र और डॉक्यूमेंट सेटअप
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    styles = getSampleStyleSheet()
    elements = []

    # हेडर स्टाइल्स
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Heading1'],
        fontSize=18,
        leading=22,
        alignment=1,
        textColor=colors.HexColor("#1A252C"),
        spaceAfter=6
    )
    sub_style = ParagraphStyle(
        'ReportSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        alignment=1,
        textColor=colors.HexColor("#5A6578"),
        spaceAfter=15
    )

    # हेडर टेक्स्ट
    test_type = evaluations[0].get("test_type", "Regular").capitalize()
    elements.append(Paragraph("<b>CLASS OMR EVALUATION REPORT</b>", title_style))
    elements.append(Paragraph(
        f"Test Type: {test_type} | Session ID: {session_id} | Total Students: {len(evaluations)}", 
        sub_style
    ))
    elements.append(Spacer(1, 10))

    # 3. टेबल डेटा तैयार करना
    table_data = [
        ["Roll No", "Total Qs", "Correct", "Wrong", "Not Attempted", "Score", "Zone"]
    ]

    for row in evaluations:
        roll = str(row.get("roll_no", "-"))
        total_q = row.get("total_marks") or 20
        score = row.get("score") or 0
        raw_ans = row.get("raw_answers") or {}

        # गणना: attempted, wrong, unattempted
        attempted_count = sum(1 for ans in raw_ans.values() if ans is not None)
        unattempted = max(0, total_q - attempted_count)
        wrong = max(0, attempted_count - score)
        zone = str(row.get("zone", "-")).upper()

        table_data.append([
            roll,
            str(total_q),
            str(score),
            str(wrong),
            str(unattempted),
            f"{score}/{total_q}",
            zone
        ])

    # 4. टेबल डिज़ाइन और स्टाइलिंग
    t = Table(table_data, colWidths=[60, 65, 65, 65, 85, 65, 65])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#F8FAFC"), colors.white]),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))

    elements.append(t)
    doc.build(elements)
    buffer.seek(0)

    # 5. फ़ाइल नाम तय करना
    if file_name:
        clean_name = file_name.replace(".pdf", "").replace(".PDF", "")
        download_title = f"Result_{clean_name}.pdf"
    else:
        short_id = session_id[:8] if len(session_id) >= 8 else session_id
        download_title = f"Class_Result_{short_id}.pdf"

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={download_title}"}
    )

