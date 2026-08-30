"""安装/校验出网网关语义消歧模型（bge-small-zh-v1.5）。

消费者面向：把本地小模型装好、开箱即用。用法：

    python scripts/install_gateway_model.py            # 自动下载到 models/gateway-semantic/
    python scripts/install_gateway_model.py --onnx 你的model.onnx   # 附带一个已导出的 ONNX

说明：
- 本脚本从 HuggingFace（BAAI/bge-small-zh-v1.5）下载 tokenizer 配置文件；
- ONNX 模型官方仓库默认不带，需自行导出或提供（见脚本内的导出指引）；
- 网络不通时会给出人话提示与「手动放置」路径，不会报裸堆栈；
- 装好后把 compliance_gateway.semantic.enabled 置 true 即启用语义消歧。

仅标准库 + requests（已在 requirements）；MIT。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import requests
except Exception:
    requests = None

REPO = "BAAI/bge-small-zh-v1.5"
HF_BASE = f"https://huggingface.co/{REPO}/resolve/main"

# tokenizer / 模型元数据（官方仓库有）
DOWNLOAD_FILES = ["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt"]

# 项目根 = 本文件(scripts/install_gateway_model.py) 向上两级
_ROOT = Path(__file__).resolve().parents[1]
_TARGET = _ROOT / "models" / "gateway-semantic" / "bge-small-zh-v1.5"


def _download(file: str, out_dir: Path, timeout: int = 30) -> bool:
    if requests is None:
        print("[!] 缺少 requests，请先 pip install requests")
        return False
    url = f"{HF_BASE}/{file}"
    dst = out_dir / file
    print(f"      下载 {url}")
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            print(f"       [跳过] HTTP {r.status_code}（该文件可能不存在于仓库）")
            return False
        dst.write_bytes(r.content)
        return True
    except requests.RequestException as exc:
        print(f"       [失败] 无法连接 HuggingFace：{exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="安装网关语义消歧模型")
    ap.add_argument("--onnx", help="可选：一个已导出的 model.onnx 路径，拷贝进模型目录")
    ap.add_argument("--dir", default=str(_TARGET), help="模型目录（默认 models/gateway-semantic/bge-small-zh-v1.5）")
    args = ap.parse_args()

    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"目标目录：{out_dir}")

    print("1) 下载 tokenizer / 配置文件（HuggingFace）")
    ok_any = False
    for f in DOWNLOAD_FILES:
        ok_any |= _download(f, out_dir)

    print("2) 处理 ONNX 模型")
    model_path = out_dir / "model.onnx"
    if args.onnx:
        src = Path(args.onnx)
        if src.is_file():
            model_path.write_bytes(src.read_bytes())
            print(f"      已从 {src} 复制 model.onnx")
        else:
            print(f"      [失败] 找不到 {src}")
    elif model_path.is_file():
        print("      已存在 model.onnx，复用")
    else:
        print("      [提示] 官方仓库默认不含 ONNX，需要你提供或导出。两种方式：")
        print("        a) 从社区/自己导出后执行： python scripts/install_gateway_model.py --onnx <path>")
        print("        b) 用 optimum 导出（按需安装，体积大）：")
        print("            pip install optimum[onnxruntime] transformers")
        print("            optimum-cli export onnx --model BAAI/bge-small-zh-v1.5 <outdir>")
        print("            （导出后把 model.onnx 放到上面目录，再执行本脚本校验）")

    print("3) 校验")
    if model_path.is_file() and (out_dir / "tokenizer.json").is_file():
        print("[OK] 模型与 tokenizer 就绪。把 compliance_gateway.semantic.enabled 置 true 即启用语义消歧。")
        return 0
    missing = [str(p.name) for p in (model_path, out_dir / "tokenizer.json") if not p.is_file()]
    print(f"[!] 仍缺：{', '.join(missing)}。装齐前网关会用规则（allow_words）兜底，不影响正常使用。")
    print("     若网络不通，可手动把 HuggingFace(BAAI/bge-small-zh-v1.5) 的文件放入：", out_dir)
    return 1


if __name__ == "__main__":
    sys.exit(main())
