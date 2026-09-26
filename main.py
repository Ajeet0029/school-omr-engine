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

def generate_hybrid_omr_pdf(payload):
    """
    PDF layout only.
    Existing API/auth/upload/QR packet logic is preserved.
    10/20 question layout remains separate.
    21-40 question layout is a dedicated single-page compact block.
    """

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4

    # ============================================================
    # COMMON DATA
    # ============================================================

    def getv(*keys, default=""):
        for k in keys:
            v = payload.get(k)
            if v not in (None, ""):
                return str(v)
        return default

    school = getv(
        "school_name",
        "school",
        default="SCHOOL ASSESSMENT TEST"
    )

    class_name = getv(
        "class_name",
        "class",
        default=""
    )

    section = getv(
        "section",
        default=""
    )

    subject = getv(
        "subject",
        default=""
    )

    difficulty = getv(
        "difficulty_level",
        "difficulty",
        default=""
    )

    chapter = getv(
        "chapter",
        "chapter_name",
        default=""
    )

    skill = getv(
        "skill_tested",
        "skill",
        default=""
    )

    date_s = getv(
        "date",
        default=datetime.now().strftime("%d-%b-%Y")
    )

    assignment_id = getv(
        "assignment_id",
        "test_id",
        default="T01"
    )

    # ============================================================
    # QUESTIONS DATA
    # ============================================================

    raw = payload.get("questions", [])

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []

    if not isinstance(raw, list):
        raw = []

    total_q = len(raw)

    if total_q <= 0:
        try:
            total_q = int(
                payload.get("total_questions", 10) or 10
            )
        except Exception:
            total_q = 10

    total_q = max(1, total_q)

    if total_q > 40:
        total_q = 40

    qs = [
        q if isinstance(q, dict) else {}
        for q in raw[:total_q]
    ]

    while len(qs) < total_q:
        qs.append({})

    # ============================================================
    # COMMON ANCHOR / REGISTRATION MARKS
    # ============================================================

    a = 14
    inset = 18

    c.setFillColor(colors.black)

    for x, y in [
        (inset, height - inset - a),
        (width - inset - a, height - inset - a),
        (inset, inset),
        (width - inset - a, inset)
    ]:
        c.rect(
            x,
            y,
            a,
            a,
            fill=1,
            stroke=0
        )

    # ============================================================
    # BLOCK 1
    # 10 / 20 QUESTIONS
    # ============================================================

    if total_q <= 20:

        strip_y = 154

        # --------------------------------------------------------
        # HEADER
        # --------------------------------------------------------

        if any('\u0900' <= ch <= '\u097F' for ch in school):
            draw_mixed_text(
                c,
                45,
                height - 34,
                school,
                12,
                True
            )
        else:
            c.setFont(
                "Helvetica-Bold",
                12
            )
            c.drawString(
                45,
                height - 34,
                school
            )

        meta_parts = []

        if class_name:
            meta_parts.append(
                f"Class: {class_name}"
            )

        if section:
            meta_parts.append(
                f"Section: {section}"
            )

        if subject:
            meta_parts.append(
                f"Subject: {subject}"
            )

        meta = "  |  ".join(meta_parts)

        draw_mixed_text(
            c,
            45,
            height - 48,
            meta,
            7.2,
            False
        )

        draw_mixed_text(
            c,
            45,
            height - 63,
            "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई OMR पट्टी में नीले/काले पेन से गोला भरकर दें।",
            6.8,
            False
        )

        # --------------------------------------------------------
        # NAME / ROLL BOX
        # --------------------------------------------------------

        rx = width - 220
        ry = height - 62
        rw = 180
        rh = 41

        c.setLineWidth(.7)

        c.rect(
            rx,
            ry,
            rw,
            rh,
            fill=0,
            stroke=1
        )

        c.setFont(
            "Helvetica-Bold",
            7.2
        )

        c.drawString(
            rx + 7,
            ry + 26,
            "NAME:"
        )

        c.line(
            rx + 45,
            ry + 25,
            rx + rw - 7,
            ry + 25
        )

        c.drawString(
            rx + 7,
            ry + 10,
            "ROLL NO:"
        )

        bx = rx + 57
        bs = 11
        gap = 3

        for b in range(4):
            c.rect(
                bx + b * (bs + gap),
                ry + 6,
                bs,
                bs,
                fill=0,
                stroke=1
            )

        c.setLineWidth(.8)

        c.line(
            40,
            height - 72,
            width - 40,
            height - 72
        )

        # --------------------------------------------------------
        # QUESTIONS
        # --------------------------------------------------------

        half = (total_q + 1) // 2
        rows = half

        q_top = height - 88

        q_area = 390

        row_h = q_area / rows

        col1_x = 42
        col2_x = width / 2 + 7

        col_w = width / 2 - 49

        gap = 8

        opt_w = (col_w - gap) / 2

        for idx in range(total_q):

            q = qs[idx]

            def qv(*keys, default=""):
                for k in keys:
                    v = q.get(k)
                    if v not in (None, ""):
                        return str(v)
                return default

            qt = qv(
                "question_text",
                "question",
                default=f"प्रश्न संख्या {idx + 1}"
            )

            opts = [
                qv(
                    "opt_a",
                    "option_a",
                    default="विकल्प A"
                ),
                qv(
                    "opt_b",
                    "option_b",
                    default="विकल्प B"
                ),
                qv(
                    "opt_c",
                    "option_c",
                    default="विकल्प C"
                ),
                qv(
                    "opt_d",
                    "option_d",
                    default="विकल्प D"
                ),
            ]

            col2 = idx >= half

            row = (
                idx - half
                if col2
                else idx
            )

            x = (
                col2_x
                if col2
                else col1_x
            )

            top = q_top - row * row_h

            qsize = (
                8.0
                if total_q <= 10
                else 6.5
            )

            osize = (
                6.7
                if total_q <= 10
                else 5.2
            )

            max_q_lines = None
            max_o_lines = None

            for _ in range(18):

                qlines = wrap_mixed(
                    qt,
                    col_w - 16,
                    qsize,
                    True,
                    max_q_lines
                )

                olines = [
                    wrap_mixed(
                        opts[j],
                        opt_w - 14,
                        osize,
                        False,
                        max_o_lines
                    )
                    for j in range(4)
                ]

                qh = (
                    len(qlines)
                    * qsize
                    * 1.12
                )

                oh = (
                    (
                        max(
                            len(olines[0]),
                            len(olines[1])
                        )
                        +
                        max(
                            len(olines[2]),
                            len(olines[3])
                        )
                    )
                    * osize
                    * 1.1
                    + 6
                )

                if (
                    qh + oh + 4 <= row_h - 3
                    or (
                        qsize <= 4.8
                        and osize <= 4.5
                    )
                ):
                    break

                qsize = max(
                    4.8,
                    qsize - .2
                )

                osize = max(
                    4.5,
                    osize - .15
                )

            y = top - 2

            qlh = qsize * 1.12

            for li, line in enumerate(qlines):

                draw_mixed_text(
                    c,
                    x + 13,
                    y - li * qlh,
                    line,
                    qsize,
                    True
                )

            c.setFont(
                "Helvetica-Bold",
                qsize
            )

            c.drawString(
                x,
                y,
                f"{idx + 1}."
            )

            y -= (
                len(qlines)
                * qlh
                + 1
            )

            row_gap = (
                osize * 1.15
                + 2
            )

            for j in range(4):

                rr = (
                    0
                    if j < 2
                    else 1
                )

                cc = j % 2

                oy = (
                    y
                    - rr * row_gap
                )

                ox = (
                    x
                    + cc
                    * (
                        opt_w
                        + gap
                    )
                )

                c.setFont(
                    "Helvetica",
                    osize
                )

                c.drawString(
                    ox,
                    oy,
                    f"({chr(65 + j)})"
                )

                for li, line in enumerate(
                    olines[j]
                ):

                    draw_mixed_text(
                        c,
                        ox + 14,
                        oy
                        - li
                        * osize
                        * 1.1,
                        line,
                        osize,
                        False
                    )

        # --------------------------------------------------------
        # BOTTOM DIVIDER
        # --------------------------------------------------------

        c.setLineWidth(1)

        c.line(
            40,
            strip_y,
            width - 40,
            strip_y
        )

        # --------------------------------------------------------
        # TEST DETAILS
        # --------------------------------------------------------

        c.setFont(
            "Helvetica-Bold",
            7.3
        )

        c.drawString(
            45,
            strip_y - 12,
            "TEST DETAILS"
        )

        detail_rows = [
            (
                "Class / Sec",
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
                date_s
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
            ),
        ]

        dy = strip_y - 23

        for lab, val in detail_rows:

            c.setFont(
                "Helvetica-Bold",
                5.7
            )

            c.drawString(
                45,
                dy,
                lab + ":"
            )

            draw_mixed_text(
                c,
                76,
                dy,
                val,
                5.7,
                False
            )

            dy -= 7.0

        # --------------------------------------------------------
        # QR
        # --------------------------------------------------------

        packet = make_packet(
            payload,
            qs,
            total_q,
            date_s,
            {
                "school": school,
                "class_name": class_name,
                "section": section,
                "subject": subject,
                "difficulty": difficulty,
                "chapter": chapter,
                "skill": skill,
                "assignment_id": assignment_id
            }
        )

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=2,
            border=2
        )

        qr.add_data(packet)

        qr.make(fit=True)

        qr_img = qr.make_image(
            fill_color="black",
            back_color="white"
        ).convert("RGB")

        c.drawInlineImage(
            qr_img,
            45,
            24,
            48,
            48
        )

        # --------------------------------------------------------
        # ROLL NO BUBBLE GRID
        # --------------------------------------------------------

        roll_x = 140

        c.setFont(
            "Helvetica-Bold",
            7.3
        )

        c.drawString(
            roll_x,
            strip_y - 12,
            "ROLL NO"
        )

        for col in range(2):

            bx = (
                roll_x
                + 6
                + col * 18
            )

            for num in range(10):

                by = (
                    strip_y
                    - 27
                    - num * 8.0
                )

                c.circle(
                    bx,
                    by,
                    3.0,
                    stroke=1,
                    fill=0
                )

                c.setFont(
                    "Helvetica",
                    4.5
                )

                c.drawCentredString(
                    bx,
                    by - 1.5,
                    str(num)
                )

        # --------------------------------------------------------
        # ANSWER STRIP
        # --------------------------------------------------------

        ans_x = 220

        c.setFont(
            "Helvetica-Bold",
            7.3
        )

        c.drawString(
            ans_x,
            strip_y - 12,
            "ANSWER STRIP (Mark One Option Only)"
        )

        ans_cols = (
            2
            if total_q <= 10
            else 4
        )

        qpc = (
            total_q
            + ans_cols
            - 1
        ) // ans_cols

        col_gap = 74

        for qi in range(total_q):

            ci = qi // qpc
            ri = qi % qpc

            qx = (
                ans_x
                + ci * col_gap
            )

            qy = (
                strip_y
                - 27
                - ri * 10.0
            )

            c.setFont(
                "Helvetica-Bold",
                6.0
            )

            c.drawString(
                qx,
                qy - 2,
                f"Q{qi + 1:02d}"
            )

            for oi, label in enumerate(
                "ABCD"
            ):

                bx = (
                    qx
                    + 20
                    + oi * 11.2
                )

                c.circle(
                    bx,
                    qy,
                    3.15,
                    stroke=1,
                    fill=0
                )

                c.setFont(
                    "Helvetica",
                    4.4
                )

                c.drawCentredString(
                    bx,
                    qy - 1.45,
                    label
                )

    # ============================================================
    # BLOCK 2
    # 21 - 40 QUESTIONS
    # DEDICATED SINGLE A4 PAGE
    # ============================================================

    else:

        strip_y = 136

        # --------------------------------------------------------
        # COMPACT HEADER
        # --------------------------------------------------------

        if any('\u0900' <= ch <= '\u097F' for ch in school):

            draw_mixed_text(
                c,
                42,
                height - 30,
                school,
                10.5,
                True
            )

        else:

            c.setFont(
                "Helvetica-Bold",
                10.5
            )

            c.drawString(
                42,
                height - 30,
                school
            )

        meta_parts = []

        if class_name:
            meta_parts.append(
                f"Class: {class_name}"
            )

        if section:
            meta_parts.append(
                f"Section: {section}"
            )

        if subject:
            meta_parts.append(
                f"Subject: {subject}"
            )

        meta = "  |  ".join(meta_parts)

        draw_mixed_text(
            c,
            42,
            height - 42,
            meta,
            6.4,
            False
        )

        draw_mixed_text(
            c,
            42,
            height - 54,
            "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई OMR पट्टी में नीले/काले पेन से गोला भरकर दें।",
            5.9,
            False
        )

        # --------------------------------------------------------
        # NAME / ROLL BOX
        # --------------------------------------------------------

        rx = width - 214
        ry = height - 57
        rw = 172
        rh = 34

        c.setLineWidth(.65)

        c.rect(
            rx,
            ry,
            rw,
            rh,
            fill=0,
            stroke=1
        )

        c.setFont(
            "Helvetica-Bold",
            6.4
        )

        c.drawString(
            rx + 6,
            ry + 22,
            "NAME:"
        )

        c.line(
            rx + 39,
            ry + 21,
            rx + rw - 6,
            ry + 21
        )

        c.drawString(
            rx + 6,
            ry + 9,
            "ROLL NO:"
        )

        bx = rx + 51
        bs = 9.2
        gap = 2.5

        for b in range(4):

            c.rect(
                bx + b * (bs + gap),
                ry + 5,
                bs,
                bs,
                fill=0,
                stroke=1
            )

        # --------------------------------------------------------
        # HEADER DIVIDER
        # --------------------------------------------------------

        c.setLineWidth(.7)

        c.line(
            40,
            height - 66,
            width - 40,
            height - 66
        )

        # --------------------------------------------------------
        # 40 QUESTION AREA
        # --------------------------------------------------------

        q_top = height - 78
        q_bottom = strip_y + 5

        q_area_height = (
            q_top
            - q_bottom
        )

        rows = 20

        row_h = (
            q_area_height
            / rows
        )

        col1_x = 40
        col2_x = 302

        col_w = 248

        option_gap = 7

        option_w = (
            col_w
            - option_gap
        ) / 2

        # --------------------------------------------------------
        # QUESTION LOOP
        # --------------------------------------------------------

        for idx in range(total_q):

            q = qs[idx]

            def qv40(*keys, default=""):
                for k in keys:
                    v = q.get(k)

                    if v not in (
                        None,
                        ""
                    ):
                        return str(v)

                return default

            qt = qv40(
                "question_text",
                "question",
                default=f"प्रश्न संख्या {idx + 1}"
            )

            opts = [
                qv40(
                    "opt_a",
                    "option_a",
                    default="विकल्प A"
                ),
                qv40(
                    "opt_b",
                    "option_b",
                    default="विकल्प B"
                ),
                qv40(
                    "opt_c",
                    "option_c",
                    default="विकल्प C"
                ),
                qv40(
                    "opt_d",
                    "option_d",
                    default="विकल्प D"
                ),
            ]

            if idx < 20:

                x = col1_x
                row = idx

            else:

                x = col2_x
                row = idx - 20

            top = (
                q_top
                - row * row_h
            )

            qsize = 5.35
            osize = 4.45

            qlh = qsize * 1.05
            olh = osize * 1.05

            # ----------------------------------------------------
            # AUTO FIT
            # ----------------------------------------------------

            for _ in range(30):

                qlines = wrap_mixed(
                    qt,
                    col_w - 15,
                    qsize,
                    True,
                    None
                )

                olines = [
                    wrap_mixed(
                        opts[j],
                        option_w - 15,
                        osize,
                        False,
                        None
                    )
                    for j in range(4)
                ]

                qlh = (
                    qsize
                    * 1.05
                )

                olh = (
                    osize
                    * 1.05
                )

                q_height = (
                    len(qlines)
                    * qlh
                )

                ab_height = (
                    max(
                        len(olines[0]),
                        len(olines[1])
                    )
                    * olh
                )

                cd_height = (
                    max(
                        len(olines[2]),
                        len(olines[3])
                    )
                    * olh
                )

                required_height = (
                    q_height
                    + 1.5
                    + ab_height
                    + cd_height
                    + 2
                )

                if (
                    required_height
                    <= row_h - 2
                ):
                    break

                qsize = max(
                    3.75,
                    qsize - 0.12
                )

                osize = max(
                    3.45,
                    osize - 0.10
                )

            # ----------------------------------------------------
            # QUESTION NUMBER + TEXT
            # ----------------------------------------------------

            y = (
                top
                - 1
            )

            c.setFont(
                "Helvetica-Bold",
                qsize
            )

            c.drawString(
                x,
                y,
                f"{idx + 1}."
            )

            for li, line in enumerate(
                qlines
            ):

                draw_mixed_text(
                    c,
                    x + 11,
                    y - li * qlh,
                    line,
                    qsize,
                    True
                )

            y -= (
                len(qlines)
                * qlh
                + 1.2
            )

            # ----------------------------------------------------
            # OPTIONS A / B
            # ----------------------------------------------------

            first_row_y = y

            for j in (0, 1):

                cc = j

                ox = (
                    x
                    + cc
                    * (
                        option_w
                        + option_gap
                    )
                )

                c.setFont(
                    "Helvetica",
                    osize
                )

                c.drawString(
                    ox,
                    first_row_y,
                    f"({chr(65 + j)})"
                )

                for li, line in enumerate(
                    olines[j]
                ):

                    draw_mixed_text(
                        c,
                        ox + 10,
                        first_row_y
                        - li * olh,
                        line,
                        osize,
                        False
                    )

            # ----------------------------------------------------
            # OPTIONS C / D
            # ----------------------------------------------------

            ab_lines = max(
                len(olines[0]),
                len(olines[1])
            )

            second_row_y = (
                first_row_y
                - ab_lines * olh
                - 1.0
            )

            for j in (2, 3):

                cc = j - 2

                ox = (
                    x
                    + cc
                    * (
                        option_w
                        + option_gap
                    )
                )

                c.setFont(
                    "Helvetica",
                    osize
                )

                c.drawString(
                    ox,
                    second_row_y,
                    f"({chr(65 + j)})"
                )

                for li, line in enumerate(
                    olines[j]
                ):

                    draw_mixed_text(
                        c,
                        ox + 10,
                        second_row_y
                        - li * olh,
                        line,
                        osize,
                        False
                    )

        # --------------------------------------------------------
        # BOTTOM DIVIDER
        # --------------------------------------------------------

        c.setLineWidth(.9)

        c.line(
            40,
            strip_y,
            width - 40,
            strip_y
        )

        # --------------------------------------------------------
        # TEST DETAILS
        # --------------------------------------------------------

        c.setFont(
            "Helvetica-Bold",
            6.4
        )

        c.drawString(
            42,
            strip_y - 11,
            "TEST DETAILS"
        )

        detail_rows_40 = [
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
                date_s
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
            ),
        ]

        dy = strip_y - 21

        for lab, val in detail_rows_40:

            c.setFont(
                "Helvetica-Bold",
                4.9
            )

            c.drawString(
                42,
                dy,
                lab + ":"
            )

            draw_mixed_text(
                c,
                78,
                dy,
                val,
                4.9,
                False
            )

            dy -= 6.0

        # --------------------------------------------------------
        # QR
        # --------------------------------------------------------

        packet = make_packet(
            payload,
            qs,
            total_q,
            date_s,
            {
                "school": school,
                "class_name": class_name,
                "section": section,
                "subject": subject,
                "difficulty": difficulty,
                "chapter": chapter,
                "skill": skill,
                "assignment_id": assignment_id
            }
        )

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=2,
            border=2
        )

        qr.add_data(packet)

        qr.make(fit=True)

        qr_img = qr.make_image(
            fill_color="black",
            back_color="white"
        ).convert("RGB")

        c.drawInlineImage(
            qr_img,
            42,
            21,
            48,
            48
        )

        # --------------------------------------------------------
        # ROLL NO BUBBLE GRID
        # --------------------------------------------------------

        roll_x = 105

        c.setFont(
            "Helvetica-Bold",
            6.4
        )

        c.drawString(
            roll_x,
            strip_y - 11,
            "ROLL NO"
        )

        for col in range(2):

            bx = (
                roll_x
                + 7
                + col * 18
            )

            for num in range(10):

                by = (
                    strip_y
                    - 25
                    - num * 8.0
                )

                c.circle(
                    bx,
                    by,
                    3.0,
                    stroke=1,
                    fill=0
                )

                c.setFont(
                    "Helvetica",
                    4.2
                )

                c.drawCentredString(
                    bx,
                    by - 1.4,
                    str(num)
                )

        # --------------------------------------------------------
        # ANSWER STRIP
        # 40 QUESTIONS = 4 COLUMNS x 10 ROWS
        # --------------------------------------------------------

        ans_x = 198

        c.setFont(
            "Helvetica-Bold",
            6.4
        )

        c.drawString(
            ans_x,
            strip_y - 11,
            "ANSWER STRIP (Mark One Option Only)"
        )

        ans_cols = 4

        qpc = (
            total_q
            + ans_cols
            - 1
        ) // ans_cols

        col_gap = 76

        for qi in range(total_q):

            ci = qi // qpc
            ri = qi % qpc

            qx = (
                ans_x
                + ci * col_gap
            )

            qy = (
                strip_y
                - 25
                - ri * 10.0
            )

            c.setFont(
                "Helvetica-Bold",
                5.4
            )

            c.drawString(
                qx,
                qy - 1.8,
                f"Q{qi + 1:02d}"
            )

            for oi, label in enumerate(
                "ABCD"
            ):

                bx = (
                    qx
                    + 19
                    + oi * 10.5
                )

                c.circle(
                    bx,
                    qy,
                    3.0,
                    stroke=1,
                    fill=0
                )

                c.setFont(
                    "Helvetica",
                    4.0
                )

                c.drawCentredString(
                    bx,
                    qy - 1.35,
                    label
                )

    # ============================================================
    # FINALIZE PDF
    # ============================================================

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
