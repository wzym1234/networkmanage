import re
from datetime import datetime
import pymysql
from netmiko import ConnectHandler
from utils import write_to_log, db_config, write_to_collection_log

def h3c_device_processing(full_device, device_name="未知"):
    current_device_ip = full_device.get('ip', '未知IP')
    sysname = device_name
    model = '未知型号'
    
    def normalize_port_name(port):
        """Normalize port name to abbreviated format (e.g., GE1/0/1, XGE1/0/1)"""
        port = re.sub(r'GigabitEthernet', 'GE', port, flags=re.IGNORECASE)
        port = re.sub(r'Ten-GigabitEthernet', 'XGE', port, flags=re.IGNORECASE)
        return port

    def get_trunk_ports(conn, model):
        trunk_ports = []
        try:
            trunk_output = conn.send_command("display port trunk")
            trunk_pattern = re.compile(r'^\s*(GE|GigabitEthernet|XGE|Ten-GigabitEthernet)(\d+(/\d+)*)\s+', re.MULTILINE)
            for match in trunk_pattern.finditer(trunk_output):
                port_type = match.group(1)
                port_num = match.group(2)
                if model.upper().startswith(("S5130","MSR36")):
                    full_port = f"{port_type}{port_num}"
                else:
                    full_port = f"GigabitEthernet{port_num}" if port_type in ("GE", "GigabitEthernet") else f"Ten-GigabitEthernet{port_num}"
                normalized_port = normalize_port_name(full_port)
                trunk_ports.append(normalized_port)
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 检测到trunk口: {trunk_ports}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 获取trunk口失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
        return trunk_ports

    def parse_current_config(output):
        sysname_match = re.search(r'^\s*sysname\s+(\S+)\s*$', output, re.MULTILINE)
        return sysname_match.group(1).strip() if sysname_match else "无"

    def parse_version_output(output):
        model_match = re.search(r'BOARD\s+TYPE\s*[:：]\s*(\S+)', output, re.IGNORECASE)
        if not model_match:
            model_match = re.search(r'H3C\s+(\S+?)\s+uptime', output)
        return model_match.group(1) if model_match else "无"

    def parse_arp_output(output):
        entries = []
        lines = output.strip().split('\n')
        data_start = False
        for line in lines:
            if "IP address" in line and "MAC address" in line and "VLAN/VSI name" in line:
                data_start = True
                continue
            if "IP Address" in line and "MAC Address" in line and "VLAN ID  Interface" in line and "Aging Type" in line:
                data_start = True
                continue
            if "IP address" in line and "MAC address" in line and "SVLAN/VSI" in line and "Aging Type" in line:
                data_start = True
                continue
            if "IP address" in line and "MAC address" in line and "VLAN/VSI" in line and "Aging Type" in line:
                data_start = True
                continue
            if data_start and line.strip():
                match = re.match(r'(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+\S+', line)
                if match:
                    ip, mac, vlan = match.groups()
                    entries.append((ip, mac, vlan if vlan != '--' else None))
        return entries

    def parse_mac_output(output, trunk_ports):
        entries = []
        lines = output.strip().split('\n')
        data_start = False
        for line in lines:
            if "MAC ADDR" in line and "VLAN ID" in line and "PORT INDEX" in line:
                data_start = True
                continue
            if "MAC Address" in line and "VLAN ID" in line and "Port/NickName" in line:
                data_start = True
                continue
            if "MAC Address" in line and "VLAN ID" in line and "State" in line and "Port/Nickname" in line and "Aging" in line:
                data_start = True
                continue
            if data_start and line.strip() and "---" not in line:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 4:
                    mac, vlan_id, port = parts[0], parts[1], parts[3]
                    normalized_port = normalize_port_name(port)
                    if port.startswith(("BAGG","Bridge-Aggregation")):
                        log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 过滤BAGG端口记录: MAC={mac}, 端口={port}"
                        print(log_message)
                        write_to_collection_log(log_message)
                        write_to_log(current_device_ip, log_message)
                        continue
                    if normalized_port not in trunk_ports:
                        entries.append((mac, vlan_id, port))
        return entries

    def parse_port_info(output, trunk_ports):
        """解析华三设备端口信息，基于标题行判断数据开始，并排除trunk口"""
        ports = []
        lines = output.strip().split('\n')
        data_start = False
        status_idx = -1

        # 记录原始输出
        log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 原始端口输出: {output}"
        print(log_message)
        write_to_collection_log(log_message)
        write_to_log(current_device_ip, log_message)

        # 检测标题行
        for i, line in enumerate(lines):
            cleaned_line = line.strip().replace('\r', '')
            # 判断桥接模式标题，确保跳过路由模式
            if 'brief information' in cleaned_line.lower() and ('bridge mode' in cleaned_line.lower()):
                log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 检测到桥接模式标题: {cleaned_line}"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(current_device_ip, log_message)
                continue
            # 标题行：包含 Interface 和 Link
            if "Interface" in cleaned_line and "Speed" in cleaned_line:
                data_start = True
                columns = re.split(r'\s+', cleaned_line.strip())
                if "Link" in columns:
                    status_idx = columns.index("Link")
                log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 检测到标题行: {cleaned_line}, 状态列索引: {status_idx}"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(current_device_ip, log_message)
                break

        if not data_start:
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 未检测到桥接模式标题行"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
            return ports

        if status_idx == -1:
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 未找到状态列（Link）"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
            return ports

        # 解析数据行
        for line in lines[i + 1:]:
            line = line.strip().replace('\r', '')
            if not line or re.match(r'^-+$', line) or 'Description' in line:
                continue

            # 分 提取端口和状态
            parts = re.split(r'\s+', line.strip())
            if len(parts) < status_idx + 1 or len(parts) < 2:
                log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 端口行分割失败: {line}, 分割结果: {parts}"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(current_device_ip, log_message)
                continue

            port_name = parts[0]
            status = parts[status_idx].lower()
            normalized_port = normalize_port_name(port_name)

            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 端口行: {line}, 端口: {port_name}, 规范化端口: {normalized_port}, 状态: {status}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)

            # 过滤物理端口并排除trunk口
            if (
                re.match(r'^(GE|XGE|GigabitEthernet|Ten-GigabitEthernet)', port_name, re.IGNORECASE) and
                not re.match(r'^(Vlanif|LoopBack|NULL|Eth-Trunk|InLoop|REG|VT|Aux|NULL0|Loop0|BAGG)', port_name, re.IGNORECASE) and
                normalized_port not in trunk_ports
            ):
                if 'up' in status:
                    normalized_status = 'UP'
                elif 'adm' in status:
                    normalized_status = 'ADM'
                else:
                    normalized_status = 'DOWN'
                ports.append((port_name, normalized_status))
            elif normalized_port in trunk_ports:
                log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 排除trunk端口: {port_name} (规范化: {normalized_port})"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(current_device_ip, log_message)

        if not ports:
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 未提取到任何非trunk端口，可能是输出格式不匹配或无有效端口行"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
        else:
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 成功提取 {len(ports)} 个非trunk端口"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
        return ports

    def save_arp_to_mysql(arp_entries, device_ip, sysname, model):
        try:
            with pymysql.connect(**db_config) as db_conn:
                create_arp_table(db_conn)
                deleted = clear_old_records(db_conn, 'arp_info', device_ip)
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 已清理ARP旧记录 {deleted} 条"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
                insert_sql = """
                INSERT INTO arp_info (device_ip, sysname, model, ip, mac, vlan, collect_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data = [(device_ip, sysname, model, ip, mac, vlan, current_time) 
                        for ip, mac, vlan in arp_entries]
                with db_conn.cursor() as cursor:
                    cursor.executemany(insert_sql, data)
                db_conn.commit()
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功存储 {len(data)} 条最新ARP记录"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] ARP数据存储失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    def save_mac_to_mysql(mac_entries, device_ip, sysname, model):
        try:
            with pymysql.connect(**db_config) as db_conn:
                create_mac_table(db_conn)
                deleted = clear_old_records(db_conn, 'mac_info', device_ip)
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 已清理MAC旧记录 {deleted} 条"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
                insert_sql = """
                INSERT INTO mac_info (device_ip, sysname, model, mac, vlan_id, port, collect_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data = [(device_ip, sysname, model, mac, vlan, port, current_time) 
                        for mac, vlan, port in mac_entries]
                with db_conn.cursor() as cursor:
                    cursor.executemany(insert_sql, data)
                db_conn.commit()
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功存储 {len(data)} 条最新MAC记录"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] MAC数据存储失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    def create_arp_table(conn):
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS arp_info (
            id INT PRIMARY KEY AUTO_INCREMENT,
            device_ip VARCHAR(15) NOT NULL COMMENT '连接的交换机IP',
            sysname VARCHAR(50) COMMENT '交换机sysname',
            model VARCHAR(50) COMMENT '交换机型号',
            ip VARCHAR(15) NOT NULL COMMENT 'IP地址',
            mac VARCHAR(17) NOT NULL COMMENT 'MAC地址（原始格式）',
            vlan VARCHAR(10) COMMENT 'VLAN信息',
            collect_time DATETIME NOT NULL COMMENT '采集时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        with conn.cursor() as cursor:
            cursor.execute(create_table_sql)
        conn.commit()

    def create_mac_table(conn):
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS mac_info (
            id INT PRIMARY KEY AUTO_INCREMENT,
            device_ip VARCHAR(15) NOT NULL COMMENT '连接的交换机IP',
            sysname VARCHAR(50) COMMENT '交换机sysname',
            model VARCHAR(50) COMMENT '交换机型号',
            mac VARCHAR(17) NOT NULL COMMENT 'MAC地址（原始格式）',
            vlan_id VARCHAR(10) COMMENT 'VLAN ID',
            port VARCHAR(50) COMMENT '所在端口（非trunk口）',
            collect_time DATETIME NOT NULL COMMENT '采集时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        with conn.cursor() as cursor:
            cursor.execute(create_table_sql)
        conn.commit()

    def create_port_table(conn):
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS port_info (
            id INT PRIMARY KEY AUTO_INCREMENT,
            device_ip VARCHAR(15) NOT NULL COMMENT '设备IP地址',
            sysname VARCHAR(50) COMMENT '设备sysname',
            model VARCHAR(50) COMMENT '设备型号',
            port_name VARCHAR(50) NOT NULL COMMENT '端口名称',
            status VARCHAR(10) NOT NULL COMMENT '端口状态（UP/DOWN/ADM）',
            collect_time DATETIME NOT NULL COMMENT '采集时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        with conn.cursor() as cursor:
            cursor.execute(create_table_sql)
        conn.commit()

    def clear_old_records(conn, table_name, device_ip):
        delete_sql = f"DELETE FROM {table_name} WHERE device_ip = %s"
        with conn.cursor() as cursor:
            cursor.execute(delete_sql, (device_ip,))
            deleted_rows = cursor.rowcount
        conn.commit()
        return deleted_rows

    def save_port_to_mysql(port_entries, device_ip, sysname, model):
        try:
            with pymysql.connect(**db_config) as db_conn:
                create_port_table(db_conn)
                deleted = clear_old_records(db_conn, 'port_info', device_ip)
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 已清理端口旧记录 {deleted} 条"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
                insert_sql = """
                INSERT INTO port_info (device_ip, sysname, model, port_name, status, collect_time)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data = [(device_ip, sysname, model, port_name, status, current_time) 
                        for port_name, status in port_entries]
                with db_conn.cursor() as cursor:
                    cursor.executemany(insert_sql, data)
                db_conn.commit()
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功存储 {len(data)} 条端口信息记录"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 端口信息存储失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    def update_devinfo(conn, device_ip, sysname):
        update_sql = """
        UPDATE devinfo 
        SET devtype = %s, devname = %s 
        WHERE ip = %s
        """
        try:
            with conn.cursor() as cursor:
                cursor.execute(update_sql, ('H3C', sysname, device_ip))
            conn.commit()
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功更新devinfo表: devtype='H3C', devname='{sysname}'"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 更新devinfo表失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    try:
        with ConnectHandler(**full_device) as conn:
            conn.enable()
            if sysname == "未知":
                config_output = conn.send_command("display current-configuration | include sysname")
                sysname = parse_current_config(config_output)
            version_output = conn.send_command("display version")
            model = parse_version_output(version_output)
            current_device_ip = full_device['ip']
            
            # Update devinfo table with devtype and devname
            with pymysql.connect(**db_config) as db_conn:
                update_devinfo(db_conn, current_device_ip, sysname)
            
            # Get trunk ports for all models to use in port parsing
            trunk_ports = get_trunk_ports(conn, model)
            
            if model.upper().startswith(("S5560","S5500","MSR36")):
                arp_output = conn.send_command("display arp all")
                arp_entries = parse_arp_output(arp_output)
                if arp_entries:
                    save_arp_to_mysql(arp_entries, current_device_ip, sysname, model)
            
            if model.upper().startswith(("S51","S5500","MSR36")):
                mac_output = conn.send_command("display mac-address")
                mac_entries = parse_mac_output(mac_output, trunk_ports)
                if mac_entries == []:
                    log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 排除trunk口，BAGG口后未匹配到本设备上的终端MAC地址，不记录入数据库"
                    print(log_message)
                    write_to_collection_log(log_message)
                    write_to_log(current_device_ip, log_message)
                if mac_entries:
                    save_mac_to_mysql(mac_entries, current_device_ip, sysname, model)
            
            # 提取并存储端口信息
            try:
                port_output = conn.send_command("display interface brief")
                log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] display interface brief 执行成功"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(current_device_ip, log_message)
            except Exception as cmd_e:
                log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] display interface brief 执行失败: {str(cmd_e)}"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(current_device_ip, log_message)
                port_output = ""
            port_entries = parse_port_info(port_output, trunk_ports)
            if port_entries:
                save_port_to_mysql(port_entries, current_device_ip, sysname, model)
            else:
                log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 未获取到有效端口信息"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(current_device_ip, log_message)
    except Exception as e:
        log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 华三设备处理失败: {str(e)}"
        print(log_message)
        write_to_collection_log(log_message)
        write_to_log(current_device_ip, log_message)