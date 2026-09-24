import os
import io
import uuid
import json
import urllib.request
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Security, HTTPException
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fpdf import FPDF
from supabase import create_client, Client
import qrcode


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="School OMR PDF API",
    version="2.0.0"
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
APP_API_SECRET_KEY = os.getenv("APP_API_SECRET_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL environment variable is missing")

if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY environment variable is missing")

if not APP_API_SECRET_KEY:
    raise RuntimeError("APP_API_SECRET_KEY environment variable is missing")


# ============================================================
# SUPABASE
# ============================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


# ============================================================
# API SECURITY
# ============================================================

api_key_header = APIKeyHeader(
    name="x-api-key",
    auto_error=False
)


def verify_api_key(api_key: Optional[str] = Security(api_key_header)):
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key"
        )

    if api_key != APP_API_SECRET_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return True


# ============================================================
# FONT
# ============================================================

FONT_DIR = "/tmp/fonts"

REGULAR_FONT = os.path.join(
    FONT_DIR,
    "NotoSansDevanagari-Regular.ttf"
)

BOLD_FONT = os.path.join(
    FONT_DIR,
    "NotoSansDevanagari-Bold.ttf"
)

REGULAR_FONT_URL = (
    "https://raw.githubusercontent.com/googlefonts/noto-fonts/"
    "main/hinted/ttf/NotoSansDevanagari/"
    "NotoSansDevanagari-Regular.ttf"
)

BOLD_FONT_URL = (
    "https://raw.githubusercontent.com/googlefonts/noto-fonts/"
    "main/hinted/ttf/NotoSansDevanagari/"
    "NotoSansDevanagari-Bold.ttf"
)


