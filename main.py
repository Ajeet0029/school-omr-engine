import io
import os
import json
import uuid
import urllib.request
import traceback
from datetime import datetime
from typing import List, Optional, Any, Dict

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client

# ReportLab इम्पोर्ट्स
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import qrcode
from PIL import Image, ImageDraw, ImageFont, features
from reportlab.lib.utils import ImageReader

app = FastAPI(title="School OMR & Question Engine")

# =====================================================================
# 1. हिंदी/देवनागरी फॉन्ट सेटअप (Auto Font Setup)
# =====================================================================
import os
import urllib.request
import re
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ------------------ 1. FONT SETUP ------------------
FONT_NAME = "NotoDevanagari"
FONT_BOLD = "NotoDevanagari-Bold"

def setup_fonts():
    font_dir = "/tmp/fonts"
    os.makedirs(font_dir, exist_ok=True)
   
    font_path_reg = os.path.join(font_dir, "NotoSansDevanagari-Regular.ttf")
    font_path_bld = os.path.join(font_dir, "NotoSansDevanagari-Bold.ttf")
   
    headers = {'User-Agent': 'Mozilla/5.0'}
   
    # Google Fonts के आधिकारिक GitHub से सीधे TTF डाउनलोड
    if not os.path.exists(font_path_reg) or os.path.getsize(font_path_reg) < 1000:
        url_reg = "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Regular.ttf"
        req = urllib.request.Request(url_reg, headers=headers)
        with urllib.request.urlopen(req) as resp, open(font_path_reg, 'wb') as f:
            f.write(resp.read())
           
    if not os.path.exists(font_path_bld) or os.path.getsize(font_path_bld) < 1000:
        url_bld = "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf"
        req = urllib.request.Request(url_bld, headers=headers)
        with urllib.request.urlopen(req) as resp, open(font_path_bld, 'wb') as f:
            f.write(resp.read())

    pdfmetrics.registerFont(TTFont(FONT_NAME, font_path_reg, shapable=True))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, font_path_bld, shapable=True))

try:
    setup_fonts()
    print("Devanagari Fonts Loaded Successfully")
except Exception as e:
    print(f"Font Setup Error: {e}")

# ------------------ 2. HINDI TEXT REORDERING FIXER ------------------
# ------------------ 2. HINDI TEXT RENDERER ------------------

HINDI_RENDER_SCALE = 6

def draw_hindi_text(c, x, y, text, font_size, bold=False):
    """
    Render Hindi through Pillow + RAQM and place it into the
    existing ReportLab PDF without changing the PDF layout.
    """

    text = "" if text is None else str(text)

    if not text:
        return

    # RAQM is required for proper Devanagari shaping.
    if not features.check("raqm"):
        raise RuntimeError(
            "Pillow RAQM support is not available on the server."
        )

    if bold:
        font_path = "/tmp/fonts/NotoSansDevanagari-Bold.ttf"
    else:
        font_path = "/tmp/fonts/NotoSansDevanagari-Regular.ttf"

    font_px = max(
        1,
        int(round(font_size * HINDI_RENDER_SCALE))
    )

    font = ImageFont.truetype(
        font_path,
        font_px
    )

    # Measure the text with RAQM/HarfBuzz shaping.
    dummy = Image.new(
        "RGBA",
        (10, 10),
        (255, 255, 255, 0)
    )

    draw = ImageDraw.Draw(dummy)

    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font,
        anchor="ls",
        direction="ltr",
        language="hi"
    )

    pad = 2 * HINDI_RENDER_SCALE

    img_w = max(
        1,
        bbox[2] - bbox[0] + (2 * pad)
    )

    img_h = max(
        1,
        bbox[3] - bbox[1] + (2 * pad)
    )

    img = Image.new(
        "RGBA",
        (img_w, img_h),
        (255, 255, 255, 0)
    )

    draw = ImageDraw.Draw(img)

    baseline_x = pad - bbox[0]
    baseline_y = pad - bbox[1]

    draw.text(
        (baseline_x, baseline_y),
        text,
        font=font,
        fill=(0, 0, 0, 255),
        anchor="ls",
        direction="ltr",
        language="hi"
    )

    # Put the rendered Hindi image back onto the
    # existing ReportLab canvas at the same baseline.
    c.drawImage(
        ImageReader(img),
        x - (baseline_x / HINDI_RENDER_SCALE),
        y - ((img_h - baseline_y) / HINDI_RENDER_SCALE),
        width=img_w / HINDI_RENDER_SCALE,
        height=img_h / HINDI_RENDER_SCALE,
        mask="auto"
    )

# =====================================================================
# 2. Supabase एवं सुरक्षा सेटिंग्स
# =====================================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "your-service-role-key")
API_SECRET_KEY = os.getenv("APP_API_SECRET_KEY", "MySecureSchoolOmrKey_2026_Secure")
BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "omr-sheets")

