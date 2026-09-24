import io
import os
import json
import uuid
import urllib.request
import traceback
from datetime import datetime

import requests
import qrcode

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Security,
    status,
)

from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

from supabase import create_client, Client

# ================================================================
# REPORTLAB
# ================================================================

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ================================================================
# FASTAPI APP
# ================================================================

app = FastAPI(
    title="School OMR & Question Engine"
)


# ================================================================
# 1. DEVANAGARI FONT SETUP
#
# IMPORTANT:
# We DO NOT manually move "ि".
# We DO NOT use regex to reorder Hindi.
#
# HarfBuzz does:
# - Matra positioning
# - Conjunct formation
# - Glyph substitution
# - Glyph positioning
# ================================================================

FONT_NAME = "NotoDevanagari"
FONT_BOLD = "NotoDevanagari-Bold"


def setup_fonts():

    font_dir = "/tmp/fonts"

    os.makedirs(
        font_dir,
        exist_ok=True
    )

    regular_font = os.path.join(
        font_dir,
        "NotoSansDevanagari-Regular.ttf"
    )

    bold_font = os.path.join(
        font_dir,
        "NotoSansDevanagari-Bold.ttf"
    )

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    # ------------------------------------------------------------
    # DOWNLOAD REGULAR FONT
    # ------------------------------------------------------------

    if (
        not os.path.exists(regular_font)
        or os.path.getsize(regular_font) < 10000
    ):

        regular_url = (
            "https://raw.githubusercontent.com/"
            "googlefonts/noto-fonts/main/hinted/ttf/"
            "NotoSansDevanagari/"
            "NotoSansDevanagari-Regular.ttf"
        )

        request = urllib.request.Request(
            regular_url,
            headers=headers
        )

        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            data = response.read()

        if len(data) < 10000:

            raise RuntimeError(
                "Noto Sans Devanagari Regular font "
                "download failed."
            )

        with open(
            regular_font,
            "wb"
        ) as file:

            file.write(data)

    # ------------------------------------------------------------
    # DOWNLOAD BOLD FONT
    # ------------------------------------------------------------

    if (
        not os.path.exists(bold_font)
        or os.path.getsize(bold_font) < 10000
    ):

        bold_url = (
            "https://raw.githubusercontent.com/"
            "googlefonts/noto-fonts/main/hinted/ttf/"
            "NotoSansDevanagari/"
            "NotoSansDevanagari-Bold.ttf"
        )

        request = urllib.request.Request(
            bold_url,
            headers=headers
        )

        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            data = response.read()

        if len(data) < 10000:

            raise RuntimeError(
                "Noto Sans Devanagari Bold font "
                "download failed."
            )

        with open(
            bold_font,
            "wb"
        ) as file:

            file.write(data)

    # ------------------------------------------------------------
    # REGISTER WITH HARFBUZZ SHAPING
    #
    # shapable=True is critical.
    # ------------------------------------------------------------

    pdfmetrics.registerFont(
        TTFont(
            FONT_NAME,
            regular_font,
            shapable=True
        )
    )

    pdfmetrics.registerFont(
        TTFont(
            FONT_BOLD,
            bold_font,
            shapable=True
        )
    )

    print(
        "Noto Sans Devanagari loaded "
        "with HarfBuzz shaping."
    )


# ================================================================
# LOAD FONTS
# ================================================================

try:

    setup_fonts()

    print(
        "Devanagari font setup successful."
    )

except Exception as error:

    print(
        f"Font Setup Error: {error}"
    )

    raise


# ================================================================
# 2. HINDI SAFE DRAW FUNCTION
#
# THIS IS THE MOST IMPORTANT PART.
#
# shaping=True tells ReportLab to use its shaping support.
#
# DO NOT reorder Unicode manually.
# ================================================================

def draw_text(
    pdf,
    x,
    y,
    text,
    font_name=FONT_NAME,
    font_size=8
):

    if text is None:
        return

    text = str(text)

    pdf.setFont(
        font_name,
        font_size
    )

    pdf.drawString(
        x,
        y,
        text,
        shaping=True
    )


