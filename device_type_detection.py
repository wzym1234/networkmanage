import re
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
#from utils import write_to_log, db_config
from modules.utils import write_to_log, db_config
from utils import write_to_collection_log
import pymysql
from pymysql.cursors import DictCursor

def detect_device_type(device, device_ip):
    # Check if devtype exists in devinfo table
    try:
        with pymysql.connect(**db_config, cursorclass=DictCursor) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT devtype, devname FROM devinfo WHERE ip = %s", (device_ip,))
                result = cursor.fetchone()
                if result and result['devtype']:
                    driver = "hp_comware" if result['devtype'] == "H3C" else "huawei"
                    return {
                        "driver": driver,
                        "type": result['devtype'].lower(),
                        "name": result['devname'] or "未知"
                    }
    except pymysql.MySQLError as e:
        log_message = f"[设备IP: {device_ip}][设备名称: 未知] 数据库查询失败: {str(e)}"
        print(log_message)
        write_to_collection_log(log_message)
        write_to_log(device_ip, log_message)

    test_drivers = ["hp_comware", "huawei", "autodetect"]
    
    for driver in test_drivers:
        temp_device = {**device, "device_type": driver}
        try:
            with ConnectHandler(**temp_device, session_log="huawei_session.log") as conn:
                version_output = conn.send_command("display version", read_timeout=10)
                if re.search(r'H3C|Comware', version_output, re.IGNORECASE):
                    return {"driver": "hp_comware", "type": "h3c", "name": "未知"}
                elif re.search(r'HUAWEI|VRP', version_output, re.IGNORECASE):
                    return {"driver": "huawei", "type": "huawei", "name": "未知"}
        
        except NetmikoTimeoutException:
            continue
        except NetmikoAuthenticationException:
            log_message = f"[设备IP: {device_ip}][设备名称: 未知] 认证失败，请检查用户名/密码"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
            raise ValueError("认证失败，请检查用户名/密码")
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: 未知] 检测驱动{driver}时出错: {str(e)}"
            write_to_log(log_message)
            print(log_message)
            write_to_log(device_ip, log_message)
    
    raise ValueError("不支持的设备类型，仅支持华为/华三设备")