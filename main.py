import os
import io
import json
import uuid
import base64
import urllib.request
import traceback




from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Security, HTTPException, Request
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

from supabase import create_client, Client

import qrcode

from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="School OMR PDF API",
    version="3.0.0"
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

SUPABASE_PDF_BUCKET = os.getenv(
    "SUPABASE_PDF_BUCKET",
    "letters"
)


if not SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL environment variable is missing"
    )

if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError(
        "SUPABASE_SERVICE_ROLE_KEY environment variable is missing"
    )

if not APP_API_SECRET_KEY:
    raise RuntimeError(
        "APP_API_SECRET_KEY environment variable is missing"
    )


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


def verify_api_key(
    api_key: Optional[str] = Security(api_key_header)
):

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
# FONT SETUP
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


def download_font(
    url: str,
    path: str
):

    os.makedirs(
        os.path.dirname(path),
        exist_ok=True
    )

    if not os.path.exists(path):

        urllib.request.urlretrieve(
            url,
            path
        )


def ensure_fonts():

    download_font(
        REGULAR_FONT_URL,
        REGULAR_FONT
    )

    download_font(
        BOLD_FONT_URL,
        BOLD_FONT
    )

    if not os.path.exists(REGULAR_FONT):
        raise RuntimeError(
            "NotoSansDevanagari-Regular.ttf missing"
        )

    if not os.path.exists(BOLD_FONT):
        raise RuntimeError(
            "NotoSansDevanagari-Bold.ttf missing"
        )


ensure_fonts()


# ============================================================
# FONT CONFIGURATION
# ============================================================

font_config = FontConfiguration()


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_str(value: Any) -> str:

    if value is None:
        return ""

    if isinstance(value, str):
        return value

    return str(value)


def clean_text(value: Any) -> str:

    """
    VERY IMPORTANT:

    Do NOT manually reorder Devanagari characters.

    In particular, do NOT move:
        ि
        ी
        ु
        ू
        े
        ै
        ो
        ौ

    Pango + HarfBuzz handles this.
    """

    text = safe_str(value)

    text = text.replace("\x00", "")

    return text.strip()


def get_first(
    data: Dict[str, Any],
    *keys,
    default=""
):

    for key in keys:

        if key in data:

            value = data[key]

            if value is not None:
                return value

    return default


def html_escape(
    text: Any
) -> str:

    text = clean_text(text)

    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ============================================================
# QUESTIONS
# ============================================================

def normalize_questions(
    payload: Dict[str, Any]
) -> List[Dict[str, Any]]:

    raw_questions = get_first(
        payload,
        "questions",
        "question_list",
        "questionList",
        default=[]
    )

    if isinstance(raw_questions, str):

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

        if isinstance(
            raw_options,
            str
        ):

            try:

                parsed = json.loads(
                    raw_options
                )

                if isinstance(
                    parsed,
                    list
                ):
                    raw_options = parsed

                else:
                    raw_options = [
                        raw_options
                    ]

            except Exception:

                if "|" in raw_options:

                    raw_options = (
                        raw_options.split("|")
                    )

                else:

                    raw_options = [
                        raw_options
                    ]

        if not isinstance(
            raw_options,
            list
        ):
            raw_options = []

        options = []

        for option in raw_options[:4]:

            options.append(
                clean_text(option)
            )

        result.append(
            {
                "question": clean_text(
                    question_text
                ),
                "options": options
            }
        )

    return result


# ============================================================
# QR CODE
# ============================================================

def create_qr_base64(
    data: str
) -> str:

    if not data:
        return ""

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

    encoded = base64.b64encode(
        output.getvalue()
    ).decode("ascii")

    return (
        "data:image/png;base64,"
        + encoded
    )


# ============================================================
# HTML QUESTION
# ============================================================