# ================================================================
# 3. SUPABASE CONFIGURATION
# ================================================================

SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)

SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY"
)

API_SECRET_KEY = os.getenv(
    "APP_API_SECRET_KEY"
)

BUCKET_NAME = os.getenv(
    "SUPABASE_BUCKET_NAME",
    "omr-sheets"
)


# ================================================================
# REQUIRED ENVIRONMENT VARIABLES
# ================================================================

if not SUPABASE_URL:

    raise RuntimeError(
        "SUPABASE_URL is not configured."
    )


if not SUPABASE_SERVICE_ROLE_KEY:

    raise RuntimeError(
        "SUPABASE_SERVICE_ROLE_KEY is not configured."
    )


if not API_SECRET_KEY:

    raise RuntimeError(
        "APP_API_SECRET_KEY is not configured."
    )


# ================================================================
# SUPABASE CLIENT
# ================================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


# ================================================================
# CORS
# ================================================================

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


# ================================================================
# 4. API KEY SECURITY
# ================================================================

API_KEY_NAME = "x-api-key"

api_key_header = APIKeyHeader(
    name=API_KEY_NAME,
    auto_error=False
)


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
                "Unauthorized: "
                "valid x-api-key required."
            )
        )

    return api_key


# ================================================================
# 5. PDF GENERATION
# ================================================================

