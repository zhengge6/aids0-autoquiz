"""便捷入口：python run.py <子命令>。等价于 python -m app.cli <子命令>。"""
import sys
from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
