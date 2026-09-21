import os
import io
import json
import base64
import traceback
from datetime import datetime
from typing import List, Optional, Any, Dict

import qrcode
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from weasyprint import HTML

app = FastAPI(title="School OMR Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origions=["*"],
    allow_crediantials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def make_qr_base64(payload_dict: dict) -> str:
    """QR कोड बनाकर Base64 स्ट्रिंग तैयार करता है"""
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
    return {"status": "live", "engine": "WeasyPrint OMR Engine"}

@app.post("/generate-omr-pdf")
@app.post("/GenerateOMRPdf")
async def generate_omr_pdf(request: Request):
    try:
        data = await request.json()
        print("Received payload keys:", list(data.keys()))

        # 1. डायनामिक करंट डेट
        current_date = datetime.now().strftime("%d-%b-%Y")
        
        # 2. फ़ील्ड्स को सुरक्षित पढ़ना
        cls_name = str(data.get("classs_name") or data.get("class_name") or "6")
        subj = str(data.get("subject") or "HINDI")
        sec = str(data.get("section") or "A")
        school = str(data.get("school_name") or "राजकीय उच्च माध्यमिक विद्यालय")
        assign_id = str(data.get("assign_id") or f"TST-{int(datetime.now().timestamp())}")
        
        # 3. total_questions पार्स करना (10 या 20)
        raw_tq = data.get("total_questions")
        try:
            total_q = int(raw_tq) if raw_tq is not None else 10
        except Exception:
            total_q = 10

        # 4. FlutterFlow से आई प्रश्न लिस्ट को पार्स करना
        raw_questions = data.get("question_json") or []
        if isinstance(raw_questions, str):
            try:
                raw_questions = json.loads(raw_questions)
            except Exception:
                raw_questions = []

        if not isinstance(raw_questions, list):
            raw_questions = []

        # तय संख्या तक सवाल लेना (स्क्रीन पर जो क्रम है, वही रहेगा)
        active_questions = raw_questions[:total_q]
        if not active_questions:
            active_questions = [
                {
                    "question_text": "प्रश्न उपलब्ध नहीं हैं",
                    "opt_a": "-",
                    "opt_b": "-",
                    "opt_c": "-",
                    "opt_d": "-",
                    "correct_opt": "A"
                }
            ]

        # 5. आपके सटीक कॉलम 'correct_opt' से QR Code हेतु Answer Key बनाना
        answer_key = {}
        for idx, q in enumerate(active_questions):
            q_dict = dict(q) if isinstance(q, dict) else {}
            ans = q_dict.get('correct_opt') or q_dict.get('correct_option') or 'A'
            answer_key[str(idx + 1)] = str(ans).strip().upper()

        qr_payload = {
            "aid": assign_id,
            "cls": cls_name,
            "sec": sec,
            "sub": subj,
            "total": len(active_questions),
            "dt": current_date,
            "keys": answer_key
        }
        qr_b64 = make_qr_base64(qr_payload)

        # 6. प्रश्नों को 2 कॉलमों में विभाजित करना
        half = (len(active_questions) + 1) // 2
        left_q = active_questions[:half]
        right_q = active_questions[half:]

        is_20 = total_q > 10
        q_font_size = "8pt" if is_20 else "9.5pt"
        opt_font_size = "7.5pt" if is_20 else "8.5pt"
        q_spacing = "4px" if is_20 else "8px"

        def render_q_html(questions_list, start_index):
            html = ""
            for i, q in enumerate(questions_list):
                q_dict = dict(q) if isinstance(q, dict) else {}
                q_num = start_index + i + 1
                q_text = q_dict.get('question_text', '')
                a = q_dict.get('opt_a', '')
                b = q_dict.get('opt_b', '')
                c = q_dict.get('opt_c', '')
                d = q_dict.get('opt_d', '')
                html += f"""
                <div class="q-item" style="margin-bottom: {q_spacing}; page-break-inside: avoid;">
                  <div class="q-title" style="font-size: {q_font_size}; font-weight: bold;">{q_num}. {q_text}</div>
                  <table class="opt-table" style="width: 100%; font-size: {opt_font_size};">
                    <tr><td style="width: 50%;">(A) {a}</td><td style="width: 50%;">(B) {b}</td></tr>
                    <tr><td style="width: 50%;">(C) {c}</td><td style="width: 50%;">(D) {d}</td></tr>
                  </table>
                </div>
                """
            return html

        left_html = render_q_html(left_q, 0)
        right_html = render_q_html(right_q, half)

        # 7. OMR Strip तैयार करना (10 सवाल -> 2 कॉलम, 20 सवाल -> 4 कॉलम)
        rows_per_col = 5
        total_cols = (len(active_questions) + rows_per_col - 1) // rows_per_col
        omr_cols_html = ""
        for c in range(max(1, total_cols)):
            col_rows = ""
            for r in range(rows_per_col):
                q_n = c * rows_per_col + r + 1
                if q_n <= len(active_questions):
                    q_str = f"Q0{q_n}" if q_n < 10 else f"Q{q_n}"
                    col_rows += f"""
                    <div style="font-size: 7pt; margin-bottom: 2px;">
                      <b>{q_str}</b>
                      <span class="bubble">A</span><span class="bubble">B</span><span class="bubble">C</span><span class="bubble">D</span>
                    </div>
                    """
            omr_cols_html += f'<td style="padding-right: 12px; vertical-align: top;">{col_rows}</td>'

        full_html = f"""<!DOCTYPE html>
        <html lang="hi">
        <head>
        <meta charset="utf-8">
        <style>
          @page {{ size: A4 portrait; margin: 10mm 12mm 8mm 12mm; }}
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
          .bottom-panel {{
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            border-top: 1.5px solid #222;
            padding-top: 5px;
            background: white;
          }}
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
                <div style="font-weight: bold; font-size: 10pt; margin-bottom: 2px;">{school}</div>
                <div style="font-size: 8.5pt; color: #222;"><b>निर्देश:</b> सभी प्रश्नों के उत्तर नीचे OMR स्ट्रिप में नीले/काले बॉलपेन से गोला भरकर दें।</div>
              </td>
              <td style="width: 35%; font-size: 8.5pt; text-align: right;">
                <div>Name: __________________________</div>
                <div style="margin-top: 4px;">Roll No: <span class="roll-box"></span><span class="roll-box"></span><span class="roll-box"></span><span class="roll-box"></span></div>
              </td>
            </tr>
          </table>

          <div class="q-container">
            <div class="q-col">{left_html}</div>
            <div class="q-col-gap"></div>
            <div class="q-col">{right_html}</div>
          </div>

          <div class="bottom-panel">
            <table style="width: 100%;">
              <tr>
                <td style="width: 62px; vertical-align: middle;">
                  <img src="data:image/png;base64,{qr_b64}" width="58" height="58" />
                </td>
                <td style="width: 155px; font-size: 7.5pt; line-height: 1.35; padding-left: 6px; vertical-align: middle;">
                  <b>TEST DETAILS</b><br>Class: {cls_name} - {sec}<br>Subject: {subj}<br>Date: {current_date}<br>
                  <span style="font-size: 6.5pt; color: #555;">ID: {assign_id}</span>
                </td>
                <td style="width: 10px; border-right: 1px solid #ccc;"></td>
                <td style="padding-left: 10px; vertical-align: middle;">
                  <div style="font-size: 7.5pt; font-weight: bold; margin-bottom: 3px;">ANSWER STRIP (Mark One Option Only)</div>
                  <table style="border-collapse: collapse;"><tr>{omr_cols_html}</tr></table>
                </td>
              </tr>
            </table>
          </div>
        </body>
        </html>"""

        pdf_bytes = HTML(string=full_html).write_pdf()
        return Response(content=pdf_bytes, media_type="application/pdf")

    except Exception as e:
        print("ERROR IN GENERATE OMR:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

