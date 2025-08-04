# app/main.py

import os
import uuid
import datetime
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

# --- Firebase Admin SDK ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- App Configuration ---
app = FastAPI(
    title="QuickPoll API",
    description="Backend API for the QuickPoll application.",
    version="1.0.0"
)

# --- Firebase Initialization ---
try:
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


# --- Pydantic Models ---

class PollCreate(BaseModel):
    question: str = Field(..., min_length=3, max_length=200)
    options: List[str] = Field(..., min_items=2, max_items=10)
    expiry: str

class PollResponse(BaseModel):
    id: str
    question: str
    options: List[Dict[str, str]]
    created_at: datetime.datetime
    is_expired: bool = False

# New model for returning full poll data including results
class PollData(PollResponse):
    results: Dict[str, int]
    user_voted: Optional[str] = None # Which option the user voted for, if any

# New model for handling a vote request
class VoteRequest(BaseModel):
    option_id: str


# --- Helper Functions ---

def get_client_ip(request: Request) -> str:
    """Gets the client's IP address from the request headers."""
    # In a production environment with a reverse proxy (like Nginx),
    # the IP is often in the 'X-Forwarded-For' header.
    x_forwarded_for = request.headers.get('x-forwarded-for')
    if x_forwarded_for:
        # The header can contain a comma-separated list of IPs.
        # The client's IP is typically the first one.
        ip = x_forwarded_for.split(',')[0]
    else:
        # Fallback to the direct client IP.
        ip = request.client.host
    return ip

# --- API Endpoints ---

@app.post("/api/polls", response_model=PollResponse, status_code=status.HTTP_201_CREATED, tags=["Polls"])
async def create_poll(poll_data: PollCreate):
    """Creates a new poll and stores it in Firestore."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore service is not available.")

    poll_id = str(uuid.uuid4())[:8]
    options_with_ids = [{"id": f"opt_{i+1}", "text": text} for i, text in enumerate(poll_data.options)]
    results = {option["id"]: 0 for option in options_with_ids}

    poll_record = {
        "id": poll_id,
        "question": poll_data.question,
        "options": options_with_ids,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
        "expiry_duration": poll_data.expiry,
        "results": results,
        "voter_ips": []
    }

    try:
        db.collection('polls').document(poll_id).set(poll_record)
        return PollResponse(**poll_record)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create poll: {e}")


@app.get("/api/polls/{poll_id}", response_model=PollData, tags=["Polls"])
async def get_poll_data(poll_id: str, request: Request):
    """Retrieves data for a specific poll, including results."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore service is not available.")
    
    poll_ref = db.collection('polls').document(poll_id)
    poll_doc = poll_ref.get()

    if not poll_doc.exists:
        raise HTTPException(status_code=404, detail="Poll not found")

    poll_data = poll_doc.to_dict()
    client_ip = get_client_ip(request)

    # Check if the current user's IP has already voted
    if client_ip in poll_data.get("voter_ips", []):
        poll_data["user_voted"] = "yes" # A simple flag is enough for now

    return PollData(**poll_data)


@app.post("/api/polls/{poll_id}/vote", status_code=status.HTTP_200_OK, tags=["Polls"])
async def cast_vote(poll_id: str, vote: VoteRequest, request: Request):
    """Casts a vote on a poll."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore service is not available.")

    poll_ref = db.collection('polls').document(poll_id)
    client_ip = get_client_ip(request)

    @firestore.transactional
    def vote_transaction(transaction, poll_ref, option_id, client_ip):
        snapshot = poll_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Poll not found")

        poll_data = snapshot.to_dict()

        # Check if IP has already voted
        if client_ip in poll_data.get("voter_ips", []):
            # This won't raise an error, but simply won't update.
            # The client-side will handle the UI.
            return {"message": "You have already voted."}
        
        # Check if the voted option is valid
        if option_id not in poll_data.get("results", {}):
            raise HTTPException(status_code=400, detail="Invalid option ID")

        # Update the vote count and add the IP
        transaction.update(poll_ref, {
            f'results.{option_id}': firestore.Increment(1),
            'voter_ips': firestore.ArrayUnion([client_ip])
        })
        return {"message": "Vote cast successfully."}

    transaction = db.transaction()
    result = vote_transaction(transaction, poll_ref, vote.option_id, client_ip)
    return result


@app.get("/api/health", tags=["Status"])
async def health_check():
    """A simple endpoint to check if the API is running."""
    return {"status": "ok", "message": "API is running smoothly"}

# --- Frontend Serving ---
@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_home():
    return FileResponse(os.path.join(static_dir, 'index.html'))

@app.get("/polls/{poll_id}", response_class=HTMLResponse, tags=["Frontend"])
async def serve_poll_page(poll_id: str):
    return FileResponse(os.path.join(static_dir, 'poll.html'))

