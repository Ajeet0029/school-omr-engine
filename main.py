import io
import os
import uuid
import traceback
import json
from datetime import datetime
from typing import List, Optional, Any, Dict

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client

# ReportLab इम्पोर्ट्स (A4 OMR लेआउट के लिए)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas

app = FastAPI(title="School OMR Engine - Production Ready", version="2.0.0")

# =====================================================================
# 1. सुरक्षा एवं पर्यावरण सेटिंग्स (Environment & Security)
# =====================================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "your-service-role-key")

# यह सीक्रेट की FlutterFlow के Headers में "x-api-key" के रूप में भेजी जाएगी
API_SECRET_KEY = os.getenv("APP_API_SECRET_KEY", "MySecureSchoolOmrKey_2026_Secure")
API_KEY_NAME = "x-api-key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "omr-sheets")

# Supabase क्लाइंट
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# CORS सेटिंग्स
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # API Key सुरक्षा होने से यह सुरक्षित है
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)


async def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key or api_key != API_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="अनधिकृत पहुँच (Unauthorized): x-api-key अमान्य या अनुपस्थित है।"
        )
    return api_key


# =====================================================================
# 2. डेटा मॉडल (Payload Validation)
# =====================================================================
class OMRRequest(BaseModel):
    school_name: Optional[str] = "PUBLIC SCHOOL"
    subject: Optional[str] = "General Subject"
    class_name: Optional[str] = "Class 10"
    section: Optional[str] = "A"
    assignment_id: Optional[str] = "TEST-01"
    total_questions: Optional[int] = 50
    questions: Optional[Any] = []


# =====================================================================
# 3. प्रोफेशनल A4 OMR लेआउट जनरेटर (ReportLab)
# =====================================================================
def generate_omr_pdf_bytes(payload: OMRRequest) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4  # A4 = 595.27 x 841.89 points

    # --- A. कंप्यूटर विज़न / स्कैनर फ़िडूशियल मार्कर्स (4 Corners) ---
    marker_size = 18
    c.setFillColor(colors.black)
    c.rect(20, height - 20 - marker_size, marker_size, marker_size, fill=1, stroke=0)
    c.rect(width - 20 - marker_size, height - 20 - marker_size, marker_size, marker_size, fill=1, stroke=0)
    c.rect(20, 20, marker_size, marker_size, fill=1, stroke=0)
    c.rect(width - 20 - marker_size, 20, marker_size, marker_size, fill=1, stroke=0)

    # --- B. हेडर सेक्शन (Header Section) ---
    c.setFont("Helvetica-Bold", 16)
    school_title = (payload.school_name or "PUBLIC SCHOOL").strip().upper()
    c.drawCentredString(width / 2.0, height - 42, school_title)

    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(width / 2.0, height - 58, "OMR EVALUATION & ANSWER SHEET")

    # हेडर बॉक्स (परीक्षा विवरण)
    top_box_y = height - 105
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.rect(45, top_box_y, width - 90, 40, fill=0)

    c.setFont("Helvetica", 9)
    today_str = datetime.now().strftime("%d-%m-%Y")
    
    # पंक्ति 1
    c.drawString(55, top_box_y + 26, f"Subject: {payload.subject}")
    c.drawString(240, top_box_y + 26, f"Class: {payload.class_name} (Sec: {payload.section})")
    c.drawString(420, top_box_y + 26, f"Date: {today_str}")

    # पंक्ति 2
    c.drawString(55, top_box_y + 10, f"Assignment/Test ID: {payload.assignment_id}")
    c.drawString(240, top_box_y + 10, f"Total Questions: {payload.total_questions}")
    c.drawString(420, top_box_y + 10, "Max Marks: 100")

    # --- C. छात्र विवरण और निर्देश बॉक्स (Student Info & Instructions) ---
    mid_box_y = top_box_y - 52
    
    # बायाँ बॉक्स: छात्र का नाम एवं रोल नंबर
    c.rect(45, mid_box_y, 250, 46, fill=0)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(50, mid_box_y + 34, "STUDENT NAME:")
    c.line(130, mid_box_y + 32, 285, mid_box_y + 32)
    c.drawString(50, mid_box_y + 14, "ROLL NUMBER:")
    c.line(130, mid_box_y + 12, 285, mid_box_y + 12)

    # दायाँ बॉक्स: ओएमआर भरने के निर्देश
    c.rect(305, mid_box_y, width - 350, 46, fill=0)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(312, mid_box_y + 34, "INSTRUCTIONS / निर्देश:")
    c.setFont("Helvetica", 7)
    c.drawString(312, mid_box_y + 22, "• Use Blue or Black Ballpoint pen only.")
    c.drawString(312, mid_box_y + 10, "• Darken the circle completely. [ Correct: (●) | Wrong: (✓) (✗) (◐) ]")

    # --- D. मुख्य ओएमआर ग्रिड (OMR Questions Grid) ---
    total_q = max(1, min(payload.total_questions or 50, 100))
    
    # कॉलम विभाजन (50 तक 2 कॉलम, 50 से अधिक पर 3 या 4 कॉलम)
    num_cols = 2 if total_q <= 50 else (3 if total_q <= 75 else 4)
    q_per_col = (total_q + num_cols - 1) // num_cols

    grid_top_y = mid_box_y - 18
    col_width = (width - 90) / num_cols
    row_height = 14.5  # प्रत्येक प्रश्न की रो का साइज़
    radius = 4.0        # बबल्स का आकार
    options = ["A", "B", "C", "D"]

    c.setLineWidth(0.7)

    for q_idx in range(total_q):
        col_idx = q_idx // q_per_col
        row_idx = q_idx % q_per_col

        x_base = 45 + (col_idx * col_width)
        y_pos = grid_top_y - (row_idx * row_height)

        # 5 के गुणक पर हल्की लाइन (आँखों के आराम व ट्रैकिंग के लिए)
        if (q_idx + 1) % 5 == 0 and row_idx != q_per_col - 1:
            c.setStrokeColor(colors.HexColor("#E0E0E0"))
            c.setLineWidth(0.4)
            c.line(x_base, y_pos - 3, x_base + col_width - 8, y_pos - 3)
            c.setStrokeColor(colors.black)
            c.setLineWidth(0.7)

        # प्रश्न संख्या (Q. No)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawRightString(x_base + 24, y_pos - 2, f"{q_idx + 1:02d}.")

        # विकल्प बबल्स: A, B, C, D
        for opt_idx, opt_char in enumerate(options):
            bx = x_base + 38 + (opt_idx * 16)
            by = y_pos

            # बबल सर्कल
            c.circle(bx, by, radius, stroke=1, fill=0)
            
            # बबल के अंदर टेक्स्ट
            c.setFont("Helvetica", 5.5)
            c.drawCentredString(bx, by - 2, opt_char)

    # --- E. निचला भाग: हस्ताक्षर बॉक्स (Signatures Footer) ---
    footer_y = 52
    c.setStrokeColor(colors.HexColor("#333333"))
    c.line(55, footer_y, 200, footer_y)
    c.line(width - 200, footer_y, width - 55, footer_y)

    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(127, footer_y - 12, "Candidate's Signature")
    c.drawCentredString(width - 127, footer_y - 12, "Invigilator's Signature")

    c.setFont("Helvetica", 6.5)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawCentredString(width / 2.0, 32, "Computer Scannable OMR Sheet • Do Not Fold or Mutilate")

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.getvalue()


