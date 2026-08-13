from fastapi.testclient import TestClient
from app.api.context_config import router

print("Routes in context_config router:")
for r in router.routes:
    print(r.path, r.methods)
