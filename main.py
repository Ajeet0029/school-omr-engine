import os
import io
import json
import base64
from datetime import datetime
from typing import List, Optional, Any, Dict

import qrcode
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from weasyprint import HTML

app = FastAPI(title="Dynamic Hindi OMR Generator")

# इनपुट डेटा का स्कीमा
class OMRRequest(BaseModel):
    class_name: Optional[Any] = "7"
    subject: Optional[Any] = "HINDI"
    section: Optional[Any] = "A"
    school_name: Optional[Any] = "राजकीय उच्च माध्यमिक विद्यालय"
    total_questions: Optional[Any] = 10
    assign_id: Optional[Any] = None
    question_json: Optional[Any] = []

def make_qr_base64(payload_dict: dict) -> str:
    """QR कोड बनाकर Base64 स्ट्रिंग में बदलता है ताकि HTML में सीधे दिख सके"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(json.dumps(payload_dict, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode('utf-8')

@app.get("/")
def root():
    return {"status": "live", "engine": "WeasyPrint Hindi OMR Engine"}

@app.post("/generate-omr-pdf")
@app.post("/GenerateOMRPdf")
async def generate_omr_pdf(req: OMRRequest):
    try:
        # क्लास का नाम दोनों कीज़ से चेक करना
        cls_name = str(req.class_name or req.classs_name or "6")
        
        # total_questions अगर स्ट्रिंग में आया हो तो int बनाना
        try:
            total_q = int(req.total_questions)
        except:
            total_q = 10

        # question_json अगर स्ट्रिंग रूप में आया हो तो parse करना
        questions = req.question_json
        if isinstance(questions, str):
            questions = json.loads(questions)
        if not isinstance(questions, list):
            questions = []
            
        active_questions = questions[:total_q]






@app.post("/generate-omr-pdf")
@app.post("/GenerateOMRPdf")
async def generate_omr_pdf(req: OMRRequest):
    try:
        # 1. आज की तारीख (Dynamic Date of Printing)
        current_date = datetime.now().strftime("%d-%b-%Y")
        total_q = req.total_questions if req.total_questions in [10, 20] else len(req.question_json)
        assign_id = req.assign_id or f"TST-{int(datetime.now().timestamp())}"
        
        # 2. जितने सवाल तय हैं, उतने ही लेना (10 या 20)
        active_questions = req.question_json[:total_q]
        
        # 3. QR कोड के लिए Answer Key तैयार करना (Auto-Checking स्कैनर के लिए)
        answer_key = {}
        for idx, q in enumerate(active_questions):
            ans = q.get('correct_option') or q.get('answer') or q.get('correct_ans') or ''
            answer_key[str(idx + 1)] = str(ans).strip().upper()
            
        qr_payload = {
            "aid": assign_id,
            "cls": req.classs_name,
            "sec": req.section,
            "sub": req.subject,
            "total": len(active_questions),
            "dt": current_date,
            "keys": answer_key
        }
        qr_b64 = make_qr_base64(qr_payload)
        
        # 4. प्रश्नों को दो कॉलमों में बाँटना (बाएँ और दाएँ)
        half = (len(active_questions) + 1) // 2
        left_q = active_questions[:half]
        right_q = active_questions[half:]
        
        # 10 vs 20 प्रश्नों के लिए लेआउट डायनामिक स्केलिंग
        is_20 = total_q > 10
        q_font_size = "8pt" if is_20 else "9.5pt"
        opt_font_size = "7.5pt" if is_20 else "8.5pt"
        q_spacing = "4px" if is_20 else "8px"
        
        def render_q_html(questions_list, start_index):
            html = ""
            for i, q in enumerate(questions_list):
                q_num = start_index + i + 1
                q_text = q.get('question_text') or q.get('question') or ''
                a = q.get('option_a', '')
                b = q.get('option_b', '')
                c = q.get('option_c', '')
                d = q.get('option_d', '')
                html += f"""
                <div class="q-item" style="margin-bottom: {q_spacing};">
                  <div class="q-title" style="font-size: {q_font_size};">{q_num}. {q_text}</div>
                  <table class="opt-table" style="font-size: {opt_font_size};">
                    <tr><td>(A) {a}</td><td>(B) {b}</td></tr>
                    <tr><td>(C) {c}</td><td>(D) {d}</td></tr>
                  </table>
                </div>
                """
            return html

        left_html = render_q_html(left_q, 0)
        right_html = render_q_html(right_q, half)
        
        # 5. OMR Answer Strip (10 प्रश्न -> 2 कॉलम, 20 प्रश्न -> 4 कॉलम)
        rows_per_col = 5
        total_cols = (total_q + rows_per_col - 1) // rows_per_col
        omr_cols_html = ""
        for c in range(total_cols):
            col_rows = ""
            for r in range(rows_per_col):
                q_n = c * rows_per_col + r + 1
                if q_n <= total_q:
                    q_str = f"Q0{q_n}" if q_n < 10 else f"Q{q_n}"
                    col_rows += f"""
                    <div class="omr-row">
                      <b>{q_str}</b>
                      <span class="bubble">A</span>
                      <span class="bubble">B</span>
                      <span class="bubble">C</span>
                      <span class="bubble">D</span>
                    </div>
                    """
            omr_cols_html += f'<td style="padding-right: 12px; vertical-align: top;">{col_rows}</td>'
        
        # 6. संपूर्ण HTML टेम्पलेट (शुद्ध हिंदी फॉन्ट Noto Sans Devanagari के साथ)
        full_html = f"""<!DOCTYPE html>
        <html lang="hi">
        <head>
        <meta charset="utf-8">
        <style>
          @page {{
            size: A4 portrait;
            margin: 10mm 12mm 8mm 12mm;
          }}
          * {{ box-sizing: border-box; margin: 0; padding: 0; }}
          body {{
            font-family: 'Noto Sans Devanagari', 'FreeSans', 'DejaVu Sans', sans-serif;
            color: #111;
            line-height: 1.25;
          }}
          .header-table {{
            width: 100%;
            border-bottom: 1.5px solid #222;
            padding-bottom: 5px;
            margin-bottom: 8px;
          }}
          .header-table td {{ vertical-align: top; }}
          .inst-text {{ font-size: 8.5pt; color: #222; line-height: 1.3; }}
          .student-meta {{ font-size: 8.5pt; text-align: right; }}
          .roll-box {{
            display: inline-block;
            border: 1px solid #333;
            width: 14px;
            height: 16px;
            margin-left: 2px;
            vertical-align: middle;
          }}
          .q-container {{ width: 100%; display: table; table-layout: fixed; }}
          .q-col {{ display: table-cell; width: 49%; vertical-align: top; }}
          .q-col-gap {{ display: table-cell; width: 2%; }}
          .q-item {{ page-break-inside: avoid; }}
          .q-title {{ font-weight: bold; margin-bottom: 2px; }}
          .opt-table {{ width: 100%; }}
          .opt-table td {{ width: 50%; padding: 1px 0; }}
          
          .bottom-panel {{
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            border-top: 1.5px solid #222;
            padding-top: 5px;
            background: white;
          }}
          .bottom-table {{ width: 100%; }}
          .qr-cell {{ width: 62px; vertical-align: middle; }}
          .details-cell {{ width: 155px; font-size: 7.5pt; line-height: 1.35; padding-left: 6px; vertical-align: middle; }}
          .divider-cell {{ width: 10px; border-right: 1px solid #ccc; }}
          .omr-cell {{ padding-left: 10px; vertical-align: middle; }}
          .omr-strip-title {{ font-size: 7.5pt; font-weight: bold; margin-bottom: 3px; }}
          .omr-row {{ font-size: 7pt; margin-bottom: 2px; }}
          .bubble {{
            display: inline-block;
            width: 11px;
            height: 11px;
            border-radius: 50%;
            border: 0.8px solid #222;
            text-align: center;
            line-height: 10px;
            font-size: 6pt;
            font-weight: bold;
            margin: 0 1px;
          }}
        </style>
        </head>
        <body>
          <table class="header-table">
            <tr>
              <td style="width: 65%;">
                <div style="font-weight: bold; font-size: 10pt; margin-bottom: 2px;">{req.school_name}</div>
                <div class="inst-text"><b>निर्देश:</b> सभी प्रश्नों के उत्तर नीचे OMR स्ट्रिप में नीले/काले बॉलपेन से गोला भरकर दें।</div>
              </td>
              <td style="width: 35%;" class="student-meta">
                <div>Name: __________________________</div>
                <div style="margin-top: 4px;">
                  Roll No: 
                  <span class="roll-box"></span><span class="roll-box"></span><span class="roll-box"></span><span class="roll-box"></span>
                </div>
              </td>
            </tr>
          </table>

          <div class="q-container">
            <div class="q-col">{left_html}</div>
            <div class="q-col-gap"></div>
            <div class="q-col">{right_html}</div>
          </div>

          <div class="bottom-panel">
            <table class="bottom-table">
              <tr>
                <td class="qr-cell">
                  <img src="data:image/png;base64,{qr_b64}" width="58" height="58" />
                </td>
                <td class="details-cell">
                  <b>TEST DETAILS</b><br>
                  Class: {req.classs_name} - {req.section}<br>
                  Subject: {req.subject}<br>
                  Date: {current_date}<br>
                  <span style="font-size: 6.5pt; color: #555;">ID: {assign_id}</span>
                </td>
                <td class="divider-cell"></td>
                <td class="omr-cell">
                  <div class="omr-strip-title">ANSWER STRIP (Mark One Option Only)</div>
                  <table style="border-collapse: collapse;">
                    <tr>{omr_cols_html}</tr>
                  </table>
                </td>
              </tr>
            </table>
          </div>
        </body>
        </html>
        """

        # 7. WeasyPrint से सीधे PDF में कनवर्ट करना
        pdf_bytes = HTML(string=full_html).write_pdf()
        return Response(content=pdf_bytes, media_type="application/pdf")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


