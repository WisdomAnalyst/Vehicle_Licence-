from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import requests
import base64
import io
import re
from pathlib import Path
import os
from dotenv import load_dotenv
import logging
from PIL import Image, ImageOps
import pytesseract

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Vehicle Plate Detection",
    description="Real-time License Plate Detection and Recognition",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration - Load from .env file securely
DEPLOYMENT_URL = os.getenv("DEPLOYMENT_URL")
API_KEY = os.getenv("API_KEY")
UPLOAD_DIR = Path("upload")
UPLOAD_DIR.mkdir(exist_ok=True)

# Validate that required environment variables are set
if not DEPLOYMENT_URL:
    logger.error("DEPLOYMENT_URL not found in .env file")
    raise ValueError("DEPLOYMENT_URL environment variable is required")

if not API_KEY:
    logger.error("API_KEY not found in .env file")
    raise ValueError("API_KEY environment variable is required")

logger.info("✓ Environment variables loaded successfully")
logger.info(f"✓ Deployment URL configured: {DEPLOYMENT_URL[:50]}...")

# Optional: on Windows, Tesseract usually isn't on PATH by default.
# Set TESSERACT_CMD in .env to its install path, e.g.:
# TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def extract_plate_text(image: Image.Image, bbox: list) -> str:
    """
    Crop the detected plate region out of the full image and run OCR on it.
    Returns cleaned uppercase alphanumeric text, or "" if nothing readable was found.
    """
    try:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        # Small padding helps OCR when the box is tight against the characters
        pad = 4
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(image.width, x2 + pad), min(image.height, y2 + pad)

        crop = image.crop((x1, y1, x2, y2)).convert("L")  # grayscale

        # Upscale small crops — Tesseract does much better on larger text
        if crop.width < 300:
            scale = 300 / crop.width
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)

        crop = ImageOps.autocontrast(crop)

        # PSM 7 = treat the crop as a single line of text (good fit for plates)
        raw_text = pytesseract.image_to_string(
            crop,
            config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
        )
        cleaned = re.sub(r"[^A-Z0-9-]", "", raw_text.upper()).strip("-")
        return cleaned
    except pytesseract.TesseractNotFoundError:
        logger.warning("Tesseract OCR is not installed / not found — skipping text extraction. "
                        "See README for install instructions.")
        return ""
    except Exception as e:
        logger.warning(f"OCR failed on a detected plate: {e}")
        return ""


@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Serve the main HTML page"""
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Template not found")


@app.post("/api/detect")
async def detect_plate(file: UploadFile = File(...)):
    """
    Detect license plates in uploaded image

    Args:
        file: Image file (JPG, PNG, etc.)

    Returns:
        JSON with detection results
    """
    try:
        # Validate file type
        allowed_types = {"image/jpeg", "image/png", "image/bmp", "image/webp"}
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
            )

        # Validate file size (max 10MB)
        contents = await file.read()
        max_size = 10 * 1024 * 1024  # 10MB
        if len(contents) > max_size:
            raise HTTPException(
                status_code=413,
                detail="File size exceeds 10MB limit"
            )

        logger.info(f"Processing file: {file.filename} ({len(contents)} bytes)")

        # Normalize EXIF orientation ONCE, and use this same corrected image for
        # everything downstream: what we send to the detection API, what we crop
        # for OCR, and what we send back as the preview. Phone photos often store
        # pixel data "sideways" with an EXIF tag telling viewers to rotate it for
        # display — browsers honor that tag, but a raw pixel array sent to the model
        # doesn't. That mismatch is exactly what makes bounding boxes land in the
        # wrong place: the model's coordinates are correct for the *unrotated* image,
        # but the browser is displaying the *rotated* one. Baking the rotation into
        # the pixels before sending keeps both sides working off the same image.
        pil_image = ImageOps.exif_transpose(Image.open(io.BytesIO(contents)))
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")

        normalized_format = "JPEG" if file.content_type in ("image/jpeg", "image/jpg") \
            else file.content_type.split("/")[-1].upper()
        normalized_buffer = io.BytesIO()
        pil_image.save(normalized_buffer, format=normalized_format)
        normalized_bytes = normalized_buffer.getvalue()

        # Save uploaded file (normalized copy, so it matches what was actually analyzed)
        file_path = UPLOAD_DIR / file.filename
        with open(file_path, "wb") as f:
            f.write(normalized_bytes)
        logger.info(f"✓ File saved to: {file_path}")

        # Prepare request to external API — send the orientation-normalized bytes
        files = {"file": (file.filename, normalized_bytes, file.content_type)}
        headers = {"Authorization": f"Bearer {API_KEY}"}
        data = {"conf": 0.25, "iou": 0.7, "imgsz": 640}

        logger.info("Sending request to detection API...")

        # Call external API
        response = requests.post(
            DEPLOYMENT_URL,
            headers=headers,
            data=data,
            files=files,
            timeout=30
        )

        if response.status_code != 200:
            logger.error(f"API returned status code: {response.status_code}")
            raise HTTPException(
                status_code=response.status_code,
                detail="Detection failed"
            )

        result = response.json()

        # Ultralytics dedicated-endpoint response shape:
        # { "images": [ { "shape": [...], "results": [...], "speed": {...} } ], "metadata": {...} }
        images = result.get("images", [])
        raw_results = images[0].get("results", []) if images else []
        speed = images[0].get("speed", {}) if images else {}

        # Open the uploaded image once so we can crop each detected box for OCR
        # (already orientation-normalized above, so crops line up with the boxes)

        detections = []
        for item in raw_results:
            box = item.get("box")
            bbox = [box["x1"], box["y1"], box["x2"], box["y2"]] if box else None

            plate_text = extract_plate_text(pil_image, bbox) if bbox else ""

            detections.append({
                # Real OCR'd characters when readable, otherwise fall back to the model's class name
                "plate_number": plate_text or item.get("name", "plate"),
                "confidence": item.get("confidence", 0),
                "bbox": bbox,
            })

        # Total processing time in seconds (frontend multiplies by 1000 to show ms)
        processing_time_ms = sum(speed.values()) if speed else 0
        processing_time = processing_time_ms / 1000

        logger.info(f"✓ Detection successful: {len(detections)} plates found")
        for d in detections:
            logger.info(f"  → {d['plate_number']} ({d['confidence']*100:.1f}%)")

        # Encode the SAME normalized image to base64 for the frontend preview,
        # so the boxes drawn client-side line up with what the model actually analyzed
        image_base64 = base64.b64encode(normalized_bytes).decode()

        return JSONResponse({
            "success": True,
            "image": f"data:image/{normalized_format.lower()};base64,{image_base64}",
            "detections": detections,
            "processing_time": processing_time,
            "filename": file.filename
        })

    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        logger.error("Request to detection API timed out")
        raise HTTPException(status_code=504, detail="Detection service timeout")
    except requests.exceptions.ConnectionError:
        logger.error("Failed to connect to detection API")
        raise HTTPException(status_code=503, detail="Detection service unavailable")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Vehicle Plate Detection API",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)