def question_html(
    number: int,
    question: Dict[str, Any]
) -> str:

    text = question.get(
        "question",
        ""
    )

    text = clean_text(text)

    if not text:

        text = "प्रश्न उपलब्ध नहीं है।"

    options = question.get(
        "options",
        []
    )

    option_labels = [
        "A",
        "B",
        "C",
        "D"
    ]

    option_blocks = []

    for i, option in enumerate(
        options[:4]
    ):

        option = clean_text(
            option
        )

        label = option_labels[i]

        option_blocks.append(
            f"""
            <div class="option">
                <span class="option-label">
                    ({label})
                </span>
                <span class="option-text">
                    {html_escape(option)}
                </span>
            </div>
            """
        )

    options_html = "".join(
        option_blocks
    )

    return f"""
    <div class="question">
        <div class="question-line">
            <span class="question-number">
                {number}.
            </span>

            <span class="question-text">
                {html_escape(text)}
            </span>
        </div>

        <div class="options">
            {options_html}
        </div>
    </div>
    """


# ============================================================
# OMR HTML
# ============================================================

def omr_row_html(
    number: int
) -> str:

    return f"""
    <div class="omr-row">

        <span class="omr-number">
            {number}
        </span>

        <span class="bubble">
            A
        </span>

        <span class="bubble">
            B
        </span>

        <span class="bubble">
            C
        </span>

        <span class="bubble">
            D
        </span>

    </div>
    """


def build_omr_html(
    count: int
) -> str:

    count = min(
        count,
        30
    )

    if count <= 0:
        return ""

    columns = [
        [],
        [],
        []
    ]

    per_column = (
        count + 2
    ) // 3

    for index in range(
        count
    ):

        column = min(
            index // per_column,
            2
        )

        columns[column].append(
            index + 1
        )

    html_columns = []

    for column in columns:

        rows = []

        for number in column:

            rows.append(
                omr_row_html(
                    number
                )
            )

        html_columns.append(
            f"""
            <div class="omr-column">
                {"".join(rows)}
            </div>
            """
        )

    return f"""
    <div class="omr-box">

        <div class="omr-title">
            उत्तर पत्रक (OMR)
        </div>

        <div class="omr-instruction">
            सही उत्तर के सामने दिए गए गोले को पेन से पूरी तरह भरें।
        </div>

        <div class="omr-columns">
            {"".join(html_columns)}
        </div>

    </div>
    """


# ============================================================
# COMPLETE HTML
# ============================================================

