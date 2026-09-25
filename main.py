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
import re

DEV_RE = re.compile(r'[\u0900-\u097F]')
RUN_RE = re.compile(r'[\u0900-\u097F]+|[^\u0900-\u097F]+')

font_reg = "/tmp/fonts/NotoSansDevanagari-Regular.ttf"
font_bold = "/tmp/fonts/NotoSansDevanagari-Bold.ttf"


def hindi_width(text, size, bold=False):
    fp=font_bold if bold else font_reg
    f=ImageFont.truetype(fp,max(1,int(round(size*HINDI_RENDER_SCALE))))
    d=ImageDraw.Draw(Image.new("L",(10,10)))
    return d.textlength(str(text),font=f,direction="ltr",language="hi")/HINDI_RENDER_SCALE


def mixed_width(text,size,bold=False):
    total=0.0
    for run in RUN_RE.findall(str(text or "")):
        if DEV_RE.search(run):
            total += hindi_width(run,size,bold)
        else:
            total += pdfmetrics.stringWidth(run, "Helvetica-Bold" if bold else "Helvetica", size)
    return total


def draw_mixed_text(c,x,y,text,size,bold=False):
    text=str(text or "")
    if not text: return
    font="Helvetica-Bold" if bold else "Helvetica"
    for run in RUN_RE.findall(text):
        if DEV_RE.search(run):
            draw_hindi_text(c,x,y,run,size,bold)
            x += hindi_width(run,size,bold)
        else:
            c.setFont(font,size)
            c.drawString(x,y,run)
            x += pdfmetrics.stringWidth(run,font,size)


def wrap_mixed(text,max_width,size,bold=False,max_lines=None):
    text=str(text or "").strip().replace("\n"," ")
    if not text: return [""]
    words=text.split()
    lines=[]; cur=""
    for word in words:
        candidate=word if not cur else cur+" "+word
        if mixed_width(candidate,size,bold)<=max_width:
            cur=candidate
        else:
            if cur:
                lines.append(cur)
            piece=""
            for ch in word:
                cand=piece+ch
                if piece and mixed_width(cand,size,bold)>max_width:
                    lines.append(piece)
                    piece=ch
                else:
                    piece=cand
            cur=piece
    if cur: lines.append(cur)
    if max_lines is not None and len(lines)>max_lines:
        # Only used when explicitly requested. Production layout calls without a limit.
        lines=lines[:max_lines]
        ell="…"; last=lines[-1]
        while last and mixed_width(last+ell,size,bold)>max_width:
            last=last[:-1]
        lines[-1]=(last+ell) if last else ell
    return lines


def make_packet(payload, qs, total_q, date_s, fields):
    import base64,zlib,json,re
    def first(d,*keys):
        for k in keys:
            if isinstance(d,dict):
                v=d.get(k)
                if v not in (None,""): return str(v)
        return ""
    def normans(v):
        s=str(v or "").strip().upper()
        m=re.search(r'\b([ABCD])\b',s)
        return m.group(1) if m else (s if s in "ABCD" else "")
    ans=[]; qmeta=[]
    for i,q in enumerate(qs[:total_q]):
        q=q if isinstance(q,dict) else {}
        a=normans(first(q,"correct_option","correct_answer","answer","answer_key","correctAnswer"))
        ans.append(a)
        qmeta.append({
            "n":i+1,
            "id":first(q,"question_id","qid","id") or f"Q{i+1:02d}",
            "a":a,
            "ch":first(q,"chapter","chapter_name") or fields["chapter"],
            "sk":first(q,"skill_tested","skill","skillTested") or fields["skill"],
            "d":first(q,"difficulty","difficulty_level","difficultyLevel") or fields["difficulty"]
        })
    packet={
        "v":1,"type":"school_omr","test":fields["assignment_id"],
        "school":fields["school"],"class":fields["class_name"],"section":fields["section"],
        "subject":fields["subject"],"date":date_s,"difficulty":fields["difficulty"],
        "chapter":fields["chapter"],"skill":fields["skill"],"n":total_q,
        "ans":"".join(ans),"q":qmeta
    }
    raw=json.dumps(packet,ensure_ascii=False,separators=(",",":")).encode("utf-8")
    return "OMR1."+base64.urlsafe_b64encode(zlib.compress(raw,9)).decode("ascii").rstrip("=")


