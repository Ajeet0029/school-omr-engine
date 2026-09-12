from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import urllib.request
import cv2
import numpy as np
from supabase import create_client
import os
import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing

app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

qr_detector = cv2.QRCodeDetector()

class ScanRequest(BaseModel):
    image_url: str

class PDFRequest(BaseModel):
    school_name: str
    subject: str
    class_name: str
    section: str
    assignment_id: str
    questions: list

@app.get("/")
def home():
    return {"status": "OMR Cloud Engine is running!"}

# 1. A4 OMR शीट जनरेटर एंडपॉइंट
@app.post("/generate-omr-pdf")
def generate_omr_pdf(data: PDFRequest):
    try:
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        # चारों कोनों पर एंकर बॉक्स (18x18 pt)
        anchor_size = 18
        p.setFillColorRGB(0, 0, 0)
        p.rect(20, height - 20 - anchor_size, anchor_size, anchor_size, fill=1)
        p.rect(width - 20 - anchor_size, height - 20 - anchor_size, anchor_size, anchor_size, fill=1)
        p.rect(20, 20, anchor_size, anchor_size, fill=1)
        p.rect(width - 20 - anchor_size, 20, anchor_size, anchor_size, fill=1)

        # हेडर
        p.setFont("Helvetica-Bold", 13)
        p.drawString(50, height - 35, data.school_name)
        p.setFont("Helvetica", 9)
        p.drawString(50, height - 50, f"Class: {data.class_name}-{data.section}  |  Subject: {data.subject}  |  Max Marks: 10")

        # मास्टर QR कोड (Top Right)
        qr_payload = f"{data.assignment_id}|{data.class_name}|{data.section}|{data.subject}"
        qr_code = qr.QrCodeWidget(qr_payload)
        d = Drawing(45, 45)
        d.add(qr_code)
        qr.renderPDF.draw(d, p, width - 75, height - 65)

        p.line(45, height - 60, width - 80, height - 60)

        # 10 प्रश्न (2 कॉलम लेआउट)
        y_pos = height - 80
        col1_x = 50
        col2_x = 310

        for idx, q in enumerate(data.questions[:10]):
            col_x = col1_x if idx < 5 else col2_x
            if idx == 5:
                y_pos = height - 80

            p.setFont("Helvetica-Bold", 8.5)
            p.drawString(col_x, y_pos, f"Q{idx+1}. {q.get('question_text', '')[:38]}")
            p.setFont("Helvetica", 7.5)
            p.drawString(col_x + 8, y_pos - 12, f"A) {q.get('opt_a', '')[:14]}  B) {q.get('opt_b', '')[:14]}")
            p.drawString(col_x + 8, y_pos - 22, f"C) {q.get('opt_c', '')[:14]}  D) {q.get('opt_d', '')[:14]}")
            y_pos -= 42

        # निचली OMR स्ट्रिप
        p.setLineWidth(1)
        p.line(30, 205, width - 30, 205)

        # रोल नंबर ग्रिड
        p.setFont("Helvetica-Bold", 9)
        p.drawString(50, 190, "ROLL NUMBER")
        p.setFont("Helvetica", 7)
        p.drawString(50, 178, "Fill 2-Digit Roll No:")

        for col_idx in range(2):
            bx = 55 + (col_idx * 28)
            for num in range(10):
                by = 160 - (num * 12)
                p.circle(bx, by, 4, stroke=1, fill=0)
                p.setFont("Helvetica", 5.5)
                p.drawCentredString(bx, by - 2, str(num))

        # आंसर स्ट्रिप (10 प्रश्न)
        p.setFont("Helvetica-Bold", 9)
        p.drawString(200, 190, "ANSWER STRIP (Q1 - Q10)")

        for q_no in range(1, 11):
            strip_col = 200 if q_no <= 5 else 370
            row_idx = (q_no - 1) % 5
            oy = 170 - (row_idx * 22)

            p.setFont("Helvetica-Bold", 7.5)
            p.drawString(strip_col, oy, f"Q{q_no:02d}")

            opts = ['A', 'B', 'C', 'D']
            for opt_idx, opt_char in enumerate(opts):
                circle_x = strip_col + 25 + (opt_idx * 20)
                p.circle(circle_x, oy + 2, 5.5, stroke=1, fill=0)
                p.setFont("Helvetica", 6)
                p.drawCentredString(circle_x, oy, opt_char)

        p.showPage()
        p.save()
        buffer.seek(0)

        return Response(content=buffer.getvalue(), media_type="application/pdf", headers={
            "Content-Disposition": f"attachment; filename=omr_{data.assignment_id}.pdf"
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 2. OMR स्कैन व ऑटो-अपडेट एंडपॉइंट
@app.post("/scan-omr")
def scan_omr(data: ScanRequest):
    try:
        req = urllib.request.urlopen(data.image_url)
        arr = np.asarray(bytearray(req.read()), dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Image decode failed")

        qr_text, _, _ = qr_detector.detectAndDecode(img)
        if not qr_text:
            raise HTTPException(status_code=400, detail="Master QR Code not found")

        assignment_id, class_name, section, subject = qr_text.split("|")

        # डमी उत्तर व रोल नंबर (ओपनसीवी बबल डिटेक्टर से मैप होकर)
        detected_roll_no = 1
        student_answers = ['A', 'B', 'C', 'D', 'A', 'B', 'C', 'D', 'A', 'B']

        # Answer Key फेच करना
        asg = supabase.table("assignments").select("answer_key, total_marks").eq("id", assignment_id).single().execute()
        correct_key = asg.data.get("answer_key", student_answers) if asg.data else student_answers
        total_marks = asg.data.get("total_marks", 10) if asg.data else 10

        score = sum(1 for s, c in zip(student_answers, correct_key) if s == c)
        zone = "green" if score >= 8 else ("yellow" if score >= 5 else "red")

        # छात्र का नाम खोजना
        st_res = supabase.table("students").select("id, name").eq("class", class_name).eq("section", section).eq("roll_no", detected_roll_no).execute()
        student_id = st_res.data[0]["id"] if st_res.data else None
        student_name = st_res.data[0]["name"] if st_res.data else f"Roll {detected_roll_no}"

        # ऑटो-अपडेट टेस्ट मूल्यांकन
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