def build_pdf_html(
    payload: Dict[str, Any],
    qr_data_uri: str
) -> str:

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

    exam_date = clean_text(
        get_first(
            payload,
            "date",
            "exam_date",
            "examDate",
            default=""
        )
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

    questions = normalize_questions(
        payload
    )

    # --------------------------------------------------------
    # Split questions into two columns
    # --------------------------------------------------------

    left_questions = []
    right_questions = []

    for index, question in enumerate(
        questions
    ):

        if index % 2 == 0:

            left_questions.append(
                question_html(
                    index + 1,
                    question
                )
            )

        else:

            right_questions.append(
                question_html(
                    index + 1,
                    question
                )
            )

    # --------------------------------------------------------
    # HTML
    # --------------------------------------------------------

    qr_html = ""

    if qr_data_uri:

        qr_html = f"""
        <img
            class="qr"
            src="{qr_data_uri}"
        />
        """

    html = f"""
<!DOCTYPE html>

<html lang="hi">

<head>

<meta charset="UTF-8">

<style>

@page {{
    size: A4;
    margin: 0;
}}

* {{
    box-sizing: border-box;
}}

html,
body {{
    margin: 0;
    padding: 0;
}}

body {{

    font-family:
        "NotoSansDevanagari",
        sans-serif;

    font-weight: 400;

    color: #000;

    width: 210mm;
    height: 297mm;

    font-size: 9pt;

    line-height: 1.35;

    -webkit-font-smoothing: antialiased;
}}


/* =========================================================
   PAGE
   ========================================================= */

.page {{

    position: relative;

    width: 210mm;
    height: 297mm;

    padding:
        10mm
        10mm
        8mm
        10mm;

    overflow: hidden;
}}


/* =========================================================
   CORNER MARKERS
   ========================================================= */

.marker {{
    position: absolute;

    width: 8mm;
    height: 8mm;

    border-color: #000;

    border-style: solid;

    border-width: 0;
}}

.marker.tl {{
    left: 5mm;
    top: 5mm;

    border-left-width: 0.5mm;
    border-top-width: 0.5mm;
}}

.marker.tr {{
    right: 5mm;
    top: 5mm;

    border-right-width: 0.5mm;
    border-top-width: 0.5mm;
}}

.marker.bl {{
    left: 5mm;
    bottom: 5mm;

    border-left-width: 0.5mm;
    border-bottom-width: 0.5mm;
}}

.marker.br {{
    right: 5mm;
    bottom: 5mm;

    border-right-width: 0.5mm;
    border-bottom-width: 0.5mm;
}}


/* =========================================================
   HEADER
   ========================================================= */

.header {{
    text-align: center;
}}

.school-name {{

    font-family:
        "NotoSansDevanagari",
        sans-serif;

    font-weight: 700;

    font-size: 15pt;

    line-height: 1.25;

    margin: 0;

    padding: 0;
}}

.exam-name {{

    font-weight: 700;

    font-size: 11pt;

    margin-top: 1mm;
}}

.exam-details {{

    font-size: 8.5pt;

    margin-top: 1.5mm;
}}


/* =========================================================
   STUDENT BOX
   ========================================================= */

.student-box {{

    margin-top: 2mm;

    height: 17mm;

    border:
        0.35mm
        solid
        #000;

    padding: 2.5mm 3mm;
}}

.student-row {{

    width: 100%;

    display: table;

    table-layout: fixed;
}}

.student-cell {{

    display: table-cell;

    vertical-align: middle;

    font-size: 8.5pt;
}}

.student-label {{
    font-weight: 700;
}}

.student-name {{
    width: 52%;
}}

.roll {{
    width: 23%;
}}

.section {{
    width: 25%;
}}

.signature {{
    margin-top: 1.5mm;

    font-size: 7.5pt;
}}


/* =========================================================
   QUESTION AREA
   ========================================================= */

.question-area {{

    margin-top: 4mm;

    height: 181mm;

    display: table;

    width: 100%;

    table-layout: fixed;
}}

.question-column {{

    display: table-cell;

    vertical-align: top;

    width: 50%;

    padding-right: 4mm;
}}

.question-column.right {{

    padding-left: 4mm;

    padding-right: 0;
}}

.question {{

    page-break-inside: avoid;

    break-inside: avoid;

    margin-bottom: 3.2mm;
}}

.question-line {{

    display: block;

    font-size: 8.7pt;

    line-height: 1.35;

    text-align: left;
}}

.question-number {{

    font-weight: 700;

    display: inline;
}}

.question-text {{

    display: inline;

    font-weight: 400;
}}


/* =========================================================
   OPTIONS
   ========================================================= */

.options {{

    margin-top: 1mm;

    display: table;

    width: 100%;

    table-layout: fixed;
}}

.option {{

    display: table-cell;

    width: 50%;

    padding-right: 2mm;

    vertical-align: top;

    font-size: 7.7pt;

    line-height: 1.3;
}}

.option:nth-child(n+3) {{

    display: table-row;
}}

.option-label {{

    font-weight: 400;

    margin-right: 1mm;
}}

.option-text {{
    font-weight: 400;
}}


/* =========================================================
   OMR
   ========================================================= */

.omr-box {{

    position: absolute;

    left: 10mm;

    right: 10mm;

    bottom: 10mm;

    height: 39mm;

    border:
        0.35mm
        solid
        #000;

    padding:
        2.5mm
        3mm;
}}

.omr-title {{

    font-weight: 700;

    font-size: 8pt;

    line-height: 1.2;
}}

.omr-instruction {{

    font-size: 6.5pt;

    margin-top: 1mm;
}}

.omr-columns {{

    display: table;

    width: 100%;

    table-layout: fixed;

    margin-top: 1.5mm;
}}

.omr-column {{

    display: table-cell;

    width: 33.33%;

    vertical-align: top;
}}

.omr-row {{

    height: 5.5mm;

    line-height: 5.5mm;

    white-space: nowrap;
}}

.omr-number {{

    display: inline-block;

    width: 7mm;

    font-size: 6.8pt;
}}

.bubble {{

    display: inline-block;

    width: 5mm;
    height: 5mm;

    border:
        0.35mm
        solid
        #000;

    border-radius: 50%;

    text-align: center;

    line-height: 4.3mm;

    font-size: 5.5pt;

    margin-right: 3mm;

    vertical-align: middle;
}}


/* =========================================================
   QR
   ========================================================= */

.qr {{

    position: absolute;

    right: 14mm;

    bottom: 13mm;

    width: 22mm;

    height: 22mm;
}}


/* =========================================================
   FOOTER
   ========================================================= */

.footer {{

    position: absolute;

    left: 10mm;

    right: 10mm;

    bottom: 5mm;

    text-align: center;

    font-size: 6.5pt;
}}

</style>

<style>

@font-face {{

    font-family:
        "NotoSansDevanagari";

    src:
        url("file://{REGULAR_FONT}");

    font-style:
        normal;

    font-weight:
        400;
}}

@font-face {{

    font-family:
        "NotoSansDevanagari";

    src:
        url("file://{BOLD_FONT}");

    font-style:
        normal;

    font-weight:
        700;
}}

</style>

</head>


<body>

<div class="page">


    <!-- CORNER MARKERS -->

    <div class="marker tl"></div>
    <div class="marker tr"></div>
    <div class="marker bl"></div>
    <div class="marker br"></div>


    <!-- HEADER -->

    <div class="header">

        <div class="school-name">
            {html_escape(school_name)}
        </div>

        <div class="exam-name">
            {html_escape(exam_name)}
        </div>

        <div class="exam-details">

            {"कक्षा: " + html_escape(class_name)
                if class_name else ""}

            {"&nbsp;&nbsp;&nbsp;&nbsp;"
                if class_name and subject else ""}

            {"विषय: " + html_escape(subject)
                if subject else ""}

            {"&nbsp;&nbsp;&nbsp;&nbsp;"
                if (class_name or subject) and exam_date else ""}

            {"दिनांक: " + html_escape(exam_date)
                if exam_date else ""}

        </div>

    </div>


    <!-- STUDENT DETAILS -->

    <div class="student-box">

        <div class="student-row">

            <div class="student-cell student-name">

                <span class="student-label">
                    विद्यार्थी का नाम:
                </span>

                {html_escape(student_name)}

            </div>


            <div class="student-cell roll">

                <span class="student-label">
                    अनुक्रमांक:
                </span>

                {html_escape(roll_number)}

            </div>


            <div class="student-cell section">

                <span class="student-label">
                    सेक्शन:
                </span>

                {html_escape(section)}

            </div>

        </div>


        <div class="signature">

            हस्ताक्षर:
            ________________________________________________

        </div>

    </div>


    <!-- QUESTIONS -->

    <div class="question-area">

        <div class="question-column">

            {"".join(left_questions)}

        </div>


        <div class="question-column right">

            {"".join(right_questions)}

        </div>

    </div>


    <!-- OMR -->

    {build_omr_html(len(questions))}


    <!-- QR -->

    {qr_html}


    <!-- FOOTER -->

    <div class="footer">

        यह दस्तावेज़ स्वचालित रूप से तैयार किया गया है।

    </div>


</div>

</body>

</html>
"""

    return html


# ============================================================
# PDF GENERATION
# ============================================================

def generate_hybrid_omr_pdf(
    payload: Dict[str, Any]
) -> bytes:

    qr_data = clean_text(
        get_first(
            payload,
            "qr_data",
            "qrData",
            "student_id",
            "studentId",
            default=""
        )
    )

    qr_data_uri = create_qr_base64(
        qr_data
    )

    html_string = build_pdf_html(
        payload,
        qr_data_uri
    )

    pdf_bytes = HTML(
        string=html_string,
        base_url="/"
    ).write_pdf(
        font_config=font_config
    )

    if not pdf_bytes:
        raise RuntimeError(
            "Generated PDF is empty"
        )

    return pdf_bytes


# ============================================================
# SUPABASE UPLOAD
# ============================================================

def upload_pdf_to_supabase(
    pdf_bytes: bytes,
    file_name: str
) -> str:

    storage_path = (
        "omr/"
        + datetime.utcnow().strftime("%Y/%m/%d/")
        + file_name
    )

    try:

        supabase.storage.from_(
            SUPABASE_PDF_BUCKET
        ).upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={
                "content-type":
                    "application/pdf",
                "upsert":
                    "true"
            }
        )

    except Exception as upload_error:

        try:

            supabase.storage.from_(
                SUPABASE_PDF_BUCKET
            ).update(
                path=storage_path,
                file=pdf_bytes,
                file_options={
                    "content-type":
                        "application/pdf",
                    "upsert":
                        "true"
                }
            )

        except Exception:

            raise RuntimeError(
                "Supabase upload failed: "
                + str(upload_error)
            )

    # --------------------------------------------------------
    # SIGNED URL
    # --------------------------------------------------------

    try:

        signed = (
            supabase
            .storage
            .from_(SUPABASE_PDF_BUCKET)
            .create_signed_url(
                storage_path,
                60 * 60 * 24
            )
        )

        if isinstance(
            signed,
            dict
        ):

            url = (
                signed.get("signedURL")
                or signed.get("signedUrl")
                or signed.get("signed_url")
            )

            if url:
                return url

        if isinstance(
            signed,
            str
        ):
            return signed

    except Exception as e:

        raise RuntimeError(
            "Signed URL creation failed: "
            + str(e)
        )

    raise RuntimeError(
        "Supabase signed URL was not returned"
    )


