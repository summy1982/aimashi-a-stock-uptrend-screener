# 1) 创建虚拟环境（可选）
python -m venv .venv
.\.venv\Scripts\activate

# 2) 安装依赖
pip install -r requirements.txt

# 3) 直接运行GUI
python main.py

# 4) 打包为exe（单文件，带控制台可选改为 --noconsole）
pyinstaller --noconfirm --onefile --windowed --name AimashiAStock main.py
