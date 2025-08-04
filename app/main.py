import os
import uuid
import datetime
import asyncio
import json
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from threading import Event

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

class PollData(PollResponse):
    results: Dict[str, int]
    user_voted: Optional[str] = None

class VoteRequest(BaseModel):
    option_id: str


# --- Helper Functions ---
def get_client_ip(request: Request) -> str:
    x_forwarded_for = request.headers.get('x-forwarded-for')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0]
    return request.client.host

# --- API Endpoints ---

@app.post("/api/polls", response_model=PollResponse, status_code=status.HTTP_201_CREATED, tags=["Polls"])
async def create_poll(poll_data: PollCreate):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_id = str(uuid.uuid4())[:8]
    options_with_ids = [{"id": f"opt_{i+1}", "text": text} for i, text in enumerate(poll_data.options)]
    results = {option["id"]: 0 for option in options_with_ids}
    poll_record = {
        "id": poll_id, "question": poll_data.question, "options": options_with_ids,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
        "expiry_duration": poll_data.expiry, "results": results, "voter_ips": []
    }
    try:
        db.collection('polls').document(poll_id).set(poll_record)
        return PollResponse(**poll_record)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create poll: {e}")

@app.get("/api/polls/{poll_id}", response_model=PollData, tags=["Polls"])
async def get_poll_data(poll_id: str, request: Request):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_ref = db.collection('polls').document(poll_id)
    poll_doc = poll_ref.get()
    if not poll_doc.exists: raise HTTPException(status_code=404, detail="Poll not found")
    poll_data = poll_doc.to_dict()
    client_ip = get_client_ip(request)
    if client_ip in poll_data.get("voter_ips", []):
        poll_data["user_voted"] = "yes"
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
        if client_ip in poll_data.get("voter_ips", []): return {"message": "You have already voted."}
        if option_id not in poll_data.get("results", {}): raise HTTPException(status_code=400, detail="Invalid option ID")
        transaction.update(poll_ref, {
            f'results.{option_id}': firestore.Increment(1),
            'voter_ips': firestore.ArrayUnion([client_ip])
        })
        return {"message": "Vote cast successfully."}
    transaction = db.transaction()
    return vote_transaction(transaction, poll_ref, vote.option_id, client_ip)

# --- NEW: Real-time SSE Endpoint ---
@app.get("/api/polls/{poll_id}/stream", tags=["Polls"])
async def stream_poll_results(poll_id: str, request: Request):
    """Streams poll results in real-time using Server-Sent Events."""
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")

    # Using an asyncio.Queue to bridge the threaded Firestore listener and the async SSE generator
    queue = asyncio.Queue()
    
    # This Event is used to signal the listener thread to stop
    stop_event = Event()

    def on_snapshot_callback(doc_snapshot, changes, read_time):
        """Callback function for Firestore listener."""
        for doc in doc_snapshot:
            if doc.exists:
                # Put the new data into the queue for the SSE stream to send
                queue.put_nowait(doc.to_dict())

    # Start the Firestore listener in a separate thread
    poll_ref = db.collection("polls").document(poll_id)
    listener = poll_ref.on_snapshot(on_snapshot_callback)

    async def event_generator():
        """Generator function that yields SSE messages."""
        try:
            while not await request.is_disconnected():
                try:
                    # Wait for new data from the queue
                    data = await asyncio.wait_for(queue.get(), timeout=30) 
                    # Convert dict to JSON string for SSE
                    # The 'default=str' handles datetime objects
                    json_data = json.dumps(data, default=str)
                    yield f"data: {json_data}\n\n"
                except asyncio.TimeoutError:
                    # Send a comment to keep the connection alive
                    yield ": keep-alive\n\n"
        finally:
            # When the client disconnects, stop the listener
            print(f"Client disconnected from poll {poll_id}. Cleaning up listener.")
            listener.unsubscribe()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/health", tags=["Status"])
async def health_check():
    return {"status": "ok", "message": "API is running smoothly"}

# --- Frontend Serving ---
@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_home():
    return FileResponse(os.path.join(static_dir, 'index.html'))

@app.get("/polls/{poll_id}", response_class=HTMLResponse, tags=["Frontend"])
async def serve_poll_page(poll_id: str):
    return FileResponse(os.path.join(static_dir, 'poll.html'))