# =====================================================================
# 4. Supabase Storage अपलोड और Signed URL निर्माण
# =====================================================================
def upload_pdf_and_get_signed_url(pdf_bytes: bytes, file_name: str) -> str:
    storage_path = f"generated_omrs/{file_name}"

    # Supabase में फ़ाइल अपलोड
    supabase.storage.from_(BUCKET_NAME).upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf"}
    )

    # 10 मिनट (600 सेकंड) के लिए मान्य सुरक्षित Signed Download URL
    res = supabase.storage.from_(BUCKET_NAME).create_signed_url(
        path=storage_path,
        expires_in=600
    )

    if isinstance(res, dict):
        url = res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")
    else:
        url = getattr(res, "signed_url", None) or str(res)

    if not url:
        raise ValueError("Supabase से Signed URL प्राप्त नहीं हो सका।")

    return url


# =====================================================================
# 5. मुख्य सुरक्षित API एंडपॉइंट
# =====================================================================
@app.post(
    "/generate-omr-pdf",
    dependencies=[Security(verify_api_key)],
    summary="Generate Scalable & Secure OMR PDF"
)
async def generate_omr_pdf(request: Request):
    try:
       raw_json = await request.json()

        # अगर questions स्ट्रिंग में आया है तो उसे सही लिस्ट में बदलें
        if isinstance(raw_json.get("questions"), str):
            try:
                raw_json["questions"] = json.loads(raw_json["questions"])
            except Exception:
                raw_json["questions"] = []

        payload = OMRRequest(**raw_json) 

        # 1. A4 OMR PDF बाइट्स निर्माण
        pdf_bytes = generate_omr_pdf_bytes(payload)

        # 2. सुरक्षित व विशिष्ट फ़ाइल नाम
        cls_sanitized = str(payload.class_name).replace(" ", "_")
        sec_sanitized = str(payload.section).replace(" ", "_")
        date_str = datetime.now().strftime("%d-%m-%Y")
        token = str(uuid.uuid4())[:8]

        file_name = f"OMR_{cls_sanitized}_{sec_sanitized}_{date_str}_{token}.pdf"

        # 3. Supabase Storage में अपलोड व Signed URL निर्माण
        download_url = upload_pdf_and_get_signed_url(pdf_bytes, file_name)

        return {
            "success": True,
            "download_url": download_url,
            "file_name": file_name
        }

    except Exception as e:
        print(f"[FATAL] Error in generate_omr_pdf: {str(e)}")
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"OMR जनरेट करने में त्रुटि: {str(e)}"
        )


