import os
import re
from datetime import datetime, timedelta, time
import logging

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

# 日志记录函数
def write_to_log(device_ip, message):
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{device_ip}.log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

def is_valid_ip(ip):
    """验证 IP 地址格式"""
    if not ip or not isinstance(ip, str):
        return False
    pattern = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    return bool(re.match(pattern, ip))

def convert_to_time(value):
    """将 timedelta 或其他格式转换为 datetime.time"""
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return time(hour=hours, minute=minutes, second=seconds)
    elif isinstance(value, time):
        return value
    elif isinstance(value, str):
        try:
            return datetime.strptime(value, '%H:%M:%S').time()
        except ValueError:
            logging.error(f"无效的时间格式: {value}")
            raise ValueError(f"无效的时间格式: {value}")
    else:
        logging.error(f"不支持的时间类型: {type(value)}")
        raise ValueError(f"不支持的时间类型: {type(value)}")

def calculate_next_execution(task):
    """计算下一次执行时间"""
    try:
        now = datetime.now()
        start_date = task['start_date']
        end_date = task['end_date']
        execution_time = convert_to_time(task['execution_time'])
        frequency = task['frequency']

        # 验证输入
        if not all([start_date, end_date, execution_time, frequency]):
            logging.error(f"任务参数不完整: {task}")
            return None

        today = now.date()
        exec_hour, exec_minute = execution_time.hour, execution_time.minute

        if frequency == 'daily':
            # 下一次执行是今天或明天
            next_exec = datetime.combine(today, execution_time)
            if next_exec <= now:
                next_exec += timedelta(days=1)
            if next_exec.date() > end_date:
                logging.info(f"任务超出结束日期: {end_date}")
                return None
            if next_exec.date() < start_date:
                next_exec = datetime.combine(start_date, execution_time)
            return next_exec

        elif frequency == 'weekly':
            # 下一次执行是本周的周一
            days_to_monday = (7 - today.weekday()) % 7 or 7
            next_monday = today + timedelta(days=days_to_monday)
            next_exec = datetime.combine(next_monday, execution_time)
            if next_exec <= now:
                next_exec += timedelta(days=7)
            if next_exec.date() > end_date:
                logging.info(f"任务超出结束日期: {end_date}")
                return None
            if next_exec.date() < start_date:
                next_exec = datetime.combine(start_date + timedelta(days=(7 - start_date.weekday()) % 7), execution_time)
            return next_exec

        elif frequency == 'monthly':
            # 下一次执行是本月或下月1号
            next_month = today.replace(day=1) + timedelta(days=32)
            next_month = next_month.replace(day=1)
            next_exec = datetime.combine(next_month, execution_time)
            if next_exec <= now:
                next_month = next_month.replace(day=1) + timedelta(days=32)
                next_month = next_month.replace(day=1)
                next_exec = datetime.combine(next_month, execution_time)
            if next_exec.date() > end_date:
                logging.info(f"任务超出结束日期: {end_date}")
                return None
            if next_exec.date() < start_date:
                next_exec = datetime.combine(start_date.replace(day=1), execution_time)
            return next_exec

        logging.error(f"无效的频率: {frequency}")
        return None

    except Exception as e:
        logging.error(f"计算下一次执行时间失败: {str(e)}")
        return None