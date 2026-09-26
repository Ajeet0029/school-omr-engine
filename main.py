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

def generate_hybrid_omr_pdf(
    payload: dict
) -> bytes:

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4

    # ================================================================
    # LOCAL LAYOUT HELPERS
    # IMPORTANT:
    # These are inside this function only.
    # No external helper/dependency is added.
    # ================================================================

    def measure_text(
        text,
        font_size,
        bold=False
    ):
        text = str(text or "")

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

        dummy = Image.new(
            "RGBA",
            (10, 10),
            (255, 255, 255, 0)
        )

        d = ImageDraw.Draw(
            dummy
        )

        bbox = d.textbbox(
            (0, 0),
            text,
            font=font,
            anchor="ls",
            direction="ltr",
            language="hi"
        )

        return (
            bbox[2] - bbox[0]
        ) / FONT_RENDER_SCALE

    def wrap_text(
        text,
        max_width,
        font_size,
        bold=False
    ):
        text = str(text or "")

        if not text.strip():
            return [""]

        words = text.split()
        lines = []
        current = ""

        for word in words:

            candidate = (
                word
                if not current
                else
                current + " " + word
            )

            if (
                measure_text(
                    candidate,
                    font_size,
                    bold
                )
                <= max_width
            ):
                current = candidate
                continue

            # --------------------------------------------------------
            # Current line is full.
            # --------------------------------------------------------

            if current:
                lines.append(
                    current
                )

            # --------------------------------------------------------
            # If the new word itself fits, start new line.
            # Otherwise split character-wise.
            # --------------------------------------------------------

            if (
                measure_text(
                    word,
                    font_size,
                    bold
                )
                <= max_width
            ):
                current = word
            else:

                piece = ""

                for ch in word:

                    test_piece = (
                        piece + ch
                    )

                    if (
                        piece
                        and
                        measure_text(
                            test_piece,
                            font_size,
                            bold
                        )
                        > max_width
                    ):
                        lines.append(
                            piece
                        )
                        piece = ch
                    else:
                        piece = test_piece

                current = piece

        if current:
            lines.append(
                current
            )

        return (
            lines
            if lines
            else [""]
        )

    def draw_wrapped(
        text,
        x,
        y,
        max_width,
        font_size,
        bold=False,
        line_height=None
    ):

        lines = wrap_text(
            text,
            max_width,
            font_size,
            bold
        )

        if line_height is None:
            line_height = (
                font_size * 1.08
            )

        for line_index, line in enumerate(
            lines
        ):
            draw_hindi_text(
                c,
                x,
                y - (
                    line_index
                    * line_height
                ),
                line,
                font_size,
                bold=bold
            )

        return len(lines)

    def draw_option(
        label,
        text,
        x,
        y,
        max_width,
        font_size,
        line_height
    ):

        draw_hindi_text(
            c,
            x,
            y,
            f"({label})",
            font_size,
            bold=False
        )

        wrapped = wrap_text(
            text,
            max_width - 13,
            font_size,
            False
        )

        for line_index, line in enumerate(
            wrapped
        ):

            draw_hindi_text(
                c,
                x + 12,
                y - (
                    line_index
                    * line_height
                ),
                line,
                font_size,
                bold=False
            )

        return len(wrapped)

    # ================================================================
    # CORNER SCANNER MARKERS
    # ================================================================

    m_size = 14

    c.setFillColor(
        colors.black
    )

    c.rect(
        18,
        height - 18 - m_size,
        m_size,
        m_size,
        fill=1,
        stroke=0
    )

    c.rect(
        width - 18 - m_size,
        height - 18 - m_size,
        m_size,
        m_size,
        fill=1,
        stroke=0
    )

    c.rect(
        18,
        18,
        m_size,
        m_size,
        fill=1,
        stroke=0
    )

    c.rect(
        width - 18 - m_size,
        18,
        m_size,
        m_size,
        fill=1,
        stroke=0
    )

    # ================================================================
    # BASIC DATA
    # ================================================================

    school_name = str(
        payload.get(
            "school_name",
            "SCHOOL ASSESSMENT TEST"
        )
    )

    class_name = str(
        payload.get(
            "class_name",
            ""
        )
    )

    section = str(
        payload.get(
            "section",
            ""
        )
    )

    subject = str(
        payload.get(
            "subject",
            ""
        )
    )

    difficulty = str(
        payload.get(
            "difficulty_level",
            payload.get(
                "difficulty",
                ""
            )
        )
    )

    chapter = str(
        payload.get(
            "chapter",
            payload.get(
                "chapter_name",
                ""
            )
        )
    )

    skill = str(
        payload.get(
            "skill_tested",
            payload.get(
                "skill",
                ""
            )
        )
    )

    assignment_id = str(
        payload.get(
            "assignment_id",
            "T01"
        )
    )

    # ================================================================
    # QUESTIONS INPUT
    # ================================================================

    raw_questions = payload.get(
        "questions",
        []
    )

    if isinstance(
        raw_questions,
        str
    ):
        try:
            raw_questions = json.loads(
                raw_questions
            )
        except Exception:
            raw_questions = []

    if not isinstance(
        raw_questions,
        list
    ):
        raw_questions = []

    total_q = (
        len(raw_questions)
        if raw_questions
        else int(
            payload.get(
                "total_questions",
                10
            ) or 10
        )
    )

    if total_q < 1:
        total_q = 1

    # Layout is designed for maximum 40 questions.
    if total_q > 40:
        total_q = 40

    # ================================================================
    # HEADER GEOMETRY
    # ================================================================

    is_40_block = (
        total_q > 20
    )

    if is_40_block:

        header_school_y = (
            height - 30
        )

        header_meta_y = (
            height - 42
        )

        instruction_y = (
            height - 54
        )

        divider_y = (
            height - 66
        )

        strip_y = 136

        school_font = 10.5
        meta_font = 6.4
        instruction_font = 5.9

    else:

        header_school_y = (
            height - 34
        )

        header_meta_y = (
            height - 48
        )

        instruction_y = (
            height - 62
        )

        divider_y = (
            height - 72
        )

        strip_y = 154

        school_font = 12
        meta_font = 7.2
        instruction_font = 6.8

    # ================================================================
    # SCHOOL NAME
    # ================================================================

    draw_hindi_text(
        c,
        42,
        header_school_y,
        school_name,
        school_font,
        bold=True
    )

    # ================================================================
    # CLASS / SECTION / SUBJECT
    # ================================================================

    header_meta = []

    if class_name:
        header_meta.append(
            f"Class: {class_name}"
        )

    if section:
        header_meta.append(
            f"Section: {section}"
        )

    if subject:
        header_meta.append(
            f"Subject: {subject}"
        )

    draw_hindi_text(
        c,
        42,
        header_meta_y,
        "  |  ".join(
            header_meta
        ),
        meta_font,
        bold=False
    )

    # ================================================================
    # INSTRUCTION
    # ================================================================

    draw_hindi_text(
        c,
        42,
        instruction_y,
        "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई ओएमआर पट्टी में नीले/काले पेन से गोला भरकर दें।",
        instruction_font,
        bold=False
    )

    # ================================================================
    # STUDENT DETAILS BOX
    # ================================================================

    if is_40_block:

        rx = width - 214
        ry = height - 57
        rw = 172
        rh = 34

        label_font = 6.4

        c.setLineWidth(
            0.65
        )

    else:

        rx = width - 220
        ry = height - 62
        rw = 180
        rh = 41

        label_font = 7.2

        c.setLineWidth(
            0.7
        )

    c.rect(
        rx,
        ry,
        rw,
        rh,
        fill=0,
        stroke=1
    )

    draw_hindi_text(
        c,
        rx + 7,
        ry + (
            22
            if is_40_block
            else 27
        ),
        "NAME:",
        label_font,
        bold=True
    )

    c.line(
        rx + 42,
        ry + (
            21
            if is_40_block
            else 26
        ),
        rx + rw - 7,
        ry + (
            21
            if is_40_block
            else 26
        )
    )

    draw_hindi_text(
        c,
        rx + 7,
        ry + (
            9
            if is_40_block
            else 11
        ),
        "ROLL NO:",
        label_font,
        bold=True
    )

    box_start_x = (
        rx
        + (
            56
            if is_40_block
            else 58
        )
    )

    box_y = (
        ry
        + (
            5
            if is_40_block
            else 6
        )
    )

    box_size = (
        9.2
        if is_40_block
        else 11
    )

    box_gap = (
        2.5
        if is_40_block
        else 3
    )

    for b in range(4):

        c.rect(
            box_start_x
            + (
                b
                * (
                    box_size
                    + box_gap
                )
            ),
            box_y,
            box_size,
            box_size,
            fill=0,
            stroke=1
        )

    # ================================================================
    # HEADER DIVIDER
    # ================================================================

    c.setLineWidth(
        0.8
    )

    c.line(
        40,
        divider_y,
        width - 40,
        divider_y
    )

    # ================================================================
    # QUESTION AREA
    # ================================================================

    question_top = (
        height
        - (
            78
            if is_40_block
            else 84
        )
    )

    question_bottom = (
        strip_y
        + 5
    )

    available_height = (
        question_top
        - question_bottom
    )

    # ================================================================
    # BLOCK A: 10 / 20 QUESTIONS
    # ================================================================

    if total_q <= 20:

        rows = (
            total_q + 1
        ) // 2

        row_height = (
            available_height
            / rows
        )

        col1_x = 42
        col2_x = (
            width / 2.0
            + 7
        )

        col_width = (
            width / 2.0
            - 49
        )

        option_gap = 8

        option_width = (
            col_width
            - option_gap
        ) / 2.0

        # Initial font sizes
        question_font = (
            8.0
            if total_q <= 10
            else 6.5
        )

        option_font = (
            6.7
            if total_q <= 10
            else 5.2
        )

        for idx in range(
            total_q
        ):

            if (
                idx
                < rows
            ):
                x = col1_x
                row = idx
            else:
                x = col2_x
                row = (
                    idx - rows
                )

            top_y = (
                question_top
                - (
                    row
                    * row_height
                )
            )

            q_data = (
                raw_questions[idx]
                if (
                    idx
                    < len(
                        raw_questions
                    )
                    and
                    isinstance(
                        raw_questions[idx],
                        dict
                    )
                )
                else {}
            )

            q_text = str(
                q_data.get(
                    "question_text"
                )
                or q_data.get(
                    "question"
                )
                or (
                    f"प्रश्न संख्या "
                    f"{idx + 1}"
                )
            )

            options = [
                str(
                    q_data.get(
                        "opt_a"
                    )
                    or q_data.get(
                        "option_a"
                    )
                    or "विकल्प A"
                ),
                str(
                    q_data.get(
                        "opt_b"
                    )
                    or q_data.get(
                        "option_b"
                    )
                    or "विकल्प B"
                ),
                str(
                    q_data.get(
                        "opt_c"
                    )
                    or q_data.get(
                        "option_c"
                    )
                    or "विकल्प C"
                ),
                str(
                    q_data.get(
                        "opt_d"
                    )
                    or q_data.get(
                        "option_d"
                    )
                    or "विकल्प D"
                )
            ]

            q_font = question_font
            o_font = option_font

            # --------------------------------------------------------
            # Auto-fit
            # --------------------------------------------------------

            for _ in range(25):

                q_lines = wrap_text(
                    q_text,
                    col_width - 15,
                    q_font,
                    True
                )

                option_lines = [
                    wrap_text(
                        options[j],
                        option_width - 14,
                        o_font,
                        False
                    )
                    for j in range(4)
                ]

                q_lh = (
                    q_font
                    * 1.10
                )

                o_lh = (
                    o_font
                    * 1.08
                )

                q_height = (
                    len(q_lines)
                    * q_lh
                )

                first_option_height = (
                    max(
                        len(
                            option_lines[0]
                        ),
                        len(
                            option_lines[1]
                        )
                    )
                    * o_lh
                )

                second_option_height = (
                    max(
                        len(
                            option_lines[2]
                        ),
                        len(
                            option_lines[3]
                        )
                    )
                    * o_lh
                )

                required_height = (
                    q_height
                    + first_option_height
                    + second_option_height
                    + 4
                )

                if (
                    required_height
                    <= row_height - 3
                ):
                    break

                q_font = max(
                    4.8,
                    q_font - 0.15
                )

                o_font = max(
                    4.4,
                    o_font - 0.12
                )

            # --------------------------------------------------------
            # QUESTION NUMBER
            # --------------------------------------------------------

            y = (
                top_y - 2
            )

            draw_hindi_text(
                c,
                x,
                y,
                f"{idx + 1}.",
                q_font,
                bold=True
            )

            # --------------------------------------------------------
            # QUESTION TEXT
            # --------------------------------------------------------

            for line_index, line in enumerate(
                q_lines
            ):

                draw_hindi_text(
                    c,
                    x + 13,
                    y - (
                        line_index
                        * q_lh
                    ),
                    line,
                    q_font,
                    bold=True
                )

            y -= (
                len(q_lines)
                * q_lh
                + 1
            )

            # --------------------------------------------------------
            # A / B
            # --------------------------------------------------------

            a_x = x

            b_x = (
                x
                + option_width
                + option_gap
            )

            draw_option(
                "A",
                options[0],
                a_x,
                y,
                option_width,
                o_font,
                o_lh
            )

            draw_option(
                "B",
                options[1],
                b_x,
                y,
                option_width,
                o_font,
                o_lh
            )

            first_option_lines = max(
                len(
                    option_lines[0]
                ),
                len(
                    option_lines[1]
                )
            )

            y2 = (
                y
                - (
                    first_option_lines
                    * o_lh
                )
                - 1
            )

            # --------------------------------------------------------
            # C / D
            # --------------------------------------------------------

            draw_option(
                "C",
                options[2],
                a_x,
                y2,
                option_width,
                o_font,
                o_lh
            )

            draw_option(
                "D",
                options[3],
                b_x,
                y2,
                option_width,
                o_font,
                o_lh
            )

    # ================================================================
    # BLOCK B: 21–40 QUESTIONS
    # DEDICATED SINGLE-PAGE A4
    # ================================================================

    else:

        rows = 20

        row_height = (
            available_height
            / rows
        )

        col1_x = 40
        col2_x = 302

        col_width = 248

        option_gap = 7

        option_width = (
            col_width
            - option_gap
        ) / 2.0

        base_question_font = 5.35
        base_option_font = 4.45

        for idx in range(
            total_q
        ):

            if idx < 20:
                x = col1_x
                row = idx
            else:
                x = col2_x
                row = (
                    idx - 20
                )

            top_y = (
                question_top
                - (
                    row
                    * row_height
                )
            )

            q_data = (
                raw_questions[idx]
                if (
                    idx
                    < len(
                        raw_questions
                    )
                    and
                    isinstance(
                        raw_questions[idx],
                        dict
                    )
                )
                else {}
            )

            q_text = str(
                q_data.get(
                    "question_text"
                )
                or q_data.get(
                    "question"
                )
                or (
                    f"प्रश्न संख्या "
                    f"{idx + 1}"
                )
            )

            options = [
                str(
                    q_data.get(
                        "opt_a"
                    )
                    or q_data.get(
                        "option_a"
                    )
                    or "विकल्प A"
                ),
                str(
                    q_data.get(
                        "opt_b"
                    )
                    or q_data.get(
                        "option_b"
                    )
                    or "विकल्प B"
                ),
                str(
                    q_data.get(
                        "opt_c"
                    )
                    or q_data.get(
                        "option_c"
                    )
                    or "विकल्प C"
                ),
                str(
                    q_data.get(
                        "opt_d"
                    )
                    or q_data.get(
                        "option_d"
                    )
                    or "विकल्प D"
                )
            ]

            q_font = base_question_font
            o_font = base_option_font

            # --------------------------------------------------------
            # AUTO-FIT FOR 40-Q PAGE
            # --------------------------------------------------------

            for _ in range(35):

                q_lines = wrap_text(
                    q_text,
                    col_width - 15,
                    q_font,
                    True
                )

                option_lines = [
                    wrap_text(
                        options[j],
                        option_width - 14,
                        o_font,
                        False
                    )
                    for j in range(4)
                ]

                q_lh = (
                    q_font
                    * 1.05
                )

                o_lh = (
                    o_font
                    * 1.05
                )

                q_height = (
                    len(q_lines)
                    * q_lh
                )

                ab_height = (
                    max(
                        len(
                            option_lines[0]
                        ),
                        len(
                            option_lines[1]
                        )
                    )
                    * o_lh
                )

                cd_height = (
                    max(
                        len(
                            option_lines[2]
                        ),
                        len(
                            option_lines[3]
                        )
                    )
                    * o_lh
                )

                required_height = (
                    q_height
                    + ab_height
                    + cd_height
                    + 4
                )

                if (
                    required_height
                    <= row_height - 2
                ):
                    break

                q_font = max(
                    3.75,
                    q_font - 0.12
                )

                o_font = max(
                    3.45,
                    o_font - 0.10
                )

            # --------------------------------------------------------
            # QUESTION NUMBER
            # --------------------------------------------------------

            y = (
                top_y - 1
            )

            draw_hindi_text(
                c,
                x,
                y,
                f"{idx + 1}.",
                q_font,
                bold=True
            )

            # --------------------------------------------------------
            # QUESTION TEXT
            # --------------------------------------------------------

            for line_index, line in enumerate(
                q_lines
            ):

                draw_hindi_text(
                    c,
                    x + 11,
                    y - (
                        line_index
                        * q_lh
                    ),
                    line,
                    q_font,
                    bold=True
                )

            y -= (
                len(q_lines)
                * q_lh
                + 1.2
            )

            # --------------------------------------------------------
            # A / B
            # --------------------------------------------------------

            a_x = x

            b_x = (
                x
                + option_width
                + option_gap
            )

            draw_option(
                "A",
                options[0],
                a_x,
                y,
                option_width,
                o_font,
                o_lh
            )

            draw_option(
                "B",
                options[1],
                b_x,
                y,
                option_width,
                o_font,
                o_lh
            )

            ab_lines = max(
                len(
                    option_lines[0]
                ),
                len(
                    option_lines[1]
                )
            )

            y2 = (
                y
                - (
                    ab_lines
                    * o_lh
                )
                - 1
            )

            # --------------------------------------------------------
            # C / D
            # --------------------------------------------------------

            draw_option(
                "C",
                options[2],
                a_x,
                y2,
                option_width,
                o_font,
                o_lh
            )

            draw_option(
                "D",
                options[3],
                b_x,
                y2,
                option_width,
                o_font,
                o_lh
            )

    # ================================================================
    # BOTTOM OMR SECTION
    # ================================================================

    c.setLineWidth(
        1
    )

    c.line(
        40,
        strip_y,
        width - 40,
        strip_y
    )

    # ================================================================
    # TEST DETAILS
    # ================================================================

    details_x = 42

    if is_40_block:

        details_title_size = 6.4
        details_font_size = 4.9
        details_gap = 6.0

    else:

        details_title_size = 7.3
        details_font_size = 5.7
        details_gap = 7.0

    draw_hindi_text(
        c,
        details_x,
        strip_y - 11,
        "TEST DETAILS",
        details_title_size,
        bold=True
    )

    detail_values = [
        (
            "Class/Sec",
            " ".join(
                [
                    v
                    for v in (
                        class_name,
                        section
                    )
                    if v
                ]
            )
        ),
        (
            "Subject",
            subject
        ),
        (
            "Date",
            datetime.now().strftime(
                "%d-%b-%Y"
            )
        ),
        (
            "Difficulty",
            difficulty or "-"
        ),
        (
            "Chapter",
            chapter or "-"
        ),
        (
            "Skill",
            skill or "-"
        ),
        (
            "ID",
            assignment_id
        )
    ]

    details_y = (
        strip_y - 21
    )

    for label, value in detail_values:

        text_line = (
            f"{label}: "
            f"{value}"
        )

        draw_hindi_text(
            c,
            details_x,
            details_y,
            text_line,
            details_font_size,
            bold=False
        )

        details_y -= details_gap

    # ================================================================
    # QR CODE
    # KEEP EXISTING QR DATA UNCHANGED
    # ================================================================

    qr = qrcode.QRCode(
        box_size=2,
        border=0
    )

    qr_data = (
        f"ID:{payload.get('assignment_id')}"
        f"|CLS:{payload.get('class_name')}"
    )

    qr.add_data(
        qr_data
    )

    qr.make(
        fit=True
    )

    qr_img = (
        qr.make_image(
            fill_color="black",
            back_color="white"
        )
        .convert("RGB")
    )

    c.drawInlineImage(
        qr_img,
        42,
        21 if is_40_block else 24,
        48,
        48
    )

    # ================================================================
    # ROLL NUMBER BUBBLE GRID
    # ================================================================

    if is_40_block:

        roll_x = 105
        roll_title_y = (
            strip_y - 11
        )
        roll_start_y = (
            strip_y - 25
        )
        roll_gap_y = 8.0
        roll_radius = 3.0
        roll_font = 4.2

    else:

        roll_x = 140
        roll_title_y = (
            strip_y - 12
        )
        roll_start_y = (
            strip_y - 27
        )
        roll_gap_y = 8.0
        roll_radius = 3.0
        roll_font = 4.5

    draw_hindi_text(
        c,
        roll_x,
        roll_title_y,
        "ROLL NO",
        7.3 if not is_40_block else 6.4,
        bold=True
    )

    for col_r in range(2):

        bx = (
            roll_x
            + 7
            + (
                col_r * 18
            )
        )

        for num in range(10):

            by = (
                roll_start_y
                - (
                    num
                    * roll_gap_y
                )
            )

            c.circle(
                bx,
                by,
                roll_radius,
                stroke=1,
                fill=0
            )

            draw_hindi_text(
                c,
                bx - 1.5,
                by - 1.4,
                str(num),
                roll_font,
                bold=False
            )

    # ================================================================
    # ANSWER STRIP
    # ================================================================

    ans_x = (
        198
        if is_40_block
        else 220
    )

    answer_title_size = (
        6.4
        if is_40_block
        else 7.3
    )

    draw_hindi_text(
        c,
        ans_x,
        strip_y - 11,
        "ANSWER STRIP (Mark One Option Only)",
        answer_title_size,
        bold=True
    )

    if total_q <= 10:

        answer_columns = 2

    elif total_q <= 20:

        answer_columns = 4

    else:

        answer_columns = 4

    q_per_answer_column = (
        total_q
        + answer_columns
        - 1
    ) // answer_columns

    answer_col_gap = (
        76
        if is_40_block
        else 74
    )

    for q_i in range(
        total_q
    ):

        col_index = (
            q_i
            // q_per_answer_column
        )

        row_index = (
            q_i
            % q_per_answer_column
        )

        q_x = (
            ans_x
            + (
                col_index
                * answer_col_gap
            )
        )

        q_y = (
            strip_y
            - (
                25
                if is_40_block
                else 27
            )
            - (
                row_index
                * 10
            )
        )

        draw_hindi_text(
            c,
            q_x,
            q_y - 2,
            f"Q{q_i + 1:02d}",
            5.4 if is_40_block else 6.0,
            bold=True
        )

        for o_i, o_label in enumerate(
            ["A", "B", "C", "D"]
        ):

            if is_40_block:

                bx = (
                    q_x
                    + 19
                    + (
                        o_i
                        * 10.5
                    )
                )

                bubble_radius = 3.0
                label_size = 4.0
                label_offset = 1.35

            else:

                bx = (
                    q_x
                    + 20
                    + (
                        o_i
                        * 11.2
                    )
                )

                bubble_radius = 3.15
                label_size = 4.4
                label_offset = 1.45

            c.circle(
                bx,
                q_y,
                bubble_radius,
                stroke=1,
                fill=0
            )

            draw_hindi_text(
                c,
                bx - 1.5,
                q_y - label_offset,
                o_label,
                label_size,
                bold=False
            )

    # ================================================================
    # FINISH PDF
    # ================================================================

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

        pdf_bytes = generate_hybrid_omr_pdf(
            payload
        )

        # -----------------------------------------------------------
        # FILE NAME
        # -----------------------------------------------------------

        today_date = (
            datetime.now().strftime(
                "%d-%m-%Y"
            )
        )

        unique_token = (
            str(
                uuid.uuid4()
            )[:8]
        )

        file_name = (
            f"OMR_Exam_"
            f"{today_date}_"
            f"{unique_token}.pdf"
        )

        # -----------------------------------------------------------
        # SUPABASE
        # -----------------------------------------------------------

        download_url = (
            upload_to_supabase(
                pdf_bytes,
                file_name
            )
        )

        # IMPORTANT:
        # Keep the original response key used by FlutterFlow.
        return {
            "success": True,
            "download_url": download_url,
            "file_name": file_name
        }

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
