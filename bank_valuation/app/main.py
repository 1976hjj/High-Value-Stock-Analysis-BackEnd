import logging
import os
from pathlib import Path
from fastapi import FastAPI
from .routers.bank import router as bank_router
from .routers.strategy import router as strategy_router


def configure_logging() -> None:
    """Write useful runtime diagnostics both to the console and a rotating file."""
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_dir = Path(os.getenv("BANK_VALUATION_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(log_dir / "bank_valuation.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger = logging.getLogger("bank_valuation")
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.propagate = False


configure_logging()

app = FastAPI(
    title="A股多行业价值与危机防御后端",
    version="0.2.0",
    description="银行深度估值、行业分析与跨行业防御策略服务；不构成投资建议。",
)
app.include_router(bank_router)
app.include_router(strategy_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
