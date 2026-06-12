from config import load_config
from pipeline import run_pipeline


def main():
    cfg = load_config()
    if not cfg.get("openai_api_key"):
        raise SystemExit("未配置 openai_api_key，请先运行 GUI 或编辑 config.json。")
    
    result = run_pipeline(cfg)
    
    # Handle dictionary output from run_pipeline
    if isinstance(result, dict):
        print("=" * 60)
        print("【运行结果】")
        print("=" * 60)
        
        final_list = result.get("final_list", [])
        if final_list:
            for i, stock in enumerate(final_list, 1):
                print(f"\n--- 标的 {i} ---")
                print(f"代码: {stock.get('code', 'N/A')}")
                print(f"名称: {stock.get('name', 'N/A')}")
                print(f"板块: {stock.get('sector', 'N/A')}")
                print(f"逻辑: {stock.get('reason', 'N/A')}")
                print(f"概率: {stock.get('probability_label', 'N/A')} (得分: {stock.get('probability_score', 'N/A')})")
                print(f"买入: {stock.get('entry_price', 'N/A')}  止损: {stock.get('stop_loss_price', 'N/A')}  目标: {stock.get('target_price_3d', 'N/A')}")
        else:
            print("未筛选到符合条件的标的。")
            
        if result.get("disclaimer"):
            print(f"\n免责声明: {result['disclaimer']}")
    else:
        # Fallback for generator output (old format)
        for msg, _ in result:
            print(msg)


if __name__ == "__main__":
    main()
