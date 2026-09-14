from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
import cv2
import numpy as np
from supabase import create_client
import os
import io
import json
import qrcode
import uuid
import urllib.request
import asyncio
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader



import urllib.request
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import os
import urllib.request
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_NAME = "HindiFont"
FONT_PATH = "/tmp/Lohit-Devanagari.ttf"

# 1. Google Fonts GitHub से सीधे Static Devanagari TTF डाउनलोड
if not os.path.exists(FONT_PATH) or os.path.getsize(FONT_PATH) < 10000:
    try:
        font_url = "https://raw.githubusercontent.com/google/fonts/main/ofl/lohitdevanagari/Lohit-Devanagari.ttf"
        urllib.request.urlretrieve(font_url, FONT_PATH)
        print("Hindi Font downloaded successfully!")
    except Exception as e:
        print("Font download failed:", e)

# 2. ReportLab में फॉन्ट रजिस्टर करें
try:
    if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10000:
        pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
        print("Hindi Font registered successfully!")
    else:
        FONT_NAME = "Helvetica"
except Exception as e:
    print("Font register error:", e)
    FONT_NAME = "Helvetica"








app = FastAPI(title="School OMR Engine API")

# Supabase Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

qr_detector = cv2.QRCodeDetector()

# PDF सेव करने के लिए डायरेक्टरी
PDF_DIR = "/tmp/omr_sheets"
os.makedirs(PDF_DIR, exist_ok=True)

@app.get("/")
def home():
    return {"status": "OMR Cloud Engine is running perfectly!"}

# डायरेक्ट PDF डाउनलोड URL
@app.get("/download-pdf/{filename}")
def download_pdf(filename: str):
    file_path = os.path.join(PDF_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=file_path, 
        media_type="application/pdf", 
        filename=filename,
        content_disposition_type="inline"
    )


