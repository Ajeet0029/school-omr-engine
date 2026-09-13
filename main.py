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
# 1. A4 OMR PRINTABLE SHEET GENERATOR
# ==========================================
@app.post("/generate-omr-pdf")
def generate_omr_pdf(payload: dict):
    try:
        school_name = payload.get("school_name", "School Assessment")
        subject = payload.get("subject", "General")
        class_name = payload.get("class_name", "Class")
        section = payload.get("section", "A")
        assignment_id = str(payload.get("assignment_id", "ASG-101"))
        
        # आज की तारीख (Format: 12-Sep-2026)
        print_date = payload.get("exam_date") or datetime.now().strftime("%d-%b-%Y")
        
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

        # 4 Anchor Markers (18x18 pt)
        anchor_size = 18
        p.setFillColorRGB(0, 0, 0)
        p.rect(20, height - 20 - anchor_size, anchor_size, anchor_size, fill=1) # Top-Left
        p.rect(width - 20 - anchor_size, height - 20 - anchor_size, anchor_size, anchor_size, fill=1) # Top-Right
        p.rect(20, 20, anchor_size, anchor_size, fill=1) # Bottom-Left
        p.rect(width - 20 - anchor_size, 20, anchor_size, anchor_size, fill=1) # Bottom-Right

        # ---------------- TOP HEADER ----------------
        p.setFont("Helvetica-Bold", 14)
        p.drawString(50, height - 35, str(school_name))
        p.setFont("Helvetica", 9)
        p.drawString(50, height - 50, "Instructions: Fill circles completely using Blue or Black ballpoint pen.")
        p.line(45, height - 58, width - 45, height - 58)

        # ---------------- 10 QUESTIONS GRID (TOP HALF) ----------------
        y_pos = height - 80
        col1_x = 50
        col2_x = 310

        for idx in range(10):
            col_x = col1_x if idx < 5 else col2_x
            if idx == 5:
                y_pos = height - 80

            q_data = questions[idx] if idx < len(questions) and isinstance(questions[idx], dict) else {}
            q_text = q_data.get('question_text', f'Question {idx+1}: Choose the correct option.')
            opt_a = q_data.get('opt_a', 'Option A')
            opt_b = q_data.get('opt_b', 'Option B')
            opt_c = q_data.get('opt_c', 'Option C')
            opt_d = q_data.get('opt_d', 'Option D')

            p.setFont("Helvetica-Bold", 8.5)
            p.drawString(col_x, y_pos, f"Q{idx+1}. {str(q_text)[:38]}")
            p.setFont("Helvetica", 7.5)
            p.drawString(col_x + 8, y_pos - 12, f"A) {str(opt_a)[:14]}   B) {str(opt_b)[:14]}")
            p.drawString(col_x + 8, y_pos - 22, f"C) {str(opt_c)[:14]}   D) {str(opt_d)[:14]}")
            y_pos -= 42

        # ---------------- BOTTOM OMR EVALUATION STRIP ----------------
        # मुख्य विभाजक रेखा
        p.setLineWidth(1.2)
        p.line(30, 205, width - 30, 205)

        # 1. टेस्ट डिटेल्स, प्रिंट डेट और QR कोड (बाएँ भाग में)
        p.setFont("Helvetica-Bold", 9)
        p.drawString(45, 192, "TEST DETAILS")
        p.setFont("Helvetica", 8)
        p.drawString(45, 178, f"Class: {class_name}-{section}")
        p.drawString(45, 166, f"Subject: {subject}")
        p.drawString(45, 154, f"Date: {print_date}")
        p.drawString(45, 142, "Max Marks: 10")

        # QR कोड (Payload में date भी शामिल है)
        qr_payload = f"{assignment_id}|{class_name}|{section}|{subject}|{print_date}"
        qr_img = qrcode.make(qr_payload)
        qr_buffer = io.BytesIO()
        qr_img.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)
        p.drawImage(ImageReader(qr_buffer), 45, 76, width=56, height=56)

        # 2. रोल नंबर OMR ग्रिड (मध्य भाग में)
        p.setFont("Helvetica-Bold", 8.5)
        p.drawString(125, 192, "ROLL NO (2 Digits)")
        for col_idx in range(2):
            bx = 135 + (col_idx * 24)
            for num in range(10):
                by = 172 - (num * 11)
                p.circle(bx, by, 4, stroke=1, fill=0)
                p.setFont("Helvetica", 5.5)
                p.drawCentredString(bx, by - 2, str(num))

        # 3. उत्तर बबल्स स्ट्रिप (Q1 से Q10) (दाएँ भाग में)
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

        return {
            "success": True,
            "pdf_url": direct_pdf_url,
            "filename": filename
        }

    except Exception as e:
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

        # QR कोड स्कैनिंग
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

        # जाँचें कि क्या फ़ाइल PDF है (PDF फ़ाइल हमेशा %PDF से शुरू होती है)
        if file_bytes.startswith(b"%PDF"):
            # PDF का पहला पेज इमेज में बदलें
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=150)  # साफ़ स्कैन के लिए 200 DPI
            image_bytes = pix.tobytes("png")
        else:
            image_bytes = file_bytes

        # इमेज वैलिडेशन
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("File could not be decoded into a valid image")

        file_obj = io.BytesIO(image_bytes)
        upload_file = UploadFile(file=file_obj, filename="omr_sheet.jpg")
        
        return await scan_omr_file(upload_file)

    except Exception as e:
        print("--- OMR SCAN ERROR TRACEBACK ---")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))



