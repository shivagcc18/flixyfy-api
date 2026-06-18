from fastapi import FastAPI
import redis
from config.settings import REDIS_HOST, REDIS_PORT, QUEUE_NAME

app = FastAPI()
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)


@app.get("/")
def home():
    return {"status": "WATCHINDIA V2 RUNNING"}


@app.get("/queue")
def queue():
    return {"queue_size": r.llen(QUEUE_NAME)}


@app.get("/health")
def health():
    return {"ok": True}