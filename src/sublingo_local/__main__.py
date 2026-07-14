from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 CaptionNest 本地服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--data-dir", type=Path, help="应用数据目录，默认 ./data")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    return parser


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().removeprefix("[").removesuffix("]")
    if normalized.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not _is_loopback_host(args.host) and not os.getenv(
        "CAPTIONNEST_SESSION_TOKEN", ""
    ).strip():
        parser.error("监听非回环地址时必须设置 CAPTIONNEST_SESSION_TOKEN")
    if args.data_dir:
        os.environ["CAPTIONNEST_DATA_DIR"] = str(args.data_dir.resolve())
    uvicorn.run(
        "sublingo_local.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
