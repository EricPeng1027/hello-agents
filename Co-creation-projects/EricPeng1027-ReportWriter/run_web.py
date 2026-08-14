# -*- coding: utf-8 -*-
"""ReportWriter Web UI 启动入口

用法: python run_web.py → http://127.0.0.1:8000
（绑定回环地址，不暴露到局域网；reload=False 避免双进程重复初始化 LLM 单例）
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.server:app", host="127.0.0.1", port=8000, reload=False)
