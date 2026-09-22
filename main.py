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

    pdfmetrics.registerFont(TTFont(FONT_NAME, font_path_reg))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, font_path_bld))

try:
    setup_fonts()
    print("Devanagari Fonts Loaded Successfully")
except Exception as e:
    print(f"Font Setup Error: {e}")

# ------------------ 2. HINDI TEXT REORDERING FIXER ------------------
def fix_hindi_text(text: str) -> str:
    """
    ReportLab drawString के लिए 'ि' की मात्रा (\u093F) को व्यंजन से पहले शिफ्ट करता है
    ताकि मात्रा अक्षर के ऊपर/पहले सही रूप से दिखे और टूटे नहीं।
    """
    if not text:
        return ""
    text = str(text)
    pattern = r'((?:[\u0915-\u0939]\u094D)*[\u0915-\u0939])(\u093F)'
    return re.sub(pattern, r'\2\1', text)








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
    # ------------------ MIDDLE SECTION (Questions & Options) ------------------
    for idx in range(total_q):
        q_data = raw_questions[idx] if idx < len(raw_questions) else {}
        q_text = str(q_data.get("question_text") or q_data.get("question") or f"प्रश्न संख्या {idx + 1}")
        opt_a = str(q_data.get("opt_a") or q_data.get("option_a") or "विकल्प A")
        opt_b = str(q_data.get("opt_b") or q_data.get("option_b") or "विकल्प B")
        opt_c = str(q_data.get("opt_c") or q_data.get("option_c") or "विकल्प C")
        opt_d = str(q_data.get("opt_d") or q_data.get("option_d") or "विकल्प D")

        is_col2 = idx >= half_q
        cur_x = col2_x if is_col2 else col1_x
        row_num = idx - half_q if is_col2 else idx
        cur_y = y_start - (row_num * line_spacing)

        # 1. प्रश्न को fix_hindi_text में पास करें
        c.setFont(FONT_BOLD, q_font_size)
        display_q = fix_hindi_text(f"{idx + 1}. {q_text[:50]}")
        c.drawString(cur_x, cur_y, display_q)

        # 2. सभी चारों विकल्पों को fix_hindi_text में पास करें
        c.setFont(FONT_NAME, opt_font_size)
        c.drawString(cur_x + 8, cur_y - 9, fix_hindi_text(f"(A) {opt_a[:18]}"))
        c.drawString(cur_x + (col_width / 2), cur_y - 9, fix_hindi_text(f"(B) {opt_b[:18]}"))
        c.drawString(cur_x + 8, cur_y - 18, fix_hindi_text(f"(C) {opt_c[:18]}"))
        c.drawString(cur_x + (col_width / 2), cur_y - 18, fix_hindi_text(f"(D) {opt_d[:18]}"))


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