def generate_hybrid_omr_pdf(
    payload: dict
) -> bytes:

    buffer = io.BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4


    # ============================================================
    # CORNER SCANNER MARKERS
    # ============================================================

    marker_size = 14

    pdf.setFillColor(
        colors.black
    )

    # TOP LEFT
    pdf.rect(
        18,
        height - 18 - marker_size,
        marker_size,
        marker_size,
        fill=1,
        stroke=0
    )

    # TOP RIGHT
    pdf.rect(
        width - 18 - marker_size,
        height - 18 - marker_size,
        marker_size,
        marker_size,
        fill=1,
        stroke=0
    )

    # BOTTOM LEFT
    pdf.rect(
        18,
        18,
        marker_size,
        marker_size,
        fill=1,
        stroke=0
    )

    # BOTTOM RIGHT
    pdf.rect(
        width - 18 - marker_size,
        18,
        marker_size,
        marker_size,
        fill=1,
        stroke=0
    )


    # ============================================================
    # HEADER
    # ============================================================

    school_name = payload.get(
        "school_name",
        "SCHOOL ASSESSMENT TEST"
    )

    # ------------------------------------------------------------
    # SCHOOL NAME
    # ------------------------------------------------------------

    draw_text(
        pdf,
        45,
        height - 40,
        school_name,
        FONT_BOLD,
        12
    )


    # ------------------------------------------------------------
    # HINDI INSTRUCTION
    # ------------------------------------------------------------

    instruction = (
        "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई "
        "ओएमआर पट्टी में नीले/काले पेन से गोला भरकर दें।"
    )

    draw_text(
        pdf,
        45,
        height - 52,
        instruction,
        FONT_NAME,
        8
    )


    # ============================================================
    # STUDENT DETAILS BOX
    # ============================================================

    pdf.rect(
        width - 220,
        height - 60,
        180,
        32,
        fill=0,
        stroke=1
    )


    draw_text(
        pdf,
        width - 215,
        height - 42,
        "Name:",
        FONT_BOLD,
        7.5
    )


    draw_text(
        pdf,
        width - 215,
        height - 54,
        "Roll No:",
        FONT_BOLD,
        7.5
    )


    # ============================================================
    # ROLL NUMBER BOXES
    # ============================================================

    box_start_x = width - 170

    for b in range(4):

        pdf.rect(
            box_start_x + (b * 12),
            height - 56,
            10,
            10,
            fill=0,
            stroke=1
        )


    # ============================================================
    # DIVIDER
    # ============================================================

    pdf.setLineWidth(
        0.8
    )

    pdf.line(
        40,
        height - 68,
        width - 40,
        height - 68
    )


    # ============================================================
    # QUESTIONS
    # ============================================================

    raw_questions = payload.get(
        "questions",
        []
    )


    # ------------------------------------------------------------
    # JSON STRING -> LIST
    # ------------------------------------------------------------

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


    # ------------------------------------------------------------
    # TOTAL QUESTIONS
    # ------------------------------------------------------------

    if raw_questions:

        total_questions = len(
            raw_questions
        )

    else:

        total_questions = int(
            payload.get(
                "total_questions",
                10
            ) or 10
        )


    # ============================================================
    # TWO COLUMN LAYOUT
    # ============================================================

    col1_x = 42

    col2_x = (
        width / 2.0
    ) + 10

    col_width = (
        width / 2.0
    ) - 52

    half_questions = (
        total_questions + 1
    ) // 2

    y_start = (
        height - 85
    )


    if total_questions <= 10:

        line_spacing = 42

    else:

        line_spacing = 24


    # ============================================================
    # QUESTION LOOP
    # ============================================================

    for index in range(
        total_questions
    ):

        # --------------------------------------------------------
        # QUESTION DATA
        # --------------------------------------------------------

        if (
            index < len(raw_questions)
            and isinstance(
                raw_questions[index],
                dict
            )
        ):

            question_data = (
                raw_questions[index]
            )

        else:

            question_data = {}


        # --------------------------------------------------------
        # QUESTION
        # --------------------------------------------------------

        question_text = (
            question_data.get(
                "question_text"
            )
            or question_data.get(
                "question"
            )
            or f"प्रश्न संख्या {index + 1}"
        )


        # --------------------------------------------------------
        # OPTIONS
        # --------------------------------------------------------

        option_a = (
            question_data.get(
                "opt_a"
            )
            or question_data.get(
                "option_a"
            )
            or "विकल्प A"
        )

        option_b = (
            question_data.get(
                "opt_b"
            )
            or question_data.get(
                "option_b"
            )
            or "विकल्प B"
        )

        option_c = (
            question_data.get(
                "opt_c"
            )
            or question_data.get(
                "option_c"
            )
            or "विकल्प C"
        )

        option_d = (
            question_data.get(
                "opt_d"
            )
            or question_data.get(
                "option_d"
            )
            or "विकल्प D"
        )


        # ========================================================
        # COLUMN POSITION
        # ========================================================

        if index >= half_questions:

            current_x = col2_x

            row_number = (
                index - half_questions
            )

        else:

            current_x = col1_x

            row_number = index


        current_y = (
            y_start
            - (
                row_number
                * line_spacing
            )
        )


        # ========================================================
        # QUESTION TEXT
        #
        # IMPORTANT:
        # NO Unicode reordering.
        # ========================================================

        display_question = (
            f"{index + 1}. "
            f"{str(question_text)[:55]}"
        )

        draw_text(
            pdf,
            current_x,
            current_y,
            display_question,
            FONT_BOLD,
            7.5
        )


        # ========================================================
        # OPTION A
        # ========================================================

        draw_text(
            pdf,
            current_x + 8,
            current_y - 10,
            f"(A) {str(option_a)[:18]}",
            FONT_NAME,
            6.8
        )


        # ========================================================
        # OPTION B
        # ========================================================

        draw_text(
            pdf,
            current_x + (
                col_width / 2
            ),
            current_y - 10,
            f"(B) {str(option_b)[:18]}",
            FONT_NAME,
            6.8
        )


        # ========================================================
        # OPTION C
        # ========================================================

        draw_text(
            pdf,
            current_x + 8,
            current_y - 20,
            f"(C) {str(option_c)[:18]}",
            FONT_NAME,
            6.8
        )


        # ========================================================
        # OPTION D
        # ========================================================

        draw_text(
            pdf,
            current_x + (
                col_width / 2
            ),
            current_y - 20,
            f"(D) {str(option_d)[:18]}",
            FONT_NAME,
            6.8
        )


    # ============================================================
    # BOTTOM OMR STRIP
    # ============================================================

    strip_y = 155

    pdf.setLineWidth(
        1
    )

    pdf.line(
        40,
        strip_y,
        width - 40,
        strip_y
    )


    # ============================================================
    # TEST DETAILS
    # ============================================================

    draw_text(
        pdf,
        45,
        strip_y - 15,
        "TEST DETAILS",
        FONT_BOLD,
        7.5
    )


    class_name = payload.get(
        "class_name",
        ""
    )

    section = payload.get(
        "section",
        ""
    )


    draw_text(
        pdf,
        45,
        strip_y - 27,
        f"Class: {class_name} {section}",
        FONT_NAME,
        6.8
    )


    subject = payload.get(
        "subject",
        ""
    )


    draw_text(
        pdf,
        45,
        strip_y - 37,
        f"Subject: {subject}",
        FONT_NAME,
        6.8
    )


    draw_text(
        pdf,
        45,
        strip_y - 47,
        (
            "Date: "
            + datetime.now().strftime(
                "%d-%b-%Y"
            )
        ),
        FONT_NAME,
        6.8
    )


    assignment_id = payload.get(
        "assignment_id",
        "T01"
    )


    draw_text(
        pdf,
        45,
        strip_y - 57,
        f"ID: {assignment_id}",
        FONT_NAME,
        6.8
    )


    # ============================================================
    # QR CODE
    # ============================================================

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

    qr_image = (
        qr.make_image(
            fill_color="black",
            back_color="white"
        ).convert("RGB")
    )


    pdf.drawInlineImage(
        qr_image,
        45,
        strip_y - 105,
        42,
        42
    )


    # ============================================================
    # ROLL NUMBER
    # ============================================================

    roll_x = 135


    draw_text(
        pdf,
        roll_x,
        strip_y - 15,
        "ROLL NO",
        FONT_BOLD,
        7.5
    )


    # ============================================================
    # ROLL NUMBER BUBBLES
    # ============================================================

    for roll_column in range(2):

        bubble_x = (
            roll_x
            + 5
            + (
                roll_column
                * 15
            )
        )

        for number in range(10):

            bubble_y = (
                strip_y
                - 30
                - (
                    number
                    * 8.5
                )
            )


            pdf.circle(
                bubble_x,
                bubble_y,
                3.2,
                stroke=1,
                fill=0
            )


            draw_text(
                pdf,
                bubble_x - 1.5,
                bubble_y - 1.8,
                str(number),
                FONT_NAME,
                5
            )


    # ============================================================
    # ANSWER STRIP
    # ============================================================

    answer_x = 210


    draw_text(
        pdf,
        answer_x,
        strip_y - 15,
        "ANSWER STRIP (Mark One Option Only)",
        FONT_BOLD,
        8
    )


    # ============================================================
    # ANSWER COLUMNS
    # ============================================================

    if total_questions <= 10:

        answer_columns = 2

    else:

        answer_columns = 4


    questions_per_column = (
        total_questions
        + answer_columns
        - 1
    ) // answer_columns


    column_gap = 75


    option_labels = [
        "A",
        "B",
        "C",
        "D"
    ]


    # ============================================================
    # ANSWER BUBBLES
    # ============================================================

    for question_index in range(
        total_questions
    ):

        answer_column = (
            question_index
            // questions_per_column
        )

        answer_row = (
            question_index
            % questions_per_column
        )


        question_x = (
            answer_x
            + (
                answer_column
                * column_gap
            )
        )


        question_y = (
            strip_y
            - 30
            - (
                answer_row
                * 10
            )
        )


        # QUESTION NUMBER

        draw_text(
            pdf,
            question_x,
            question_y - 2,
            f"Q{question_index + 1:02d}",
            FONT_BOLD,
            6.5
        )


        # A/B/C/D BUBBLES

        for option_index, label in enumerate(
            option_labels
        ):

            bubble_x = (
                question_x
                + 22
                + (
                    option_index
                    * 12
                )
            )

            bubble_y = question_y


            pdf.circle(
                bubble_x,
                bubble_y,
                3.4,
                stroke=1,
                fill=0
            )


            draw_text(
                pdf,
                bubble_x - 1.2,
                bubble_y - 1.5,
                label,
                FONT_NAME,
                4.8
            )


    # ============================================================
    # SAVE PDF
    # ============================================================

    pdf.showPage()

    pdf.save()

    buffer.seek(0)

    return buffer.getvalue()


