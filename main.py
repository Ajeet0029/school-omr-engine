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
    # TOP SECTION
    # ================================================================

    school_name = payload.get(
        "school_name",
        "SCHOOL ASSESSMENT TEST"
    )

    draw_hindi_text(
        c,
        45,
        height - 40,
        str(school_name),
        12,
        bold=True
    )

    # ---------------------------------------------------------------
    # HINDI INSTRUCTION
    # ---------------------------------------------------------------

    draw_hindi_text(
        c,
        45,
        height - 52,
        "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई ओएमआर पट्टी में नीले/काले पेन से गोला भरकर दें।",
        8,
        bold=False
    )

    # ================================================================
    # STUDENT DETAILS BOX
    # ================================================================

    c.rect(
        width - 220,
        height - 60,
        180,
        32,
        fill=0
    )

    c.setFont(
        FONT_BOLD,
        7.5
    )

    c.drawString(
        width - 215,
        height - 42,
        "Name:"
    )

    c.drawString(
        width - 215,
        height - 54,
        "Roll No:"
    )

    box_start_x = width - 170

    for b in range(4):

        c.rect(
            box_start_x + (
                b * 12
            ),
            height - 56,
            10,
            10,
            fill=0
        )

    # ================================================================
    # DIVIDER
    # ================================================================

    c.setLineWidth(
        0.8
    )

    c.line(
        40,
        height - 68,
        width - 40,
        height - 68
    )

    # ================================================================
    # QUESTIONS
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

    col1_x = 42

    col2_x = (
        width / 2.0
    ) + 10

    col_width = (
        width / 2.0
    ) - 52

    half_q = (
        total_q + 1
    ) // 2

    y_start = (
        height - 85
    )

    line_spacing = (
        42
        if total_q <= 10
        else 24
    )

    # ================================================================
    # QUESTION LOOP
    # ================================================================

    for idx in range(total_q):

        q_data = (
            raw_questions[idx]
            if (
                idx < len(
                    raw_questions
                )
                and isinstance(
                    raw_questions[idx],
                    dict
                )
            )
            else {}
        )

        # ------------------------------------------------------------
        # QUESTION
        # ------------------------------------------------------------

        q_text = (
            q_data.get(
                "question_text"
            )
            or q_data.get(
                "question"
            )
            or f"प्रश्न संख्या {idx + 1}"
        )

        # ------------------------------------------------------------
        # OPTIONS
        # ------------------------------------------------------------

        opt_a = (
            q_data.get(
                "opt_a"
            )
            or q_data.get(
                "option_a"
            )
            or "विकल्प A"
        )

        opt_b = (
            q_data.get(
                "opt_b"
            )
            or q_data.get(
                "option_b"
            )
            or "विकल्प B"
        )

        opt_c = (
            q_data.get(
                "opt_c"
            )
            or q_data.get(
                "option_c"
            )
            or "विकल्प C"
        )

        opt_d = (
            q_data.get(
                "opt_d"
            )
            or q_data.get(
                "option_d"
            )
            or "विकल्प D"
        )

        # ------------------------------------------------------------
        # COLUMN
        # ------------------------------------------------------------

        is_col2 = (
            idx >= half_q
        )

        cur_x = (
            col2_x
            if is_col2
            else col1_x
        )

        row_num = (
            idx - half_q
            if is_col2
            else idx
        )

        cur_y = (
            y_start
            - (
                row_num
                * line_spacing
            )
        )

        # ============================================================
        # QUESTION
        # ============================================================

        display_q = (
            f"{idx + 1}. "
            f"{str(q_text)[:55]}"
        )

        draw_hindi_text(
            c,
            cur_x,
            cur_y,
            display_q,
            7.5,
            bold=True
        )

        # ============================================================
        # OPTION A
        # ============================================================

        draw_hindi_text(
            c,
            cur_x + 8,
            cur_y - 10,
            f"(A) {str(opt_a)[:18]}",
            6.8,
            bold=False
        )

        # ============================================================
        # OPTION B
        # ============================================================

        draw_hindi_text(
            c,
            cur_x + (
                col_width / 2
            ),
            cur_y - 10,
            f"(B) {str(opt_b)[:18]}",
            6.8,
            bold=False
        )

        # ============================================================
        # OPTION C
        # ============================================================

        draw_hindi_text(
            c,
            cur_x + 8,
            cur_y - 20,
            f"(C) {str(opt_c)[:18]}",
            6.8,
            bold=False
        )

        # ============================================================
        # OPTION D
        # ============================================================

        draw_hindi_text(
            c,
            cur_x + (
                col_width / 2
            ),
            cur_y - 20,
            f"(D) {str(opt_d)[:18]}",
            6.8,
            bold=False
        )

    # ================================================================
    # BOTTOM OMR SECTION
    # ================================================================

    strip_y = 155

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

    c.setFont(
        FONT_BOLD,
        7.5
    )

    c.drawString(
        45,
        strip_y - 15,
        "TEST DETAILS"
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

    # Class/section may contain Hindi.
    draw_hindi_text(
        c,
        45,
        strip_y - 27,
        f"Class: {class_name} {section}",
        6.8,
        bold=False
    )

    subject = str(
        payload.get(
            "subject",
            ""
        )
    )

    # Subject may contain Hindi.
    draw_hindi_text(
        c,
        45,
        strip_y - 37,
        f"Subject: {subject}",
        6.8,
        bold=False
    )

    c.setFont(
        FONT_NAME,
        6.8
    )

    c.drawString(
        45,
        strip_y - 47,
        (
            "Date: "
            + datetime.now().strftime(
                "%d-%b-%Y"
            )
        )
    )

    c.drawString(
        45,
        strip_y - 57,
        (
            "ID: "
            + str(
                payload.get(
                    "assignment_id",
                    "T01"
                )
            )
        )
    )

    # ================================================================
    # QR CODE
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
        45,
        strip_y - 105,
        42,
        42
    )

    # ================================================================
    # ROLL NUMBER BUBBLE GRID
    # ================================================================

    roll_x = 135

    c.setFont(
        FONT_BOLD,
        7.5
    )

    c.drawString(
        roll_x,
        strip_y - 15,
        "ROLL NO"
    )

    for col_r in range(2):

        bx = (
            roll_x
            + 5
            + (
                col_r
                * 15
            )
        )

        for num in range(10):

            by = (
                strip_y
                - 30
                - (
                    num
                    * 8.5
                )
            )

            c.circle(
                bx,
                by,
                3.2,
                stroke=1,
                fill=0
            )

            c.setFont(
                FONT_NAME,
                5
            )

            c.drawCentredString(
                bx,
                by - 1.8,
                str(num)
            )

    # ================================================================
    # ANSWER STRIP
    # ================================================================

    ans_x = 210

    c.setFont(
        FONT_BOLD,
        8
    )

    c.drawString(
        ans_x,
        strip_y - 15,
        "ANSWER STRIP (Mark One Option Only)"
    )

    ans_cols = (
        2
        if total_q <= 10
        else 4
    )

    q_per_ans_col = (
        total_q
        + ans_cols
        - 1
    ) // ans_cols

    col_gap = 75

    opt_labels = [
        "A",
        "B",
        "C",
        "D"
    ]

    for q_i in range(
        total_q
    ):

        c_i = (
            q_i
            // q_per_ans_col
        )

        r_i = (
            q_i
            % q_per_ans_col
        )

        q_base_x = (
            ans_x
            + (
                c_i
                * col_gap
            )
        )

        q_base_y = (
            strip_y
            - 30
            - (
                r_i
                * 10
            )
        )

        c.setFont(
            FONT_BOLD,
            6.5
        )

        c.drawString(
            q_base_x,
            q_base_y - 2,
            f"Q{q_i + 1:02d}"
        )

        for o_i, o_label in enumerate(
            opt_labels
        ):

            bx = (
                q_base_x
                + 22
                + (
                    o_i
                    * 12
                )
            )

            by = q_base_y

            c.circle(
                bx,
                by,
                3.4,
                stroke=1,
                fill=0
            )

            c.setFont(
                FONT_NAME,
                4.8
            )

            c.drawCentredString(
                bx,
                by - 1.5,
                o_label
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
