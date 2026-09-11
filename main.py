from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import urllib.request
import cv2
import numpy as np
from supabase import create_client
import os

app = FastAPI()

# Supabase Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://YOUR_PROJECT_ID.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "YOUR_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# OpenCV का इन-बिल्ट QR डिटेक्टर (किसी बाहरी C-लाइब्रेरी की जरूरत नहीं)
qr_detector = cv2.QRCodeDetector()

class ScanRequest(BaseModel):
    image_url: str

@app.get("/")
def home():
    return {"status": "OMR Cloud Engine is running!"}

@app.post("/scan-omr")
def scan_omr(data: ScanRequest):
    try:
        # 1. Supabase Storage से इमेज डाउनलोड करना
        req = urllib.request.urlopen(data.image_url)
        arr = np.asarray(bytearray(req.read()), dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Image could not be decoded")

        # 2. QR कोड पढ़ना (OpenCV इन-बिल्ट)
        qr_text, _, _ = qr_detector.detectAndDecode(img)
        if not qr_text:
            raise HTTPException(status_code=400, detail="QR Code not found on sheet")

        payload = qr_text.split("|")
        student_id = payload[0]
        assignment_id = payload[1]
        chapter_no = int(payload[2])

        # 3. स्कोर गणना (डिफ़ॉल्ट लॉजिक)
        score = 8
        total_marks = 10
        zone = "green" if score >= 8 else ("yellow" if score >= 5 else "red")

        # 4. Supabase में रिजल्ट इन्सर्ट करना
        res = supabase.table("test_evaluations").insert({
            "student_id": student_id,
            "assignment_id": assignment_id,
            "chapter_no": chapter_no,
            "test_type": "regular",
            "score": score,
            "total_marks": total_marks,
            "zone": zone
        }).execute()

        return {"success": True, "score": score, "zone": zone, "student_id": student_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

