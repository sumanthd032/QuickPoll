# app/main.py

import os
import uuid
import datetime
import asyncio
import json
import io
import csv
import secrets
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from threading import Event

# --- NEW: Import dotenv ---
from dotenv import load_dotenv

# --- Firebase Admin SDK ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- Load Environment Variables ---
# This will load the variables from your .env file for local development
load_dotenv()

# --- App Configuration ---
app = FastAPI(
    title="QuickPoll API",
    description="Backend API for the QuickPoll application.",
    version="1.0.0"
)

# --- Firebase Initialization ---
try:
    private_key = os.getenv("FIREBASE_PRIVATE_KEY", "").replace('\\n', '\n')
    cred_dict = {
        "type": os.getenv("FIREBASE_TYPE"),
        "project_id": os.getenv("FIREBASE_PROJECT_ID"),
        "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
        "private_key": private_key,
        "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
        "client_id": os.getenv("FIREBASE_CLIENT_ID"),
        "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
        "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
        "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_X509_CERT_URL"),
        "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_X509_CERT_URL")
    }
    if not all(cred_dict.values()):
         raise ValueError("One or more Firebase environment variables are not set. Please check your .env file or hosting configuration.")
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firestore connection successful from environment variables.")
except Exception as e:
    print(f"🔥 Firestore connection failed: {e}")
    db = None

# --- Static Files ---
current_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(current_dir, '..', 'static')
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# --- Pydantic Models ---
class PollCreate(BaseModel):
    question: str = Field(..., min_length=3, max_length=200)
    options: List[str] = Field(..., min_items=2, max_items=10)
    expiry: str
    quiz_mode: bool = False

class PollCreateResponse(BaseModel):
    id: str
    host_secret: Optional[str] = None

class PollResponse(BaseModel):
    id: str
    question: str
    options: List[Dict[str, str]]
    created_at: datetime.datetime
    is_expired: bool = False
    quiz_mode: bool = False
    results_revealed: bool = True

class PollData(PollResponse):
    results: Dict[str, int]
    user_voted: Optional[str] = None

class VoteRequest(BaseModel):
    option_id: str

class HostActionRequest(BaseModel):
    host_secret: str

# --- Helper Functions ---
# --- UPDATED: IP Address Logic for Vercel ---
def get_client_ip(request: Request) -> str:
    """
    Gets the client's real IP address, prioritizing Vercel's specific header.
    """
    # Vercel provides the user's IP in this header
    vercel_ip = request.headers.get("x-vercel-forwarded-for")
    if vercel_ip:
        return vercel_ip
    
    # Fallback for standard reverse proxies
    x_forwarded_for = request.headers.get('x-forwarded-for')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0]
    
    # Fallback for direct connection (local development)
    return request.client.host

def is_poll_expired(poll_data: dict) -> bool:
    duration_str = poll_data.get("expiry_duration")
    if not duration_str or duration_str == "never": return False
    created_at = poll_data.get("created_at")
    if not isinstance(created_at, datetime.datetime):
        created_at = datetime.datetime.fromisoformat(str(created_at))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    delta = None
    if duration_str.endswith('m'): delta = datetime.timedelta(minutes=int(duration_str[:-1]))
    elif duration_str.endswith('h'): delta = datetime.timedelta(hours=int(duration_str[:-1]))
    elif duration_str.endswith('d'): delta = datetime.timedelta(days=int(duration_str[:-1]))
    if delta: return datetime.datetime.now(datetime.timezone.utc) > created_at + delta
    return False

# --- (The rest of the file is unchanged) ---