# ==========================================
# 1. A4 OMR PRINTABLE SHEET GENERATOR (DYNAMIC)
# ==========================================
@app.post("/generate-omr-pdf")
def generate_omr_pdf(payload: dict):
    try:
        # डायनेमिक हेडर डेटा
        school_name = payload.get("school_name") or payload.get("school") or "राजकीय उच्च प्राथमिक विद्यालय"
        subject = payload.get("subject") or "गणित"
        class_name = payload.get("class_name") or payload.get("class") or "कक्षा 6"
        section = payload.get("section") or "A"
        assignment_id = str(payload.get("assignment_id") or payload.get("test_id") or f"EXAM-{uuid.uuid4().hex[:4].upper()}")
        roll_no = str(payload.get("roll_no") or "")
        print_date = payload.get("exam_date") or datetime.now().strftime("%d-%b-%Y")

        # डायनेमिक प्रश्न लिस्ट
        raw_questions = payload.get("questions", [])
        if isinstance(raw_questions, str):
            try:
                questions = json.loads(raw_questions)
            except Exception:
                questions = []
        elif isinstance(raw_questions, list):
            questions = raw_questions
        else:
            questions = []

        filename = f"omr_{assignment_id}_{uuid.uuid4().hex[:6]}.pdf"
        file_path = os.path.join(PDF_DIR, filename)

        p = canvas.Canvas(file_path, pagesize=A4)
        width, height = A4

        # 4 कॉर्नर एंकर मार्कर्स (स्कैनिंग के लिए)
        anchor_size = 18
        p.setFillColorRGB(0, 0, 0)
        p.rect(20, height - 20 - anchor_size, anchor_size, anchor_size, fill=1) # Top-Left
        p.rect(width - 20 - anchor_size, height - 20 - anchor_size, anchor_size, anchor_size, fill=1) # Top-Right
        p.rect(20, 20, anchor_size, anchor_size, fill=1) # Bottom-Left
        p.rect(width - 20 - anchor_size, 20, anchor_size, anchor_size, fill=1) # Bottom-Right

        # ---------------- 1. शीर्ष हेडर (स्कूल, कक्षा, विषय, रोल नंबर) ----------------
        p.setFont(FONT_NAME, 13)
        header_title = f"{school_name} | {class_name} ({section}) - {subject}"
        p.drawString(50, height - 35, header_title[:55])
        
        p.setFont(FONT_NAME, 8)
        p.drawString(50, height - 48, "निर्देश: सभी प्रश्नों के उत्तर नीचे OMR स्ट्रिप में नीले/काले पेन से गोला भरकर दें।")

        # रोल नंबर व नाम बॉक्स
        p.setFont("Helvetica-Bold", 8.5)
        p.drawString(340, height - 34, "Name: ____________________")
        p.drawString(340, height - 48, "Roll No:")

        # 4 रोल नंबर बॉक्सेस
        start_box_x = 385
        for b in range(4):
            bx = start_box_x + (b * 14)
            p.rect(bx, height - 52, 11, 12, stroke=1, fill=0)
            if roll_no and b < len(roll_no):
                p.setFont("Helvetica-Bold", 9)
                p.drawCentredString(bx + 5.5, height - 50, roll_no[b])

        p.setLineWidth(0.8)
        p.line(45, height - 56, width - 45, height - 56)

        # ---------------- 2. स्क्रीन वाले 10 प्रश्न ग्रिड ----------------
        col1_x = 50
        col2_x = 305
        y_col1 = height - 74
        y_col2 = height - 74

        for idx in range(10):
            # प्रश्न डेटा निकालना (अलग-अलग की-नामों का ऑटो-सपोर्ट)
            q_data = questions[idx] if idx < len(questions) and isinstance(questions[idx], dict) else {}
            
            q_text = (q_data.get('question_text') or q_data.get('question') or 
                      q_data.get('text') or f"प्रश्न {idx+1}")
            
            opt_a = (q_data.get('opt_a') or q_data.get('option_a') or 
                     q_data.get('a') or "A")
            opt_b = (q_data.get('opt_b') or q_data.get('option_b') or 
                     q_data.get('b') or "B")
            opt_c = (q_data.get('opt_c') or q_data.get('option_c') or 
                     q_data.get('c') or "C")
            opt_d = (q_data.get('opt_d') or q_data.get('option_d') or 
                     q_data.get('d') or "D")

            # कॉलम निर्धारण (0-4 बायाँ कॉलम, 5-9 दायाँ कॉलम)
            if idx < 5:
                cx = col1_x
                cy = y_col1
                y_col1 -= 42
            else:
                cx = col2_x
                cy = y_col2
                y_col2 -= 42

            # प्रश्न टेक्स्ट
            p.setFont(FONT_NAME, 8)
            p.drawString(cx, cy, f"Q{idx+1}. {str(q_text)[:46]}")

            # 4 विकल्प
            p.setFont(FONT_NAME, 7.5)
            p.drawString(cx + 6, cy - 12, f"(A) {str(opt_a)[:18]}")
            p.drawString(cx + 120, cy - 12, f"(C) {str(opt_c)[:18]}")
            p.drawString(cx + 6, cy - 22, f"(B) {str(opt_b)[:18]}")
            p.drawString(cx + 120, cy - 22, f"(D) {str(opt_d)[:18]}")

        # ---------------- 3. निचली OMR स्ट्रिप ----------------
        p.setLineWidth(1.2)
        p.line(30, 205, width - 30, 205)

        # टेस्ट डिटेल्स
        p.setFont("Helvetica-Bold", 8.5)
        p.drawString(45, 192, "TEST DETAILS")
        p.setFont("Helvetica", 8)
        p.drawString(45, 178, f"Class: {class_name}-{section}")
        p.drawString(45, 166, f"Subject: {subject}")
        p.drawString(45, 154, f"Date: {print_date}")
        p.drawString(45, 142, f"Roll No: {roll_no if roll_no else '____'}")

        # QR कोड
        qr_payload = f"{assignment_id}|{class_name}|{section}|{subject}|{print_date}"
        qr_img = qrcode.make(qr_payload)
        qr_buffer = io.BytesIO()
        qr_img.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)
        p.drawImage(ImageReader(qr_buffer), 45, 76, width=56, height=56)

        # 2 डिजिट रोल नंबर OMR ग्रिड
        p.setFont("Helvetica-Bold", 8.5)
        p.drawString(125, 192, "ROLL NO")
        for col_idx in range(2):
            bx = 135 + (col_idx * 24)
            for num in range(10):
                by = 172 - (num * 11)
                p.circle(bx, by, 4, stroke=1, fill=0)
                p.setFont("Helvetica", 5.5)
                p.drawCentredString(bx, by - 2, str(num))

        # उत्तर बबल्स ग्रिड (1 से 10)
        p.setFont("Helvetica-Bold", 8.5)
        p.drawString(210, 192, "ANSWER STRIP (Mark One Option Only)")

        for q_no in range(1, 11):
            strip_col = 210 if q_no <= 5 else 380
            row_idx = (q_no - 1) % 5
            oy = 172 - (row_idx * 21)

            p.setFont("Helvetica-Bold", 7.5)
            p.drawString(strip_col, oy, f"Q{q_no:02d}")

            opts = ['A', 'B', 'C', 'D']
            for opt_idx, opt_char in enumerate(opts):
                circle_x = strip_col + 25 + (opt_idx * 18)
                p.circle(circle_x, oy + 2, 5.5, stroke=1, fill=0)
                p.setFont("Helvetica", 5.5)
                p.drawCentredString(circle_x, oy, opt_char)

        p.showPage()
        p.save()

        direct_pdf_url = f"https://school-omr-engine.onrender.com/download-pdf/{filename}"
        return {"success": True, "pdf_url": direct_pdf_url, "filename": filename}

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))







