import asyncio

from app.infrastructure.worker import run_worker
from app.logging_config import configure_logging

if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_worker())