def download_file(url: str, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if not os.path.exists(path) or os.path.getsize(path) < 10000:
        urllib.request.urlretrieve(url, path)


def ensure_fonts():
    download_file(
        REGULAR_FONT_URL,
        REGULAR_FONT
    )

    download_file(
        BOLD_FONT_URL,
        BOLD_FONT
    )

    if not os.path.exists(REGULAR_FONT):
        raise RuntimeError("NotoSansDevanagari-Regular.ttf not found")

    if not os.path.exists(BOLD_FONT):
        raise RuntimeError("NotoSansDevanagari-Bold.ttf not found")


ensure_fonts()


# ============================================================
# PDF CLASS
# ============================================================

class HindiPDF(FPDF):

    def __init__(self):
        super().__init__(
            orientation="P",
            unit="mm",
            format="A4"
        )

        # Unicode Devanagari fonts
        self.add_font(
            family="NotoDevanagari",
            style="",
            fname=REGULAR_FONT
        )

        self.add_font(
            family="NotoDevanagari",
            style="B",
            fname=BOLD_FONT
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # HarfBuzz text shaping
        # ----------------------------------------------------
        #
        # This is the critical difference from the previous
        # ReportLab implementation.
        #
        # fpdf2 uses HarfBuzz / uharfbuzz for complex scripts.
        #
        self.set_text_shaping(
            use_shaping_engine=True,
            script="deva",
            language="hi",
            direction="ltr"
        )

        self.set_auto_page_break(
            auto=False
        )

        self.set_margins(
            left=10,
            top=10,
            right=10
        )

        self.set_font(
            "NotoDevanagari",
            "",
            10
        )


# ============================================================
# HELPERS
# ============================================================

def safe_str(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    return str(value)


def clean_text(value: Any) -> str:
    """
    IMPORTANT:
    Do NOT manually reorder Devanagari characters here.

    HarfBuzz must receive the original Unicode text.
    """

    text = safe_str(value)

    # Remove accidental null characters only.
    text = text.replace("\x00", "")

    return text.strip()


def get_first(data: Dict[str, Any], *keys, default=""):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]

    return default


# ============================================================
# DRAWING HELPERS
# ============================================================

def draw_corner_markers(pdf: FPDF):

    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)

    page_w = 210
    page_h = 297

    marker = 8
    margin = 5

    # Top left
    pdf.line(
        margin,
        margin,
        margin + marker,
        margin
    )

    pdf.line(
        margin,
        margin,
        margin,
        margin + marker
    )

    # Top right
    pdf.line(
        page_w - margin,
        margin,
        page_w - margin - marker,
        margin
    )

    pdf.line(
        page_w - margin,
        margin,
        page_w - margin,
        margin + marker
    )

    # Bottom left
    pdf.line(
        margin,
        page_h - margin,
        margin + marker,
        page_h - margin
    )

    pdf.line(
        margin,
        page_h - margin,
        margin,
        page_h - margin - marker
    )

    # Bottom right
    pdf.line(
        page_w - margin,
        page_h - margin,
        page_w - margin - marker,
        page_h - margin
    )

    pdf.line(
        page_w - margin,
        page_h - margin,
        page_w - margin,
        page_h - margin - marker
    )


def draw_circle(
    pdf: FPDF,
    x: float,
    y: float,
    radius: float = 2.5
):
    pdf.ellipse(
        x - radius,
        y - radius,
        radius * 2,
        radius * 2
    )


def draw_checkbox(
    pdf: FPDF,
    x: float,
    y: float,
    size: float = 3.5
):
    pdf.rect(
        x,
        y,
        size,
        size
    )


# ============================================================
# QUESTION TEXT WRAPPING
# ============================================================

def draw_question(
    pdf: FPDF,
    question_number: int,
    question_text: str,
    options: List[str],
    x: float,
    y: float,
    width: float,
    question_font_size: float = 8.7,
    option_font_size: float = 7.7,
    line_height: float = 4.6
):

    question_text = clean_text(question_text)

    if not question_text:
        question_text = "प्रश्न उपलब्ध नहीं है।"

    # --------------------------------------------------------
    # Question number
    # --------------------------------------------------------

    pdf.set_font(
        "NotoDevanagari",
        "B",
        question_font_size
    )

    pdf.set_xy(x, y)

    pdf.cell(
        8,
        line_height,
        text=f"{question_number}.",
        border=0
    )

    # --------------------------------------------------------
    # Question body
    # --------------------------------------------------------

    body_x = x + 7
    body_w = width - 7

    pdf.set_font(
        "NotoDevanagari",
        "",
        question_font_size
    )

    pdf.set_xy(body_x, y)

    # Multi-cell performs Unicode text shaping through
    # fpdf2/HarfBuzz.
    pdf.multi_cell(
        body_w,
        line_height,
        text=question_text,
        border=0,
        align="L"
    )

    current_y = pdf.get_y()

    # --------------------------------------------------------
    # Options
    # --------------------------------------------------------

    if not options:
        return current_y + 2

    option_labels = ["(A)", "(B)", "(C)", "(D)"]

    pdf.set_font(
        "NotoDevanagari",
        "",
        option_font_size
    )

    option_y = current_y + 0.5

    # Two-column options
    col_gap = 3
    option_w = (width - col_gap) / 2

    row = 0

    for i, option in enumerate(options[:4]):

        option = clean_text(option)

        if not option:
            option = ""

        col = i % 2

        if col == 0 and i > 0:
            row += 1

        ox = x + col * (option_w + col_gap)
        oy = option_y + row * 4.2

        label = option_labels[i]

        pdf.set_xy(
            ox,
            oy
        )

        pdf.cell(
            9,
            4,
            text=label,
            border=0
        )

        pdf.set_xy(
            ox + 8,
            oy
        )

        # Small width so long Hindi options wrap.
        pdf.multi_cell(
            option_w - 8,
            4,
            text=option,
            border=0,
            align="L"
        )

    return option_y + (row + 1) * 4.2 + 1.5


# ============================================================
# HEADER
# ============================================================

def draw_header(
    pdf: FPDF,
    payload: Dict[str, Any]
):

    pdf.set_text_color(0, 0, 0)

    school_name = clean_text(
        get_first(
            payload,
            "school_name",
            "schoolName",
            "school",
            default="विद्यालय"
        )
    )

    exam_name = clean_text(
        get_first(
            payload,
            "exam_name",
            "examName",
            "test_name",
            "testName",
            default="परीक्षा"
        )
    )

    class_name = clean_text(
        get_first(
            payload,
            "class_name",
            "className",
            "class",
            default=""
        )
    )

    subject = clean_text(
        get_first(
            payload,
            "subject",
            "subject_name",
            "subjectName",
            default=""
        )
    )

    pdf.set_font(
        "NotoDevanagari",
        "B",
        15
    )

    pdf.set_xy(10, 11)

    pdf.cell(
        190,
        7,
        text=school_name,
        border=0,
        align="C"
    )

    pdf.set_font(
        "NotoDevanagari",
        "B",
        11
    )

    pdf.set_xy(10, 19)

    pdf.cell(
        190,
        6,
        text=exam_name,
        border=0,
        align="C"
    )

    pdf.set_font(
        "NotoDevanagari",
        "",
        8.5
    )

    pdf.set_xy(10, 27)

    details = []

    if class_name:
        details.append(f"कक्षा: {class_name}")

    if subject:
        details.append(f"विषय: {subject}")

    date_value = get_first(
        payload,
        "date",
        "exam_date",
        "examDate",
        default=""
    )

    if date_value:
        details.append(
            f"दिनांक: {clean_text(date_value)}"
        )

    pdf.cell(
        190,
        5,
        text="    ".join(details),
        border=0,
        align="C"
    )


# ============================================================
# STUDENT DETAILS
# ============================================================

def draw_student_details(
    pdf: FPDF,
    payload: Dict[str, Any]
):

    y = 35

    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.35)

    pdf.rect(
        10,
        y,
        190,
        17
    )

    student_name = clean_text(
        get_first(
            payload,
            "student_name",
            "studentName",
            "name",
            default=""
        )
    )

    roll_number = clean_text(
        get_first(
            payload,
            "roll_number",
            "rollNumber",
            "roll_no",
            "rollNo",
            default=""
        )
    )

    section = clean_text(
        get_first(
            payload,
            "section",
            default=""
        )
    )

    pdf.set_font(
        "NotoDevanagari",
        "B",
        8.5
    )

    pdf.set_xy(13, y + 3)

    pdf.cell(
        30,
        5,
        text="विद्यार्थी का नाम:"
    )

    pdf.set_font(
        "NotoDevanagari",
        "",
        8.5
    )

    pdf.set_xy(42, y + 3)

    pdf.cell(
        70,
        5,
        text=student_name
    )

    pdf.set_font(
        "NotoDevanagari",
        "B",
        8.5
    )

    pdf.set_xy(118, y + 3)

    pdf.cell(
        20,
        5,
        text="अनुक्रमांक:"
    )

    pdf.set_font(
        "NotoDevanagari",
        "",
        8.5
    )

    pdf.set_xy(140, y + 3)

    pdf.cell(
        25,
        5,
        text=roll_number
    )

    pdf.set_font(
        "NotoDevanagari",
        "B",
        8.5
    )

    pdf.set_xy(168, y + 3)

    pdf.cell(
        15,
        5,
        text="सेक्शन:"
    )

    pdf.set_font(
        "NotoDevanagari",
        "",
        8.5
    )

    pdf.set_xy(184, y + 3)

    pdf.cell(
        12,
        5,
        text=section
    )

    # Signature line
    pdf.set_font(
        "NotoDevanagari",
        "",
        7.5
    )

    pdf.set_xy(13, y + 10)

    pdf.cell(
        170,
        4,
        text="हस्ताक्षर: ________________________________________________"
    )


