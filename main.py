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

app = FastAPI(title="School OMR & Question Engine")

# =====================================================================
# 1. हिंदी/देवनागरी फॉन्ट सेटअप (Auto Font Setup)
# =====================================================================
FONT_NAME = "NotoSansHindi"
FONT_BOLD = "NotoSansHindi-Bold"

def setup_fonts():
    # हिंदी सपोर्ट के लिए Noto Sans Devanagari फॉन्ट लोड करें
    font_dir = "/tmp/fonts"
    os.makedirs(font_dir, exist_ok=True)
    
    font_path_regular = os.path.join(font_dir, "NotoSansDevanagari-Regular.ttf")
    font_path_bold = os.path.join(font_dir, "NotoSansDevanagari-Bold.ttf")
    
    # अगर फॉन्ट मौजूद नहीं हैं, तो डाउनलोड करें
    if not os.path.exists(font_path_regular):
        url_reg = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Regular.ttf"
        urllib.request.urlretrieve(url_reg, font_path_regular)
        
    if not os.path.exists(font_path_bold):
        url_bld = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf"
        urllib.request.urlretrieve(url_bld, font_path_bold)

    pdfmetrics.registerFont(TTFont(FONT_NAME, font_path_regular))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, font_path_bold))

try:
    setup_fonts()
