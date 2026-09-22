import os
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

app = FastAPI(title="School OMR Engine")

# 1. CORS कॉन्फ़िगरेशन (बिना किसी टाइपो के)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Supabase क्रेडेंशियल्स (Render के Environment Variables या सीधे स्ट्रिंग में डालें)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project-ref.supabase.co")
# ध्यान दें: Storage में अपलोड करने के लिए 'service_role' की आवश्यकता होती है
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "your-service-role-key")

# आपके Supabase बकेट का नाम
BUCKET_NAME = "omr-sheets"  # यदि आपके बकेट का नाम अलग है तो यहाँ बदलें

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


@app.post("/generate-omr-pdf")
async def generate_omr_pdf(request: Request):
    try:
        data = await request.json()

        # ----------------------------------------------------
        # आपका मौजूदा PDF जनरेशन लॉजिक यहाँ रहेगा:
        # मान लीजिए pdf_bytes में आपकी तैयार की गई PDF के बाइट्स हैं
        # उदा. pdf_bytes = make_omr_pdf(data)
        # ----------------------------------------------------
        pdf_bytes = your_existing_pdf_generation_logic(data)

        if not pdf_bytes:
            raise ValueError("PDF बाइट्स जनरेट नहीं हो सके।")

        # डायनामिक फ़ाइल नाम तैयार करना
        class_name = data.get("class_name", "Class")
        section = data.get("section", "")
        today_date = datetime.now().strftime("%d-%m-%Y")
        unique_id = str(uuid.uuid4())[:8]

        file_name = f"OMR_{class_name}_{section}_{today_date}_{unique_id}.pdf"
        storage_path = f"omr_sheets/{file_name}"

        # 3. Supabase Storage में अपलोड करें
        upload_response = supabase.storage.from_(BUCKET_NAME).upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf"}
        )

        # 4. 10 मिनट (600 सेकंड) के लिए मान्य Signed URL बनाएँ
        signed_res = supabase.storage.from_(BUCKET_NAME).create_signed_url(
            path=storage_path,
            expires_in=600
        )

        download_url = signed_res.get("signedURL") or signed_res.get("signedUrl")

        # 5. सीधा और साफ़ JSON रिस्पॉन्स
        return {
            "success": True,
            "download_url": download_url,
            "file_name": file_name
        }

    except Exception as e:
        print(f"Error in generate_omr_pdf: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate OMR: {str(e)}")