# ============================================================
# QUESTIONS
# ============================================================

def normalize_questions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:

    raw_questions = get_first(
        payload,
        "questions",
        "question_list",
        "questionList",
        default=[]
    )

    if isinstance(raw_questions, str):

        try:
            raw_questions = json.loads(raw_questions)
        except Exception:
            raw_questions = []

    if not isinstance(raw_questions, list):
        return []

    result = []

    for q in raw_questions:

        if not isinstance(q, dict):
            continue

        question_text = get_first(
            q,
            "question",
            "question_text",
            "questionText",
            "text",
            default=""
        )

        raw_options = get_first(
            q,
            "options",
            "option",
            "choices",
            default=[]
        )

        if isinstance(raw_options, str):

            try:
                parsed = json.loads(raw_options)

                if isinstance(parsed, list):
                    raw_options = parsed
                else:
                    raw_options = [raw_options]

            except Exception:

                # If options are pipe separated
                if "|" in raw_options:
                    raw_options = raw_options.split("|")
                else:
                    raw_options = [raw_options]

        if not isinstance(raw_options, list):
            raw_options = []

        options = [
            clean_text(x)
            for x in raw_options
        ]

        result.append(
            {
                "question": clean_text(question_text),
                "options": options[:4]
            }
        )

    return result


# ============================================================
# OMR ANSWER AREA
# ============================================================