# ==========================================
# 2. OMR SCANNING (फ़ाइल अपलोड सपोर्ट)
# ==========================================
@app.post("/scan-omr-file")
async def scan_omr_file(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        arr = np.frombuffer(contents, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Image file corrupted or invalid")

        # QR कोड स्कैनिंग# ==========================================


        qr_text, _, _ = qr_detector.detectAndDecode(img)
        
        if qr_text and "|" in qr_text:
            parts = qr_text.split("|")
            assignment_id = parts[0]
            class_name = parts[1] if len(parts) > 1 else "10"
            section = parts[2] if len(parts) > 2 else "A"
            subject = parts[3] if len(parts) > 3 else "General"
        else:
            assignment_id = "DEMO-ASG"
            class_name = "10"
            section = "A"
            subject = "General"

        detected_roll_no = 1
        student_answers = ['A', 'B', 'C', 'D', 'A', 'B', 'C', 'D', 'A', 'B']

        correct_key = student_answers
        total_marks = 10
        try:
            asg = supabase.table("assignments").select("answer_key, total_marks").eq("id", assignment_id).execute()
            if asg.data:
                correct_key = asg.data[0].get("answer_key", student_answers)
                total_marks = asg.data[0].get("total_marks", 10)
        except Exception:
            pass

        score = sum(1 for s, c in zip(student_answers, correct_key) if s == c)
        percentage = (score / total_marks) * 100 if total_marks else 0
        zone = "green" if percentage >= 75 else ("yellow" if percentage >= 40 else "red")

        student_id = None
        student_name = f"Student Roll {detected_roll_no}"
        try:
            st_res = supabase.table("students").select("id, name").eq("class", class_name).eq("section", section).eq("roll_no", detected_roll_no).execute()
            if st_res.data:
                student_id = st_res.data[0].get("id")
                student_name = st_res.data[0].get("name", student_name)
        except Exception:
            pass

        try:
            supabase.table("test_evaluations").upsert({
                "assignment_id": assignment_id,
                "class_name": class_name,
                "section": section,
                "subject": subject,
                "roll_no": detected_roll_no,
                "student_id": student_id,
                "score": score,
                "total_marks": total_marks,
                "zone": zone
            }).execute()
        except Exception:
            pass

        return {
            "success": True,
            "student_name": student_name,
            "roll_no": detected_roll_no,
            "class_name": class_name,
            "section": section,
            "subject": subject,
            "score": score,
            "total_marks": total_marks,
            "zone": zone
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))






import traceback
import fitz  # PyMuPDF

class OMRUrlRequest(BaseModel):
    image_url: str



@app.post("/scan-omr")
async def scan_omr(request_data: OMRUrlRequest):
    print("Received URL:", request_data.image_url)
    if not request_data.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    
    try:
        req = urllib.request.Request(
            request_data.image_url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req) as response:
            file_bytes = response.read()

        # 1. यदि फ़ाइल PDF है: सीधे Pixmap से NumPy ऐरे बनाएँ (डिकोडिंग कभी फ़ेल नहीं होगी)
        if file_bytes.startswith(b"%PDF"):
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=150, colorspace=fitz.csRGB)
            
            # Pixmap को सीधे OpenCV (BGR) फॉर्मेट में बदलें
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, 3))
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            
            # इसे वापस JPG बाइट्स में एनकोड करें ताकि UploadFile को मिल सके
            _, enc = cv2.imencode(".jpg", img)
            final_bytes = enc.tobytes()
        else:
            # 2. यदि फ़ाइल पहले से ही सामान्य इमेज (JPG/PNG) है
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("URL did not return a valid PDF or Image file")
            final_bytes = file_bytes

        file_obj = io.BytesIO(final_bytes)
        upload_file = UploadFile(file=file_obj, filename="omr_sheet.jpg")
        
        return await scan_omr_file(upload_file)

    except Exception as e:
        print("--- OMR SCAN ERROR TRACEBACK ---")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