# ================================================================
# 6. SUPABASE STORAGE UPLOAD
# ================================================================

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


    # ============================================================
    # UPLOAD
    # ============================================================

    upload_url = (
        f"{base_url}"
        f"/storage/v1/object/"
        f"{bucket}/"
        f"{storage_path}"
    )


    upload_headers = {

        "Authorization":
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",

        "apikey":
            SUPABASE_SERVICE_ROLE_KEY,

        "Content-Type":
            "application/pdf",

        "x-upsert":
            "true",
    }


    upload_response = requests.post(

        upload_url,

        data=pdf_bytes,

        headers=upload_headers,

        timeout=60
    )


    if upload_response.status_code not in (
        200,
        201
    ):

        print(
            "Supabase Upload Failed:",
            upload_response.status_code
        )

        print(
            upload_response.text
        )

        raise ValueError(
            "Supabase upload failed: "
            + upload_response.text
        )


    # ============================================================
    # SIGNED URL
    # ============================================================

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
            "application/json",
    }


    sign_payload = {
        "expiresIn": 600
    }


    sign_response = requests.post(

        sign_url,

        json=sign_payload,

        headers=sign_headers,

        timeout=60
    )


    if sign_response.status_code not in (
        200,
        201
    ):

        print(
            "Signed URL Failed:",
            sign_response.status_code
        )

        print(
            sign_response.text
        )

        raise ValueError(
            "Signed URL failed: "
            + sign_response.text
        )


    signed_data = (
        sign_response.json()
    )


    signed_url = (
        signed_data.get(
            "signedURL"
        )
        or signed_data.get(
            "signedUrl"
        )
    )


    if not signed_url:

        raise ValueError(
            "Invalid signed URL response: "
            + str(signed_data)
        )


    if signed_url.startswith(
        "http"
    ):

        return signed_url


    if signed_url.startswith(
        "/storage/v1"
    ):

        return (
            base_url
            + signed_url
        )


    return (
        base_url
        + "/storage/v1"
        + signed_url
    )


