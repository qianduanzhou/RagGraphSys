import argparse
import os
import subprocess
import sys
from pathlib import Path


def read_dotenv(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"未找到配置文件 {path}，请先复制 .env.example 为 .env 并填写镜像仓库配置。")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必填配置：{name}")
    return value


def run(command: list[str], *, input_text: str | None = None) -> None:
    subprocess.run(command, input=input_text, text=True, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建并推送后端 Docker 镜像")
    parser.add_argument("--env-file", default=".env", help="环境变量文件，默认 .env")
    parser.add_argument("--no-push", action="store_true", help="只构建镜像，不登录和推送")
    args = parser.parse_args()

    read_dotenv(Path(args.env_file))

    image_push_address = require_env("IMAGE_PUSH_ADDRESS").rstrip("/")
    image_name = require_env("IMAGE_NAME").strip("/")
    image_tag = require_env("IMAGE_TAG")
    image = f"{image_push_address}/{image_name}:{image_tag}"

    print(f"构建后端镜像：{image}", flush=True)
    run(["docker", "build", "-t", image, "."])

    if args.no_push:
        print(f"后端镜像已构建：{image}", flush=True)
        return 0

    registry_address = require_env("REGISTRY_ADDRESS")
    registry_username = require_env("REGISTRY_USERNAME")
    registry_password = require_env("REGISTRY_PASSWORD")

    logged_in = False
    try:
        print(f"登录镜像仓库：{registry_address}", flush=True)
        run(
            ["docker", "login", registry_address, "--username", registry_username, "--password-stdin"],
            input_text=registry_password,
        )
        logged_in = True

        print(f"推送后端镜像：{image}", flush=True)
        run(["docker", "push", image])

        print(f"后端镜像已推送：{image}", flush=True)
        return 0
    finally:
        if logged_in:
            print(f"退出镜像仓库登录：{registry_address}", flush=True)
            subprocess.run(["docker", "logout", registry_address], check=False)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)


