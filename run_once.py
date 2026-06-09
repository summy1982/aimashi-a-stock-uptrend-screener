from config import load_config
from pipeline import run_pipeline


def main():
    cfg = load_config()
    if not cfg.get("openai_api_key"):
        raise SystemExit("未配置 openai_api_key，请先运行 GUI 或编辑 config.json。")
    for msg, _ in run_pipeline(cfg):
        print(msg)


if __name__ == "__main__":
    main()