# ================================================================
# 7. MAIN API
# ================================================================

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

        # --------------------------------------------------------
        # READ JSON
        # --------------------------------------------------------

        data = await request.json()


        if not isinstance(
            data,
            dict
        ):

            raise ValueError(
                "Request body must be a JSON object."
            )


        # --------------------------------------------------------
        # GENERATE PDF
        # --------------------------------------------------------

        pdf_bytes = (
            generate_hybrid_omr_pdf(
                data
            )
        )


        # --------------------------------------------------------
        # FILE NAME
        # --------------------------------------------------------

        today_date = (
            datetime.now().strftime(
                "%d-%m-%Y"
            )
        )


        unique_token = (
            str(uuid.uuid4())[:8]
        )


        file_name = (
            "OMR_Exam_"
            + today_date
            + "_"
            + unique_token
            + ".pdf"
        )


        # --------------------------------------------------------
        # SUPABASE
        # --------------------------------------------------------

        download_url = (
            upload_to_supabase(
                pdf_bytes,
                file_name
            )
        )


        # --------------------------------------------------------
        # RESPONSE TO FLUTTERFLOW
        # --------------------------------------------------------

        return {

            "success": True,

            "download_url":
                download_url,

            "file_name":
                file_name
        }


    except HTTPException:

        raise


    except Exception as error:

        print(
            f"PDF Generation Error: {error}"
        )

        traceback.print_exc()

        raise HTTPException(

            status_code=500,

            detail=str(error)
        )


# ================================================================
# 8. HEALTH CHECK
# ================================================================

@app.get("/")
async def health_check():

    return {

        "status": "ok",

        "service":
            "School OMR & Question Engine",

        "pdf_engine":
            "ReportLab",

        "devanagari_font":
            "Noto Sans Devanagari",

        "text_shaping":
            "HarfBuzz",

        "status_message":
            "Hindi shaping enabled"
    }

