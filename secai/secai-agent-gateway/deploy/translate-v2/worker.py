"""Start only the isolated translation worker, with private transient stores."""
import asyncio
import os

os.environ["SESSION_STORE_BACKEND"] = "memory"
os.environ["REDIS_URL"] = ""
os.environ["GATEWAY_DB_PATH"] = os.environ.get(
    "TRANSLATE_V2_WORKER_DB_PATH", "./data/translate-v2/worker-gateway.sqlite3"
)
os.environ["POC_CATALOG_DB"] = os.environ.get(
    "TRANSLATE_V2_WORKER_POC_DB_PATH", "./data/translate-v2/worker-poc.sqlite3"
)

from secai_gateway.translation.worker import main  # noqa: E402

asyncio.run(main())
