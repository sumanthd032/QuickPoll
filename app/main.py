
import os
import uuid
import datetime
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict

# --- Firebase Admin SDK ---
# Make sure to install the required library:
# pip install google-cloud-firestore
import firebase_admin
from firebase_admin import credentials, firestore

# --- App Configuration ---

# Create the FastAPI app instance
app = FastAPI(
    title="QuickPoll API",
    description="Backend API for the QuickPoll application.",
    version="1.0.0"
)

# --- Firebase Initialization ---
# Ensure your service account key file is in the root directory
try:
    # The path should be relative to where you run `uvicorn`
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firestore connection successful.")
except Exception as e:
    print(f"🔥 Firestore connection failed: {e}")
    db = None

# --- Static Files ---
static_dir = os.path.join(os.path.dirname(__file__), '..', 'static')
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# --- Pydantic Models (Data Validation) ---

class PollCreate(BaseModel):
    """Model for creating a new poll."""
    question: str = Field(..., min_length=3, max_length=200)
    options: List[str] = Field(..., min_items=2, max_items=10)
    expiry: str 

class PollResponse(BaseModel):
    """Model for returning poll data to the client."""
    id: str
    question: str
    options: List[Dict[str, str]] # e.g., [{'id': 'opt_1', 'text': 'Python'}]
    created_at: datetime.datetime
    is_expired: bool = False
    

# --- API Endpoints ---

@app.post("/api/polls", response_model=PollResponse, status_code=status.HTTP_201_CREATED, tags=["Polls"])
async def create_poll(poll_data: PollCreate):
    """
    Creates a new poll and stores it in Firestore.
    """
    if not db:
        raise HTTPException(status_code=503, detail="Firestore service is not available.")

    poll_id = str(uuid.uuid4())[:8] # Short, unique ID

    options_with_ids = [{"id": f"opt_{i+1}", "text": text} for i, text in enumerate(poll_data.options)]
    results = {option["id"]: 0 for option in options_with_ids}

    poll_record = {
        "id": poll_id,
        "question": poll_data.question,
        "options": options_with_ids,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
        "expiry_duration": poll_data.expiry,
        "results": results,
        "voter_ips": [] # To track unique voters
    }

    try:
        poll_ref = db.collection('polls').document(poll_id)
        poll_ref.set(poll_record)
        
        return PollResponse(
            id=poll_id,
            question=poll_record["question"],
            options=poll_record["options"],
            created_at=poll_record["created_at"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create poll: {e}")


@app.get("/api/health", tags=["Status"])
async def health_check():
    """A simple endpoint to check if the API is running."""
    return {"status": "ok", "message": "API is running smoothly"}

# --- Frontend Serving ---

@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_home(request: Request):
    """Serves the main poll creation page (index.html)."""
    return FileResponse(os.path.join(static_dir, 'index.html'))

@app.get("/polls/{poll_id}", response_class=HTMLResponse, tags=["Frontend"])
async def serve_poll_page(poll_id: str, request: Request):
    """Serves the poll page for a specific poll ID."""
    return FileResponse(os.path.join(static_dir, 'poll.html'))