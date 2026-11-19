# -*- coding: utf-8 -*-
"""启动入口：python run.py"""

import os

import uvicorn

from app import config

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=os.environ.get("DYER_RELOAD") == "1",
        log_level="info",
    )