# ============================================================
# REQUEST MODEL
# ============================================================


Optional[Dict[str, Any]]
= None
class Config:
extra = "allow"


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "status": "ok",
        "service": "School OMR PDF API",
        "pdf_engine": "WeasyPrint",
        "text_engine": "Pango + HarfBuzz",
        "font": "Noto Sans Devanagari",
        "language": "hi"
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "healthy",

        "regular_font":
            os.path.exists(
                REGULAR_FONT
            ),

        "bold_font":
            os.path.exists(
                BOLD_FONT
            ),

        "supabase":
            bool(SUPABASE_URL),

        "pdf_engine":
            "WeasyPrint",

        "text_engine":
            "Pango + HarfBuzz"
    }


# ============================================================
# GENERATE OMR PDF
# ============================================================

@app.post(
    "/generate-omr-pdf",
    dependencies=[
        Security(verify_api_key)
    ]
)
async def generate_omr_pdf(
    request: Request
):

    try:

        # ====================================================
        # READ JSON BODY
        # ====================================================

        body = await request.json()

        # ====================================================
        # SUPPORT BOTH REQUEST FORMATS
        #
        # FORMAT 1:
        # {
        #     "data": {
        #         ...
        #     }
        # }
        #
        # FORMAT 2:
        # {
        #     "school_name": "...",
        #     "questions": [...]
        # }
        # ====================================================

        if (
            isinstance(body, dict)
            and isinstance(body.get("data"), dict)
        ):

            payload = body["data"]

        elif isinstance(body, dict):

            payload = body

        else:

            raise HTTPException(
                status_code=400,
                detail="Request body must be a JSON object"
            )

        # ====================================================
        # GENERATE PDF
        # ====================================================

        pdf_bytes = generate_hybrid_omr_pdf(
            payload
        )

        if not pdf_bytes:

            raise RuntimeError(
                "PDF generation returned empty data"
            )

        # ====================================================
        # FILE NAME
        # ====================================================

        student_name = clean_text(
            get_first(
                payload,
                "student_name",
                "studentName",
                "name",
                default="student"
            )
        )

        safe_filename = "".join(
            c
            if (
                c.isalnum()
                or c in "-_"
            )
            else "_"
            for c in student_name
        )

        if not safe_filename:

            safe_filename = "student"

        file_name = (
            safe_filename
            + "_"
            + uuid.uuid4().hex[:10]
            + ".pdf"
        )

        # ====================================================
        # UPLOAD TO SUPABASE
        # ====================================================

        signed_url = upload_pdf_to_supabase(
            pdf_bytes,
            file_name
        )

        # ====================================================
        # RESPONSE
        # ====================================================

        return {

            "success": True,

            "file_name":
                file_name,

            "file_url":
                signed_url,

            "signed_url":
                signed_url,

            "size_bytes":
                len(pdf_bytes)
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
# LOCAL SERVER
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