def draw_omr(
    pdf: FPDF,
    payload: Dict[str, Any],
    y: float
):

    questions = normalize_questions(payload)

    if not questions:
        return

    # Prevent overflow
    remaining = min(
        len(questions),
        30
    )

    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.35)

    box_h = 38

    if y + box_h > 288:
        y = 250

    pdf.rect(
        10,
        y,
        190,
        box_h
    )

    pdf.set_font(
        "NotoDevanagari",
        "B",
        8
    )

    pdf.set_xy(13, y + 3)

    pdf.cell(
        30,
        4,
        text="उत्तर पत्रक (OMR)"
    )

    pdf.set_font(
        "NotoDevanagari",
        "",
        6.5
    )

    pdf.set_xy(13, y + 8)

    pdf.cell(
        175,
        4,
        text="सही उत्तर के सामने दिए गए गोले को पेन से पूरी तरह भरें।"
    )

    start_y = y + 15

    # 3 columns
    columns = 3

    per_column = (remaining + columns - 1) // columns

    col_width = 61

    for col in range(columns):

        start_index = col * per_column
        end_index = min(
            start_index + per_column,
            remaining
        )

        x = 13 + col * col_width

        for idx in range(
            start_index,
            end_index
        ):

            q_no = idx + 1

            row = idx - start_index

            cy = start_y + row * 6.2

            pdf.set_font(
                "NotoDevanagari",
                "",
                6.8
            )

            pdf.set_xy(
                x,
                cy - 2
            )

            pdf.cell(
                8,
                4,
                text=str(q_no)
            )

            letters = ["A", "B", "C", "D"]

            for j, letter in enumerate(letters):

                cx = x + 10 + j * 10

                draw_circle(
                    pdf,
                    cx,
                    cy,
                    2.1
                )

                pdf.set_font(
                    "NotoDevanagari",
                    "",
                    5.5
                )

                pdf.set_xy(
                    cx - 1.5,
                    cy - 1.8
                )

                pdf.cell(
                    3,
                    3,
                    text=letter,
                    align="C"
                )


# ============================================================
# QR CODE
# ============================================================

def make_qr_image(
    data: str
) -> io.BytesIO:

    qr = qrcode.QRCode(
        version=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2
    )

    qr.add_data(data)

    qr.make(
        fit=True
    )

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    output = io.BytesIO()

    image.save(
        output,
        format="PNG"
    )

    output.seek(0)

    return output


def draw_qr(
    pdf: FPDF,
    payload: Dict[str, Any]
):

    qr_data = get_first(
        payload,
        "qr_data",
        "qrData",
        "student_id",
        "studentId",
        default=""
    )

    if not qr_data:
        return

    qr_buffer = make_qr_image(
        clean_text(qr_data)
    )

    # fpdf2 can accept a BytesIO image object
    pdf.image(
        qr_buffer,
        x=174,
        y=257,
        w=22,
        h=22
    )


# ============================================================
# PDF GENERATOR
# ============================================================

