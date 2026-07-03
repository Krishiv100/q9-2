import time
import uuid
import base64
from collections import defaultdict, deque
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Assignment Constraints
T = 42
RATE_LIMIT = 18
WINDOW = 10

app = FastAPI()

# Standard CORS Middleware for normal 200/201 responses
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"], 
)

idempotency_store = {}
client_requests = defaultdict(deque)

# Pre-generate the fixed catalog of IDs 1 through T (42)
orders_catalog = [
    {"id": i, "item": f"order-{i}", "amount": float(i * 10)}
    for i in range(1, T + 1)
]

class OrderIn(BaseModel):
    item: str | None = "sample"

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # 1. Browsers send OPTIONS requests automatically. 
    # Do not count these against the client's rate limit.
    if request.method == "OPTIONS":
        return await call_next(request)

    # 2. Read X-Client-Id precisely as required by the grader
    client_id = request.headers.get("X-Client-Id", "unknown")

    now = time.monotonic()
    bucket = client_requests[client_id]

    # Remove requests older than 10 seconds
    while bucket and now - bucket[0] >= WINDOW:
        bucket.popleft()

    # 3. Check bucket size. If 18 requests are already in the 10s window, reject the 19th.
    if len(bucket) >= RATE_LIMIT:
        retry_after = max(1, int(WINDOW - (now - bucket[0])))
        
        # CRITICAL: Because we are returning directly from middleware, 
        # CORSMiddleware is bypassed. We MUST inject CORS headers manually here.
        return Response(
            content='{"detail":"rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={
                "Retry-After": str(retry_after),
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "Retry-After"
            },
        )

    # Add current request to the bucket
    bucket.append(now)
    return await call_next(request)

@app.post("/orders", status_code=201)
def create_order(
    body: OrderIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # Idempotent POST requirement
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")

    # If key exists, return the exact same object (do not create duplicate)
    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    order = {
        "id": str(uuid.uuid4()),
        "item": body.item,
        "created": True,
    }

    idempotency_store[idempotency_key] = order
    return order


# Helper functions to handle opaque Base64 cursors safely
def encode_cursor(index: int) -> str:
    return base64.urlsafe_b64encode(str(index).encode()).decode()

def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        # Add required padding to prevent base64 decode errors
        padded = cursor + "=" * (-len(cursor) % 4)
        return int(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


@app.get("/orders")
def list_orders(limit: int = 10, cursor: str | None = None):
    # Cursor pagination requirement
    limit = max(1, limit)  # Prevent zero or negative limits
    
    start_index = decode_cursor(cursor)
    end_index = min(start_index + limit, T)

    # Slice the catalog exactly
    items = orders_catalog[start_index:end_index]
    
    # Only return a next_cursor if we haven't hit the total T (42) yet
    next_cursor = encode_cursor(end_index) if end_index < T else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }

@app.get("/")
def root():
    return {"status": "Orders API Online"}