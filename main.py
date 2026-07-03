import time, uuid, base64
from collections import defaultdict, deque
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from urllib.parse import unquote

T = 42
RATE_LIMIT = 18
WINDOW = 10

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

idempotency_store = {}
client_requests = defaultdict(deque)

orders_catalog = [
    {"id": i, "item": f"order-{i}", "amount": float(i * 10)}
    for i in range(1, T + 1)
]


class OrderIn(BaseModel):
    item: str | None = "sample"


@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "default")
    now = time.time()
    bucket = client_requests[client_id]

    while bucket and now - bucket[0] >= WINDOW:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        retry_after = max(1, int(WINDOW - (now - bucket[0])))
        return Response(
            content='{"detail":"rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )

    bucket.append(now)
    return await call_next(request)


@app.get("/")
def root():
    return {"status": "ok", "message": "Orders API running"}


@app.post("/orders", status_code=201)
def create_order(
    body: OrderIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")

    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    order = {
        "id": str(uuid.uuid4()),
        "item": body.item,
        "created": True,
    }

    idempotency_store[idempotency_key] = order
    return order


def encode_cursor(index: int) -> str:
    return base64.urlsafe_b64encode(str(index).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        cursor = unquote(cursor)
        padding = "=" * (-len(cursor) % 4)
        return int(base64.urlsafe_b64decode((cursor + padding).encode()).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")

@app.options("/orders")
def options_orders():
    return Response(status_code=204)

@app.get("/orders")
def list_orders(limit: int = 10, cursor: str | None = None):
    limit = max(1, min(limit, 100))
    start = decode_cursor(cursor)
    end = min(start + limit, T)

    items = orders_catalog[start:end]
    next_cursor = encode_cursor(end) if end < T else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}