def generate_hybrid_omr_pdf(payload):
    buffer=io.BytesIO(); c=canvas.Canvas(buffer,pagesize=A4)
    width,height=A4
    strip_y=154
    # anchors
    a=14; inset=18
    c.setFillColor(colors.black)
    for x,y in [(inset,height-inset-a),(width-inset-a,height-inset-a),(inset,inset),(width-inset-a,inset)]:
        c.rect(x,y,a,a,fill=1,stroke=0)

    # fields
    def getv(*keys,default=""):
        for k in keys:
            v=payload.get(k)
            if v not in (None,""): return str(v)
        return default
    school=getv("school_name","school",default="SCHOOL ASSESSMENT TEST")
    class_name=getv("class_name","class",default="")
    section=getv("section",default="")
    subject=getv("subject",default="")
    difficulty=getv("difficulty_level","difficulty",default="")
    chapter=getv("chapter","chapter_name",default="")
    skill=getv("skill_tested","skill",default="")
    date_s=getv("date",default=datetime.now().strftime("%d-%b-%Y"))
    assignment_id=getv("assignment_id","test_id",default="T01")

    # header left
    if DEV_RE.search(school):
        draw_mixed_text(c,45,height-34,school,12,True)
    else:
        c.setFont("Helvetica-Bold",12); c.drawString(45,height-34,school)
    meta_parts=[]
    if class_name: meta_parts.append(f"Class: {class_name}")
    if section: meta_parts.append(f"Section: {section}")
    if subject: meta_parts.append(f"Subject: {subject}")
    meta="  |  ".join(meta_parts)
    draw_mixed_text(c,45,height-48,meta,7.2,False)

    # instruction
    draw_mixed_text(c,45,height-63,"निर्देश: सभी प्रश्नों के उत्तर नीचे दी गई OMR पट्टी में नीले/काले पेन से गोला भरकर दें।",6.8,False)

    # name/roll box
    rx,ry,rw,rh=width-220,height-62,180,41
    c.setLineWidth(.7); c.rect(rx,ry,rw,rh,fill=0,stroke=1)
    c.setFont("Helvetica-Bold",7.2); c.drawString(rx+7,ry+26,"NAME:")
    c.line(rx+45,ry+25,rx+rw-7,ry+25)
    c.drawString(rx+7,ry+10,"ROLL NO:")
    bx=rx+57; bs=11; gap=3
    for b in range(4): c.rect(bx+b*(bs+gap),ry+6,bs,bs,fill=0,stroke=1)
    c.setLineWidth(.8); c.line(40,height-72,width-40,height-72)

    # questions
    raw=payload.get("questions",[])
    if isinstance(raw,str):
        try: raw=json.loads(raw)
        except Exception: raw=[]
    if not isinstance(raw,list): raw=[]
    total_q=len(raw) if raw else int(payload.get("total_questions",10) or 10)
    total_q=max(1,total_q)
    if total_q>20: total_q=20
    qs=[q if isinstance(q,dict) else {} for q in raw[:total_q]]
    while len(qs)<total_q: qs.append({})
    half=(total_q+1)//2
    rows=half
    q_top=height-88
    q_area=390 if total_q<=20 else 390
    row_h=q_area/rows
    col1_x=42; col2_x=width/2+7; col_w=width/2-49; gap=8; opt_w=(col_w-gap)/2

    for idx in range(total_q):
        q=qs[idx]
        def qv(*keys,default=""):
            for k in keys:
                v=q.get(k)
                if v not in (None,""): return str(v)
            return default
        qt=qv("question_text","question",default=f"प्रश्न संख्या {idx+1}")
        opts=[
            qv("opt_a","option_a",default="विकल्प A"),
            qv("opt_b","option_b",default="विकल्प B"),
            qv("opt_c","option_c",default="विकल्प C"),
            qv("opt_d","option_d",default="विकल्प D"),
        ]
        col2=idx>=half; row=idx-half if col2 else idx; x=col2_x if col2 else col1_x
        top=q_top-row*row_h

        qsize=8.0 if total_q<=10 else 6.5
        osize=6.7 if total_q<=10 else 5.2
        max_q_lines=None
        max_o_lines=None

        for _ in range(18):
            qlines=wrap_mixed(qt,col_w-16,qsize,True,max_q_lines)
            olines=[wrap_mixed(opts[j],opt_w-14,osize,False,max_o_lines) for j in range(4)]
            qh=len(qlines)*qsize*1.12
            oh=(max(len(olines[0]),len(olines[1])) + max(len(olines[2]),len(olines[3])))*osize*1.1 + 6
            if qh+oh+4 <= row_h-3 or (qsize<=4.8 and osize<=4.5):
                break
            qsize=max(4.8,qsize-.2); osize=max(4.5,osize-.15)

        y=top-2
        qlh=qsize*1.12
        for li,line in enumerate(qlines):
            draw_mixed_text(c,x+13,y-li*qlh,line,qsize,True)
        c.setFont("Helvetica-Bold",qsize)
        c.drawString(x,y,f"{idx+1}.")
        y-=len(qlines)*qlh+1

        row_gap=osize*1.15+2
        for j in range(4):
            rr=0 if j<2 else 1; cc=j%2
            oy=y-rr*row_gap
            ox=x+cc*(opt_w+gap)
            c.setFont("Helvetica",osize)
            c.drawString(ox,oy,f"({chr(65+j)})")
            for li,line in enumerate(olines[j]):
                draw_mixed_text(c,ox+14,oy-li*osize*1.1,line,osize,False)

    # bottom divider
    c.setLineWidth(1); c.line(40,strip_y,width-40,strip_y)

    # test details
    c.setFont("Helvetica-Bold",7.3); c.drawString(45,strip_y-12,"TEST DETAILS")
    detail_rows=[
        ("Class / Sec"," ".join([v for v in (class_name,section) if v])),
        ("Subject",subject),("Date",date_s),("Difficulty",difficulty or "-"),
        ("Chapter",chapter or "-"),("Skill",skill or "-"),("ID",assignment_id)
    ]
    dy=strip_y-23
    for lab,val in detail_rows:
        c.setFont("Helvetica-Bold",5.7); c.drawString(45,dy,lab+":")
        draw_mixed_text(c,76,dy,val,5.7,False)
        dy-=7.0

    # QR
    packet=make_packet(payload,qs,total_q,date_s,{"school":school,"class_name":class_name,"section":section,"subject":subject,"difficulty":difficulty,"chapter":chapter,"skill":skill,"assignment_id":assignment_id})
    qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=2,border=2)
    qr.add_data(packet); qr.make(fit=True)
    qr_img=qr.make_image(fill_color="black",back_color="white").convert("RGB")
    c.drawInlineImage(qr_img,45,24,48,48)

    # roll no bubble grid
    roll_x=140
    c.setFont("Helvetica-Bold",7.3); c.drawString(roll_x,strip_y-12,"ROLL NO")
    for col in range(2):
        bx=roll_x+6+col*18
        for num in range(10):
            by=strip_y-27-num*8.0
            c.circle(bx,by,3.0,stroke=1,fill=0)
            c.setFont("Helvetica",4.5); c.drawCentredString(bx,by-1.5,str(num))

    # answer strip
    ans_x=220
    c.setFont("Helvetica-Bold",7.3); c.drawString(ans_x,strip_y-12,"ANSWER STRIP (Mark One Option Only)")
    ans_cols=2 if total_q<=10 else 4
    qpc=(total_q+ans_cols-1)//ans_cols; col_gap=74
    for qi in range(total_q):
        ci=qi//qpc; ri=qi%qpc
        qx=ans_x+ci*col_gap; qy=strip_y-27-ri*10.0
        c.setFont("Helvetica-Bold",6.0); c.drawString(qx,qy-2,f"Q{qi+1:02d}")
        for oi,label in enumerate("ABCD"):
            bx=qx+20+oi*11.2
            c.circle(bx,qy,3.15,stroke=1,fill=0)
            c.setFont("Helvetica",4.4); c.drawCentredString(bx,qy-1.45,label)

    c.showPage(); c.save(); buffer.seek(0); return buffer.getvalue()



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
            "massage": "PDF generated successfully",
            "file_url":
            download_url, "signed_url":
            download_url, "download_urL":
            download_url, "file_name": file_name
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