except Exception as e:
    print(f"Font download fallback to Helvetica: {e}")
    FONT_NAME = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"


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
def generate_hybrid_omr_pdf(payload: dict) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4  # 595 x 842

    # कोनों पर काले स्कैनर मार्कर्स (4 Corners)
    m_size = 14
    c.setFillColor(colors.black)
    c.rect(18, height - 18 - m_size, m_size, m_size, fill=1, stroke=0)
    c.rect(width - 18 - m_size, height - 18 - m_size, m_size, m_size, fill=1, stroke=0)
    c.rect(18, 18, m_size, m_size, fill=1, stroke=0)
    c.rect(width - 18 - m_size, 18, m_size, m_size, fill=1, stroke=0)

    # ------------------ TOP SECTION (Header) ------------------
    # स्कूल / टेस्ट हेडर
    school_name = payload.get("school_name", "SCHOOL ASSESSMENT TEST")
    c.setFont(FONT_BOLD, 12)
    c.drawString(45, height - 40, school_name.upper())

    c.setFont(FONT_NAME, 8)
    c.drawString(45, height - 52, "निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई ओएमआर पट्टी में नीले/काले पेन से गोला भरकर दें।")

    # छात्र का नाम और रोल नंबर बॉक्स (दाएँ कोने पर)
    c.rect(width - 220, height - 60, 180, 32, fill=0)
    c.setFont(FONT_BOLD, 7.5)
    c.drawString(width - 215, height - 42, "Name:")
    c.drawString(width - 215, height - 54, "Roll No:")
    # रोल नंबर के छोटे डिब्बे
    box_start_x = width - 170
    for b in range(4):
        c.rect(box_start_x + (b * 12), height - 56, 10, 10, fill=0)

    # विभाजक रेखा
    c.setLineWidth(0.8)
    c.line(40, height - 68, width - 40, height - 68)

    # ------------------ MIDDLE SECTION (Questions) ------------------
    raw_questions = payload.get("questions", [])
    if isinstance(raw_questions, str):
        try:
            raw_questions = json.loads(raw_questions)
        except Exception:
            raw_questions = []

    total_q = len(raw_questions) if raw_questions else int(payload.get("total_questions", 10) or 10)
    
    # दो कॉलम में प्रश्न (बायाँ और दायाँ)
    col1_x = 42
    col2_x = (width / 2.0) + 10
    col_width = (width / 2.0) - 52
    
    # 10 प्रश्न होने पर 5-5 दोनों कॉलम में; 20 होने पर 10-10
    half_q = (total_q + 1) // 2
    y_start = height - 85
    line_spacing = 42 if total_q <= 10 else 24  # प्रश्नों की संख्या के अनुसार स्पेसिंग

    for idx in range(total_q):
        q_data = raw_questions[idx] if idx < len(raw_questions) else {}
        q_text = q_data.get("question_text") or q_data.get("question") or f"प्रश्न संख्या {idx + 1}"
        opt_a = q_data.get("opt_a") or q_data.get("option_a") or "विकल्प A"
        opt_b = q_data.get("opt_b") or q_data.get("option_b") or "विकल्प B"
        opt_c = q_data.get("opt_c") or q_data.get("option_c") or "विकल्प C"
        opt_d = q_data.get("opt_d") or q_data.get("option_d") or "विकल्प D"

        is_col2 = idx >= half_q
        cur_x = col2_x if is_col2 else col1_x
        row_num = idx - half_q if is_col2 else idx
        cur_y = y_start - (row_num * line_spacing)

        # प्रश्न
        c.setFont(FONT_BOLD, 7.5)
        # लंबा प्रश्न ट्रंकेट न हो, इसके लिए पहली 50 अक्षर
        display_q = f"{idx + 1}. {q_text[:55]}"
        c.drawString(cur_x, cur_y, display_q)

        # विकल्प A, B, C, D
        c.setFont(FONT_NAME, 6.8)
        c.drawString(cur_x + 8, cur_y - 10, f"(A) {str(opt_a)[:18]}")
        c.drawString(cur_x + (col_width / 2), cur_y - 10, f"(B) {str(opt_b)[:18]}")
        c.drawString(cur_x + 8, cur_y - 20, f"(C) {str(opt_c)[:18]}")
        c.drawString(cur_x + (col_width / 2), cur_y - 20, f"(D) {str(opt_d)[:18]}")

    # ------------------ BOTTOM SECTION (OMR Answer Strip) ------------------
    strip_y = 155
    c.setLineWidth(1)
    c.line(40, strip_y, width - 40, strip_y)  # ऊपर की बॉर्डर

    # 1. टेस्ट डिटेल्स व QR कोड
    c.setFont(FONT_BOLD, 7.5)
    c.drawString(45, strip_y - 15, "TEST DETAILS")
    c.setFont(FONT_NAME, 6.8)
    c.drawString(45, strip_y - 27, f"Class: {payload.get('class_name', '')} {payload.get('section', '')}")
    c.drawString(45, strip_y - 37, f"Subject: {payload.get('subject', '')}")
    c.drawString(45, strip_y - 47, f"Date: {datetime.now().strftime('%d-%b-%Y')}")
    c.drawString(45, strip_y - 57, f"ID: {payload.get('assignment_id', 'T01')}")

    # QR कोड जनरेशन (सीधे PIL Image पास करें)
    qr = qrcode.QRCode(box_size=2, border=0)
    qr_data = f"ID:{payload.get('assignment_id')}|CLS:{payload.get('class_name')}"
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
    
    # सीधे इमेज ऑब्जेक्ट ड्रा करें
    c.drawInlineImage(qr_img, 45, strip_y - 105, 42, 42)


    # 2. रोल नंबर बबल ग्रिड
    roll_x = 135
    c.setFont(FONT_BOLD, 7.5)
    c.drawString(roll_x, strip_y - 15, "ROLL NO")
    
    # 2 डिजिट रोल नंबर बबल्स
    for col_r in range(2):
        bx = roll_x + 5 + (col_r * 15)
        for num in range(10):
            by = strip_y - 30 - (num * 8.5)
            c.circle(bx, by, 3.2, stroke=1, fill=0)
            c.setFont(FONT_NAME, 5)
            c.drawCentredString(bx, by - 1.8, str(num))

    # 3. आंसर स्ट्रिप बबल्स (ANSWER STRIP)
    ans_x = 210
    c.setFont(FONT_BOLD, 8)
    c.drawString(ans_x, strip_y - 15, "ANSWER STRIP (Mark One Option Only)")

    # बबल्स ग्रिड (10 प्रश्न = 2 कॉलम; 20 प्रश्न = 4 कॉलम)
    ans_cols = 2 if total_q <= 10 else 4
    q_per_ans_col = (total_q + ans_cols - 1) // ans_cols
    col_gap = 75
    opt_labels = ["A", "B", "C", "D"]

    for q_i in range(total_q):
        c_i = q_i // q_per_ans_col
        r_i = q_i % q_per_ans_col

        q_base_x = ans_x + (c_i * col_gap)
        q_base_y = strip_y - 30 - (r_i * 10)

        c.setFont(FONT_BOLD, 6.5)
        c.drawString(q_base_x, q_base_y - 2, f"Q{q_i + 1:02d}")

        for o_i, o_label in enumerate(opt_labels):
            bx = q_base_x + 22 + (o_i * 12)
            by = q_base_y
            c.circle(bx, by, 3.4, stroke=1, fill=0)
            c.setFont(FONT_NAME, 4.8)
            c.drawCentredString(bx, by - 1.5, o_label)

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.getvalue()


# =====================================================================
# 4. Storage अपलोड एवं मुख्य API
# =====================================================================
def upload_to_supabase(pdf_bytes: bytes, file_name: str) -> str:
    storage_path = f"generated_omrs/{file_name}"
    
    supabase.storage.from_(BUCKET_NAME).upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf"}
    )
    
    res = supabase.storage.from_(BUCKET_NAME).create_signed_url(
        path=storage_path,
        expires_in=600
    )
    
    if isinstance(res, dict):
        return res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")
    return getattr(res, "signed_url", str(res))


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

