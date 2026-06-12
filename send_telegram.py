"""Send daily screener results to Telegram."""
import json
import os
import glob
import requests
from datetime import datetime


def get_latest_results():
    """Get the latest results JSON file."""
    results_dir = "results"
    if not os.path.exists(results_dir):
        print(f"Results directory '{results_dir}' not found.")
        return None

    json_files = glob.glob(os.path.join(results_dir, "final_*.json"))
    if not json_files:
        print("No final result files found.")
        return None

    latest = max(json_files, key=os.path.getmtime)
    with open(latest, 'r', encoding='utf-8') as f:
        return json.load(f)


def format_message(data):
    """Format results into a Telegram message."""
    if not data:
        return "⚠️ 今日扫描无结果"

    final_list = data.get("final_list", [])
    if not final_list:
        return "⚠️ 今日未选出股票"

    # Build summary
    lines = [
        "📈 **A股主板升势筛选 - " + datetime.now().strftime("%Y-%m-%d") + " 8:00",
        "",
        "📊 **最终候选池** (" + str(len(final_list)) + "只):",
        "-" * 30
    ]

    for i, item in enumerate(final_list[:10], 1):  # Send max 10 items
        code = item.get("code", "")
        name = item.get("name", "")
        sector = item.get("sector", "")
        score = item.get("probability_score", "")
        price = item.get("current_price", "")
        entry = item.get("entry_price", "")
        stop = item.get("stop_loss_price", "")
        target = item.get("target_price_3d", "")

        lines.append(f"**{i}. {code} {name}**")
        lines.append(f"   板块: {sector}")
        lines.append(f"   置信度: {item.get('probability_label', '')}({score})")
        lines.append(f"   当前价: {price} | 买入观察: {entry} | 止损: {stop} | 3日目标: {target}")

        watch = item.get("watch_3d", "")
        if watch:
            lines.append(f"   观察要点: {watch}")
        lines.append("")

    # Risk warning
    disclaimer = data.get("disclaimer", "仅供参考，不构成投资建议。")
    lines.append("⚠️ " + disclaimer)
    lines.append("")
    lines.append("🤖 由 A股主板升势筛选系统自动生成")

    return "\n".join(lines)


def send_to_telegram(message):
    """Send message to Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("Telegram credentials not configured.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            print("Message sent to Telegram successfully!")
            return True
        else:
            print(f"Failed to send: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Error sending message: {e}")
        return False


def main():
    results = get_latest_results()
    message = format_message(results)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