# --- API Endpoints ---
@app.post("/api/polls", response_model=PollCreateResponse, status_code=status.HTTP_201_CREATED, tags=["Polls"])
async def create_poll(poll_data: PollCreate):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_id = str(uuid.uuid4())[:8]
    options_with_ids = [{"id": f"opt_{i+1}", "text": text} for i, text in enumerate(poll_data.options)]
    results = {option["id"]: 0 for option in options_with_ids}
    poll_record = {
        "id": poll_id, "question": poll_data.question, "options": options_with_ids,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
        "expiry_duration": poll_data.expiry, "results": results, "voter_ips": [],
        "quiz_mode": poll_data.quiz_mode, "results_revealed": not poll_data.quiz_mode,
        "host_secret": secrets.token_urlsafe(16) if poll_data.quiz_mode else None
    }
    try:
        db.collection('polls').document(poll_id).set(poll_record)
        return PollCreateResponse(id=poll_id, host_secret=poll_record["host_secret"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create poll: {e}")

@app.get("/api/polls/{poll_id}", response_model=PollData, tags=["Polls"])
async def get_poll_data(poll_id: str, request: Request, host_secret: Optional[str] = None):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_ref = db.collection('polls').document(poll_id)
    poll_doc = poll_ref.get()
    if not poll_doc.exists: raise HTTPException(status_code=404, detail="Poll not found")
    poll_data = poll_doc.to_dict()
    is_host = poll_data.get("quiz_mode") and poll_data.get("host_secret") == host_secret
    poll_data["is_expired"] = is_poll_expired(poll_data)
    client_ip = get_client_ip(request)
    if client_ip in poll_data.get("voter_ips", []):
        poll_data["user_voted"] = "yes"
    if poll_data.get("quiz_mode") and not poll_data.get("results_revealed") and not is_host:
        poll_data["results"] = {option["id"]: 0 for option in poll_data["options"]}
    return PollData(**poll_data)

@app.post("/api/polls/{poll_id}/vote", status_code=status.HTTP_200_OK, tags=["Polls"])
async def cast_vote(poll_id: str, vote: VoteRequest, request: Request):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_ref = db.collection('polls').document(poll_id)
    client_ip = get_client_ip(request)
    @firestore.transactional
    def vote_transaction(transaction, poll_ref, option_id, client_ip):
        snapshot = poll_ref.get(transaction=transaction)
        if not snapshot.exists: raise HTTPException(status_code=404, detail="Poll not found")
        poll_data = snapshot.to_dict()
        if is_poll_expired(poll_data): raise HTTPException(status_code=403, detail="This poll has expired.")
        if client_ip in poll_data.get("voter_ips", []): return {"message": "You have already voted."}
        if option_id not in poll_data.get("results", {}): raise HTTPException(status_code=400, detail="Invalid option ID")
        transaction.update(poll_ref, {f'results.{option_id}': firestore.Increment(1), 'voter_ips': firestore.ArrayUnion([client_ip])})
        return {"message": "Vote cast successfully."}
    transaction = db.transaction()
    return vote_transaction(transaction, poll_ref, vote.option_id, client_ip)

@app.post("/api/polls/{poll_id}/reveal", status_code=status.HTTP_200_OK, tags=["Host"])
async def reveal_results(poll_id: str, action: HostActionRequest):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_ref = db.collection('polls').document(poll_id)
    poll_doc = poll_ref.get()
    if not poll_doc.exists: raise HTTPException(status_code=404, detail="Poll not found")
    poll_data = poll_doc.to_dict()
    if not poll_data.get("quiz_mode"): raise HTTPException(status_code=400, detail="This is not a quiz mode poll.")
    if poll_data.get("host_secret") != action.host_secret: raise HTTPException(status_code=403, detail="Invalid host secret.")
    poll_ref.update({"results_revealed": True})
    return {"message": "Results have been revealed to participants."}

@app.get("/api/polls/{poll_id}/stream", tags=["Polls"])
async def stream_poll_results(poll_id: str, request: Request, host_secret: Optional[str] = None):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    queue = asyncio.Queue()
    def on_snapshot_callback(doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            if doc.exists: queue.put_nowait(doc.to_dict())
    poll_ref = db.collection("polls").document(poll_id)
    listener = poll_ref.on_snapshot(on_snapshot_callback)
    async def event_generator():
        try:
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    is_host = data.get("quiz_mode") and data.get("host_secret") == host_secret
                    data["is_expired"] = is_poll_expired(data)
                    if data.get("quiz_mode") and not data.get("results_revealed") and not is_host:
                        data["results"] = {option["id"]: 0 for option in data["options"]}
                    json_data = json.dumps(data, default=str)
                    yield f"data: {json_data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            listener.unsubscribe()
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/polls/{poll_id}/export", tags=["Polls"])
async def export_poll_results(poll_id: str):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_ref = db.collection('polls').document(poll_id)
    poll_doc = poll_ref.get()
    if not poll_doc.exists: raise HTTPException(status_code=404, detail="Poll not found")
    poll_data = poll_doc.to_dict()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Question', 'Option', 'Votes'])
    question = poll_data.get('question')
    results = poll_data.get('results', {})
    options_map = {opt['id']: opt['text'] for opt in poll_data.get('options', [])}
    for option_id, vote_count in results.items():
        writer.writerow([question, options_map.get(option_id, 'Unknown'), vote_count])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=poll_results_{poll_id}.csv"})

# --- Frontend Serving ---
@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_home(): return FileResponse(os.path.join(static_dir, 'index.html'))

@app.get("/polls/{poll_id}", response_class=HTMLResponse, tags=["Frontend"])
async def serve_poll_page(poll_id: str): return FileResponse(os.path.join(static_dir, 'poll.html'))

@app.get("/host/{poll_id}", response_class=HTMLResponse, tags=["Frontend"])
async def serve_host_page(poll_id: str): return FileResponse(os.path.join(static_dir, 'host.html'))