API_KEY_NAME = "x-api-key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

async def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key or api_key != API_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="अनधिकृत पहुँच (Unauthorized): मान्य x-api-key आवश्यक है।"
        )
    return api_key


# =====================================================================
# 3. हाइब्रिड PDF लेआउट (ऊपर प्रश्न + नीचे OMR स्ट्रिप)
# =====================================================================
def generate_hybrid_omr_pdf(payload):
    """
    FINAL PDF RENDERER

    IMPORTANT:
    - Only PDF layout is handled here.
    - API, authentication, Supabase upload,
      FlutterFlow response contract and other backend code
      remain unchanged.
    """

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4

    # ===============================================================
    # FIXED OMR GEOMETRY
    # ===============================================================

    strip_y = 154
    left_margin = 40
    right_margin = width - 40

    # Four fixed scanner / registration markers
    anchor_size = 14
    anchor_inset = 18

    c.setFillColor(colors.black)

    anchors = [
        (
            anchor_inset,
            height - anchor_inset - anchor_size
        ),
        (
            width - anchor_inset - anchor_size,
            height - anchor_inset - anchor_size
        ),
        (
            anchor_inset,
            anchor_inset
        ),
        (
            width - anchor_inset - anchor_size,
            anchor_inset
        ),
    ]

    for ax, ay in anchors:
        c.rect(
            ax,
            ay,
            anchor_size,
            anchor_size,
            fill=1,
            stroke=0
        )

    # ===============================================================
    # SAFE PAYLOAD HELPERS
    # ===============================================================

    def getv(*keys, default=""):
        for key in keys:
            value = payload.get(key)

            if value not in (None, ""):
                return str(value)

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

    # ===============================================================
    # HEADER
    # ===============================================================

    header_left = 45
    header_top = height - 34

    if DEV_RE.search(school):

        draw_mixed_text(
            c,
            header_left,
            header_top,
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
            header_left,
            header_top,
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

    meta_text = "   |   ".join(meta_parts)

    if meta_text:

        draw_mixed_text(
            c,
            header_left,
            height - 48,
            meta_text,
            7.2,
            False
        )

    instruction = (
        "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई OMR पट्टी में "
        "नीले/काले पेन से गोला भरकर दें।"
    )

    draw_mixed_text(
        c,
        header_left,
        height - 63,
        instruction,
        6.8,
        False
    )

    # ===============================================================
    # NAME + ROLL NUMBER BOX
    # ===============================================================

    rx = width - 220
    ry = height - 62
    rw = 180
    rh = 41

    c.setLineWidth(0.7)

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

    bubble_size = 11
    bubble_gap = 3

    for i in range(4):

        c.rect(
            bx + i * (bubble_size + bubble_gap),
            ry + 6,
            bubble_size,
            bubble_size,
            fill=0,
            stroke=1
        )

    # Header divider
    c.setLineWidth(0.8)

    c.line(
        40,
        height - 72,
        width - 40,
        height - 72
    )

    # ===============================================================
    # QUESTION DATA
    # ===============================================================

    raw = payload.get(
        "questions",
        []
    )

    if isinstance(raw, str):

        try:

            raw = json.loads(raw)

        except Exception:

            raw = []

    if not isinstance(raw, list):
        raw = []

    requested_total = payload.get(
        "total_questions",
        10
    )

    try:

        requested_total = int(
            requested_total or 10
        )

    except Exception:

        requested_total = 10

    total_q = (
        len(raw)
        if raw
        else requested_total
    )

    # Current production layout supports 1-20 questions.
    total_q = max(
        1,
        min(total_q, 20)
    )

    qs = [
        q if isinstance(q, dict) else {}
        for q in raw[:total_q]
    ]

    while len(qs) < total_q:

        qs.append({})

    # ===============================================================
    # QUESTION AREA
    # ===============================================================

    q_top = height - 88
    q_bottom = strip_y + 10

    q_area = q_top - q_bottom

    # Two columns
    half = (total_q + 1) // 2
    rows = half

    col1_x = 42
    col2_x = width / 2 + 7

    col_gap = 8

    col_w = width / 2 - 49

    opt_w = (
        col_w - col_gap
    ) / 2

    row_h = q_area / rows

    # Light separators
    c.setStrokeColor(
        colors.HexColor("#D9D9D9")
    )

    c.setLineWidth(0.3)

    for separator_row in range(
        1,
        rows
    ):

        sy = (
            q_top
            - separator_row * row_h
            + 3
        )

        c.line(
            col1_x,
            sy,
            col1_x + col_w,
            sy
        )

        c.line(
            col2_x,
            sy,
            col2_x + col_w,
            sy
        )

    c.setStrokeColor(
        colors.black
    )

    # ===============================================================
    # QUESTIONS
    # ===============================================================

    for idx in range(total_q):

        q = qs[idx]

        def qv(*keys, default=""):

            for key in keys:

                value = q.get(key)

                if value not in (
                    None,
                    ""
                ):

                    return str(value)

            return default

        question_text = qv(
            "question_text",
            "question",
            default=f"प्रश्न संख्या {idx + 1}"
        )

        options = [

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

        # Determine column
        in_right_column = (
            idx >= half
        )

        row_index = (
            idx - half
            if in_right_column
            else idx
        )

        x = (
            col2_x
            if in_right_column
            else col1_x
        )

        row_top = (
            q_top
            - row_index * row_h
        )

        # ===========================================================
        # FONT SIZE
        # ===========================================================

        if total_q <= 10:

            q_size = 8.0
            option_size = 6.6

            min_q_size = 6.0
            min_option_size = 5.0

        else:

            q_size = 6.5
            option_size = 5.25

            min_q_size = 5.0
            min_option_size = 4.35

        # ===========================================================
        # AUTO FIT
        # ===========================================================

        for _ in range(20):

            q_lines = wrap_mixed(
                question_text,
                col_w - 16,
                q_size,
                True
            )

            option_lines = [

                wrap_mixed(
                    options[j],
                    opt_w - 15,
                    option_size,
                    False
                )

                for j in range(4)
            ]

            question_h = (
                len(q_lines)
                * q_size
                * 1.12
            )

            top_option_lines = max(
                len(option_lines[0]),
                len(option_lines[1])
            )

            bottom_option_lines = max(
                len(option_lines[2]),
                len(option_lines[3])
            )

            option_h = (

                top_option_lines
                * option_size
                * 1.08

                +

                bottom_option_lines
                * option_size
                * 1.08

                +

                option_size * 1.55
            )

            needed_h = (
                question_h
                + option_h
                + 5
            )

            if needed_h <= row_h - 5:
                break

            new_q_size = max(
                min_q_size,
                q_size - 0.20
            )

            new_option_size = max(
                min_option_size,
                option_size - 0.15
            )

            if (
                new_q_size == q_size
                and
                new_option_size == option_size
            ):

                break

            q_size = new_q_size
            option_size = new_option_size

        # ===========================================================
        # DRAW QUESTION
        # ===========================================================

        y = row_top - 8

        c.setFont(
            "Helvetica-Bold",
            q_size
        )

        c.drawString(
            x,
            y,
            f"{idx + 1}."
        )

        text_x = x + 13

        q_line_gap = (
            q_size * 1.12
        )

        for line_index, line in enumerate(
            q_lines
        ):

            draw_mixed_text(
                c,
                text_x,
                y - line_index * q_line_gap,
                line,
                q_size,
                True
            )

        y -= (
            len(q_lines)
            * q_line_gap
            + 1
        )

        # ===========================================================
        # DRAW OPTIONS A-D
        # ===========================================================

        option_row_gap = max(
            option_size * 1.10,
            option_size + 1.6
        ) + 1.5

        for option_index in range(4):

            option_row = (
                0
                if option_index < 2
                else 1
            )

            option_col = (
                option_index % 2
            )

            oy = (
                y
                - option_row
                * option_row_gap
            )

            ox = (
                x
                + option_col
                * (opt_w + col_gap)
            )

            label = (
                f"({chr(65 + option_index)})"
            )

            c.setFont(
                "Helvetica",
                option_size
            )

            c.drawString(
                ox,
                oy,
                label
            )

            option_text_x = (
                ox + 15
            )

            option_line_gap = (
                option_size * 1.08
            )

            for line_index, line in enumerate(
                option_lines[option_index]
            ):

                draw_mixed_text(
                    c,
                    option_text_x,
                    oy - line_index * option_line_gap,
                    line,
                    option_size,
                    False
                )

    # ===============================================================
    # BOTTOM OMR SECTION
    # ===============================================================

    c.setLineWidth(1)

    c.line(
        left_margin,
        strip_y,
        right_margin,
        strip_y
    )

    # ===============================================================
    # TEST DETAILS
    # ===============================================================

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
                value
                for value in (
                    class_name,
                    section
                )
                if value
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

    detail_y = (
        strip_y - 23
    )

    for label, value in detail_rows:

        c.setFont(
            "Helvetica-Bold",
            5.7
        )

        c.drawString(
            45,
            detail_y,
            f"{label}:"
        )

        draw_mixed_text(
            c,
            76,
            detail_y,
            value,
            5.7,
            False
        )

        detail_y -= 7.0

    # ===============================================================
    # QR CODE
    # ===============================================================

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
            "assignment_id": assignment_id,
        }
    )

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=2,
        border=2
    )

    qr.add_data(packet)

    qr.make(
        fit=True
    )

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

    # ===============================================================
    # ROLL NUMBER BUBBLES
    # ===============================================================

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

        bubble_x = (
            roll_x
            + 6
            + col * 18
        )

        for number in range(10):

            bubble_y = (
                strip_y
                - 27
                - number * 8.0
            )

            c.circle(
                bubble_x,
                bubble_y,
                3.0,
                stroke=1,
                fill=0
            )

            c.setFont(
                "Helvetica",
                4.5
            )

            c.drawCentredString(
                bubble_x,
                bubble_y - 1.5,
                str(number)
            )

    # ===============================================================
    # ANSWER STRIP
    # ===============================================================

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

    # 10 questions -> 2 columns
    # 20 questions -> 4 columns

    answer_columns = (
        2
        if total_q <= 10
        else 4
    )

    questions_per_answer_column = (
        total_q
        + answer_columns
        - 1
    ) // answer_columns

    answer_col_gap = 74

    for question_index in range(total_q):

        answer_col = (
            question_index
            // questions_per_answer_column
        )

        answer_row = (
            question_index
            % questions_per_answer_column
        )

        qx = (
            ans_x
            + answer_col
            * answer_col_gap
        )

        qy = (
            strip_y
            - 27
            - answer_row * 10.0
        )

        c.setFont(
            "Helvetica-Bold",
            6.0
        )

        c.drawString(
            qx,
            qy - 2,
            f"Q{question_index + 1:02d}"
        )

        for option_index, label in enumerate(
            "ABCD"
        ):

            bubble_x = (
                qx
                + 20
                + option_index * 11.2
            )

            c.circle(
                bubble_x,
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
                bubble_x,
                qy - 1.45,
                label
            )

    # ===============================================================
    # FINALIZE PDF
    # ===============================================================

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer.getvalue()



