import os
from datetime import datetime
import threading
# 公共数据库配置
db_config = {
    "host": "127.0.0.1",
    "user": "admin",
    "password": "admin1234",
    "database": "network_monitor",
    "port": 3306,
    "charset": "utf8mb4"
}

# 公共设备连接参数
base_device = {
    "port": 22,
    "conn_timeout": 15,
    "timeout": 30,
    "global_delay_factor": 5
}
# 用于保护 collection.log 写入的线程锁
_collection_log_lock = threading.Lock()
# 日志记录函数
def write_to_log(device_ip, message):
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{device_ip}.log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")
        
def write_to_collection_log(message):
    """以追加模式写入 collection.log，并使用线程同步"""
    with _collection_log_lock:
        with open('collection.log', 'a', encoding='utf-8') as f:
            f.write(f"{message}\n")