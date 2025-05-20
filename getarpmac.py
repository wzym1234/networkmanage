import os
import pymysql
from pymysql.cursors import DictCursor
from concurrent.futures import ThreadPoolExecutor
from device_type_detection import detect_device_type
from h3c_device_processing import h3c_device_processing
from huawei_device_processing import huawei_device_processing
from utils import write_to_log, db_config, base_device, write_to_collection_log
import logging

# 配置日志
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def validate_device_params(device_params):
    """验证设备参数是否完整"""
    required_keys = ['ip', 'user', 'pass']
    if not all(key in device_params and device_params[key] for key in required_keys):
        return False, f"设备参数不完整: {device_params}"
    if not isinstance(device_params['ip'], str) or not device_params['ip'].strip():
        return False, f"无效的IP地址: {device_params['ip']}"
    return True, ""

def process_single_device(device_params):
    """处理单个设备"""
     # 清空 collection.log 文件

    try:
        # 验证设备参数
        is_valid, error_msg = validate_device_params(device_params)
        if not is_valid:
            logging.error(error_msg)
            raise ValueError(error_msg)

        full_device = {
            **base_device,
            "ip": device_params["ip"],
            "username": device_params["user"],
            "password": device_params["pass"],
            "device_type": "autodetect"
        }
        
        logging.info(f"开始处理设备: [IP: {device_params['ip']}] [用户名: {device_params['user']}]")
        
        # 检测设备类型
        device_info = detect_device_type(full_device, device_params["ip"])
        driver = device_info["driver"]
        device_type = device_info["type"]
        device_name = device_info.get("name", "未知")
        
        log_message = f"[设备IP: {device_params['ip']}][设备名称: {device_name}] 类型识别为：{device_type.upper()}（驱动={driver}）"
        write_to_collection_log(log_message)
        print(log_message)
        logging.info(log_message)
        write_to_log(device_params['ip'], log_message)
        
        full_device["device_type"] = driver
        
        if device_type == "h3c":
            h3c_device_processing(full_device, device_name)
        elif device_type == "huawei":
            huawei_device_processing(full_device, device_name)
        else:
            error_msg = f"[设备IP: {device_params['ip']}][设备名称: {device_name}] 不支持的设备类型: {device_type}，仅支持华为/华三设备"
            write_to_collection_log(error_msg)
            logging.error(error_msg)
            write_to_log(device_params['ip'], error_msg)
            raise ValueError(error_msg)
        print(f"[设备IP: {device_params['ip']}][设备名称: {device_name}] 采集完成")
        write_to_collection_log(f"[设备IP: {device_params['ip']}][设备名称: {device_name}] 采集完成")
        logging.info(f"[设备IP: {device_params['ip']}][设备名称: {device_name}] 采集完成")
    
    except ValueError as e:
        error_msg = f"[设备IP: {device_params.get('ip', '未知')}][设备名称: 未知] 类型检测失败: {str(e)}"
        print(error_msg)
        logging.error(error_msg)
        write_to_log(device_params.get('ip', '未知'), error_msg)
        write_to_collection_log(device_params.get('ip', '未知'), error_msg)
        raise
    except Exception as e:
        error_msg = f"[设备IP: {device_params.get('ip', '未知')}][设备名称: 未知] 处理失败: {str(e)}"
        print(error_msg)
        logging.error(error_msg)
        write_to_log(device_params.get('ip', '未知'), error_msg)
        write_to_collection_log(device_params.get('ip', '未知'), error_msg)
        raise

def main():
    
    # 清空 collection.log 文件
    with open('collection.log', 'w', encoding='utf-8') as f:
        f.write('')
        
    """主函数，处理所有设备"""
    os.makedirs("logs", exist_ok=True)
    result = {
        "success": False,
        "message": "",
        "failed_devices": [],
        "device_list": []
    }
    
    try:
        logging.info("开始全网采集")
        print("开始全网采集")
        write_to_collection_log("开始全网采集")
        # 使用DictCursor确保查询结果为字典
        with pymysql.connect(**db_config, cursorclass=DictCursor) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT ip, user, pass FROM devinfo")
                rows = cursor.fetchall()
                logging.info(f"从devinfo表查询到 {len(rows)} 条记录")
                device_list = [
                    {"ip": row["ip"], "user": row["user"], "pass": row["pass"]}
                    for row in rows
                    if row["ip"] and row["user"] and row["pass"]
                ]
        
        if not device_list:
            result["message"] = "数据库中没有有效的设备信息"
            logging.warning(result["message"])
            return result
        
        log_message = f"成功读取 {len(device_list)} 台有效设备信息"
        print(log_message)
        write_to_collection_log(log_message)
        logging.info(log_message)
        
        # 并行处理设备
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for device in device_list:
                futures.append(executor.submit(process_single_device, device))
            
            for future, device in zip(futures, device_list):
                try:
                    future.result()
                    logging.info(f"[设备IP: {device['ip']}] 采集成功")
                    print(f"[设备IP: {device['ip']}] 采集成功")
                    write_to_collection_log(f"[设备IP: {device['ip']}] 采集成功")
                except Exception as e:
                    error_msg = f"[设备IP: {device['ip']}] 采集失败: {str(e)}"
                    print(error_msg)
                    write_to_collection_log(error_msg)
                    logging.error(error_msg)
                    write_to_log(device['ip'], error_msg)
                    result["failed_devices"].append({"ip": device['ip'], "error": str(e)})
        
        success_count = len(device_list) - len(result["failed_devices"])
        result["device_list"] = device_list
        if success_count > 0:
            result["success"] = True
            result["message"] = f"全网采集完成，成功处理 {success_count} 台设备"
        else:
            result["message"] = "全网采集失败，所有设备处理失败"
        
        if result["failed_devices"]:
            result["message"] += f"\n失败设备：\n" + "\n".join(
                [f"IP {d['ip']}: {d['error']}" for d in result["failed_devices"]]
            )
        print(result["message"])
        write_to_collection_log(result["message"])
        logging.info(result["message"])
        return result
    
    except Exception as e:
        error_msg = f"全网采集失败: {str(e)}"
        write_to_collection_log(log_message)
        print(error_msg)
        logging.error(error_msg)
        write_to_log("global", error_msg)
        result["message"] = error_msg
        return result