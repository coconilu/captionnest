from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 SubLingo Local 本地服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--data-dir", type=Path, help="应用数据目录，默认 ./data")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.data_dir:
        import os

        os.environ["SUBLINGO_DATA_DIR"] = str(args.data_dir.resolve())
    uvicorn.run(
        "sublingo_local.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