def generate_hybrid_omr_pdf(
    payload: Dict[str, Any]
) -> bytes:

    pdf = HindiPDF()

    pdf.add_page()

    # --------------------------------------------------------
    # Corner markers
    # --------------------------------------------------------

    draw_corner_markers(pdf)

    # --------------------------------------------------------
    # Header
    # --------------------------------------------------------

    draw_header(
        pdf,
        payload
    )

    # --------------------------------------------------------
    # Student details
    # --------------------------------------------------------

    draw_student_details(
        pdf,
        payload
    )

    # --------------------------------------------------------
    # Questions
    # --------------------------------------------------------

    questions = normalize_questions(
        payload
    )

    question_y = 58

    left_x = 11
    right_x = 106

    column_width = 93

    left_questions = questions[::2]
    right_questions = questions[1::2]

    max_question_y = 245

    # Left column
    y_left = question_y

    for index, q in enumerate(left_questions):

        y_before = y_left

        y_left = draw_question(
            pdf=pdf,
            question_number=index * 2 + 1,
            question_text=q["question"],
            options=q["options"],
            x=left_x,
            y=y_left,
            width=column_width
        )

        # Safety against excessive content
        if y_left > max_question_y:
            break

    # Right column
    y_right = question_y

    for index, q in enumerate(right_questions):

        y_before = y_right

        y_right = draw_question(
            pdf=pdf,
            question_number=index * 2 + 2,
            question_text=q["question"],
            options=q["options"],
            x=right_x,
            y=y_right,
            width=column_width
        )

        if y_right > max_question_y:
            break

    # --------------------------------------------------------
    # OMR
    # --------------------------------------------------------

    omr_y = 248

    draw_omr(
        pdf,
        payload,
        omr_y
    )

    # --------------------------------------------------------
    # QR
    # --------------------------------------------------------

    draw_qr(
        pdf,
        payload
    )

    # --------------------------------------------------------
    # Footer
    # --------------------------------------------------------

    pdf.set_font(
        "NotoDevanagari",
        "",
        6.5
    )

    pdf.set_xy(
        10,
        289
    )

    pdf.cell(
        190,
        4,
        text="यह दस्तावेज़ स्वचालित रूप से तैयार किया गया है।",
        align="C"
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    pdf_bytes = bytes(
        pdf.output()
    )

    return pdf_bytes


# ============================================================
# SUPABASE STORAGE
# ============================================================

SUPABASE_BUCKET = os.getenv(
    "SUPABASE_PDF_BUCKET",
    "letters"
)


def upload_pdf_to_supabase(
    pdf_bytes: bytes,
    file_name: str
) -> str:

    storage_path = (
        f"omr/{datetime.utcnow().strftime('%Y/%m/%d')}/"
        f"{file_name}"
    )

    try:

        supabase.storage.from_(
            SUPABASE_BUCKET
        ).upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

    except Exception as e:

        # If already exists, try update
        try:

            supabase.storage.from_(
                SUPABASE_BUCKET
            ).update(
                path=storage_path,
                file=pdf_bytes,
                file_options={
                    "content-type": "application/pdf",
                    "upsert": "true"
                }
            )

        except Exception:

            raise RuntimeError(
                f"Supabase PDF upload failed: {str(e)}"
            )

    # --------------------------------------------------------
    # Signed URL
    # --------------------------------------------------------

    try:

        signed = supabase.storage.from_(
            SUPABASE_BUCKET
        ).create_signed_url(
            storage_path,
            60 * 60 * 24
        )

        if isinstance(signed, dict):

            signed_url = (
                signed.get("signedURL")
                or signed.get("signedUrl")
                or signed.get("signed_url")
            )

            if signed_url:
                return signed_url

        if isinstance(signed, str):
            return signed

    except Exception as e:

        raise RuntimeError(
            f"Could not create signed URL: {str(e)}"
        )

    raise RuntimeError(
        "Supabase did not return a signed URL"
    )


# ============================================================
# REQUEST MODEL
# ============================================================

class OMRRequest(BaseModel):
    data: Dict[str, Any]


# ============================================================
# HEALTH
# ============================================================

@app.get("/")
def root():

    return {
        "status": "ok",
        "service": "School OMR PDF API",
        "pdf_engine": "fpdf2",
        "text_shaping": "HarfBuzz",
        "script": "Devanagari"
    }


@app.get("/health")
def health():

    return {
        "status": "healthy",
        "font_regular": os.path.exists(REGULAR_FONT),
        "font_bold": os.path.exists(BOLD_FONT),
        "supabase_configured": bool(SUPABASE_URL),
        "text_shaping": "HarfBuzz"
    }


# ============================================================
# GENERATE OMR PDF
# ============================================================

@app.post(
    "/generate-omr-pdf",
    dependencies=[Security(verify_api_key)]
)
def generate_omr_pdf(
    request: OMRRequest
):

    try:

        payload = request.data

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="data must be an object"
            )

        # ----------------------------------------------------
        # Generate PDF
        # ----------------------------------------------------

        pdf_bytes = generate_hybrid_omr_pdf(
            payload
        )

        if not pdf_bytes:
            raise RuntimeError(
                "PDF generation returned empty data"
            )

        # ----------------------------------------------------
        # File name
        # ----------------------------------------------------

        student_name = clean_text(
            get_first(
                payload,
                "student_name",
                "studentName",
                "name",
                default="student"
            )
        )

        # Keep filename ASCII-safe
        safe_filename = "".join(
            c if c.isalnum() or c in "-_"
            else "_"
            for c in student_name
        )

        if not safe_filename:
            safe_filename = "student"

        file_name = (
            f"{safe_filename}_"
            f"{uuid.uuid4().hex[:10]}.pdf"
        )

        # ----------------------------------------------------
        # Upload
        # ----------------------------------------------------

        signed_url = upload_pdf_to_supabase(
            pdf_bytes,
            file_name
        )

        return {
            "success": True,
            "file_name": file_name,
            "file_url": signed_url,
            "signed_url": signed_url,
            "size_bytes": len(pdf_bytes)
        }

    except HTTPException:
        raise

    except Exception as e:

        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# LOCAL RUN
# ============================================================

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
