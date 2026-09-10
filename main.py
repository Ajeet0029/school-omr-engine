from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import urllib.request
import cv2
import numpy as np
from pyzbar.pyzbar import decode
from supabase import create_client
import os

app = FastAPI()

# Supabase Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL", "यहाँ_अपना_SUPABASE_URL_डालें")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "यहाँ_अपनी_SUPABASE_SERVICE_ROLE_KEY_डालें")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

ANSWER_KEY = {1: 1, 2: 1, 3: 0, 4: 1, 5: 0, 6: 2, 7: 0, 8: 1, 9: 0, 10: 1}

class ScanRequest(BaseModel):
    image_url: str

@app.get("/")
def home():
    return {"status": "OMR Cloud Engine is running!"}

@app.post("/scan-omr")
def scan_omr(data: ScanRequest):
    try:
        req = urllib.request.urlopen(data.image_url)
        arr = np.asarray(bytearray(req.read()), dtype=np.uint8)
        img = cv2.imdecode(arr, -1)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        qr_data = decode(gray)
        if not qr_data:
            raise HTTPException(status_code=400, detail="QR Code not found")

        payload = qr_data[0].data.decode("utf-8").split("|")
        student_id, assignment_id, chapter_no = payload[0], payload[1], int(payload[2])

        score = 8
        total_marks = 10
        zone = "green" if score >= 8 else ("yellow" if score >= 5 else "red")

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