# =====================================================================
# 4. Storage अपलोड एवं मुख्य API
# =====================================================================
import requests
def upload_to_supabase(pdf_bytes: bytes, file_name: str) -> str:
    # 1. अगर URL में गलती से /rest/v1 या कुछ लगा हो तो उसे हटाकर केवल Base Domain रखें
    raw_url = os.getenv("SUPABASE_URL", "").strip().rstrip('/')
   
    # अगर URL में http:// या https:// के बाद अतिरिक्त पाथ है तो केवल ओरिजिन (Origin) निकालें
    if "supabase.co" in raw_url:
        # उदा: https://wcbwbradrinqeeoysjjx.supabase.co
        project_ref = raw_url.split("supabase.co")[0] + "supabase.co"
        base_url = project_ref
    else:
        base_url = raw_url

    bucket = BUCKET_NAME.strip().strip('/')
    storage_path = f"generated_omrs/{file_name}"

    # 2. सही Storage REST एंडपॉइंट
    upload_url = f"{base_url}/storage/v1/object/{bucket}/{storage_path}"

    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/pdf",
        "x-upsert": "true"
    }

    upload_res = requests.post(upload_url, data=pdf_bytes, headers=headers)
   
    if upload_res.status_code not in (200, 201):
        print(f"Supabase Upload Failed ({upload_res.status_code}): {upload_res.text}")
        raise ValueError(f"Upload failed: {upload_res.text}")

    # 3. Signed URL एंडपॉइंट
    sign_url = f"{base_url}/storage/v1/object/sign/{bucket}/{storage_path}"
    sign_headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/json"
    }
    sign_payload = {"expiresIn": 600}

    sign_res = requests.post(sign_url, json=sign_payload, headers=sign_headers)
   
    if sign_res.status_code not in (200, 201):
        print(f"Signed URL Failed ({sign_res.status_code}): {sign_res.text}")
        raise ValueError(f"Signed URL failed: {sign_res.text}")

    sign_data = sign_res.json()
    signed_url_path = sign_data.get("signedURL") or sign_data.get("signedUrl")
   
    if not signed_url_path:
        raise ValueError(f"Invalid signed URL: {sign_data}")

    if signed_url_path.startswith("http"):
        return signed_url_path
    elif signed_url_path.startswith("/storage/v1"):
        return f"{base_url}{signed_url_path}"
    else:
        return f"{base_url}/storage/v1{signed_url_path}"










@app.post("/generate-omr-pdf", dependencies=[Security(verify_api_key)])
async def generate_omr_pdf(request: Request):
    try:
        data = await request.json()
       
        # PDF जनरेट करें
        pdf_bytes = generate_hybrid_omr_pdf(data)

        # यूनीक नामकरण
        today_date = datetime.now().strftime("%d-%m-%Y")
        unique_token = str(uuid.uuid4())[:8]
        file_name = f"OMR_Exam_{today_date}_{unique_token}.pdf"

        # Supabase में अपलोड
        download_url = upload_to_supabase(pdf_bytes, file_name)

        return {
            "success": True,
            "download_url": download_url,
            "file_name": file_name
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


