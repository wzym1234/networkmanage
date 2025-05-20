import re
from datetime import datetime
import pymysql
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
from utils import write_to_log, db_config, write_to_collection_log

def huawei_device_processing(full_device, device_name="未知"):
    device_ip = full_device.get('ip', '未知IP')
    sysname = device_name
    device_model = '未知型号'

    def normalize_port_name(port):
        """Normalize port name to abbreviated format (e.g., GE0/0/1, XGE0/0/1)"""
        port = re.sub(r'XGigabitEthernet', 'XGE', port)
        port = re.sub(r'GigabitEthernet', 'GE', port)
        return port

    def get_sysname(output):
        match = re.search(r"sysname\s+(\S+)", output)
        return match.group(1) if match else "未知"

    def get_device_model(output):
        match = re.search(r"(?i)(?:Huawei|Quidway)\s+(\S+?)\s+(?:Router|Routing Switch)", output)
        return match.group(1) if match else "未知"

    def parse_trunk_ports(output):
        ports = []
        lines = output.strip().split("\n")
        for line in lines[1:]:
            if not line.strip():
                continue
            full_port = line.split()[0]
            normalized_port = normalize_port_name(full_port)
            ports.append(normalized_port)
        log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 检测到trunk口: {ports}"
        print(log_message)
        write_to_collection_log(log_message)
        write_to_log(device_ip, log_message)
        return ports

    def parse_mac_address(output, exclude_ports):
        mac_list = []
        lines = output.strip().split("\n")
        header_found = False
        data_start = -1
        for idx, line in enumerate(lines):
            if "MAC Address" in line and "Learned-From" in line:
                data_start = idx + 2
                header_found = True
                break
        if not header_found:
            return mac_list
        data_end = len(lines)
        for idx in range(data_start, len(lines)):
            if lines[idx].startswith("----------------") or "Total items" in lines[idx]:
                data_end = idx
                break
        data_lines = lines[data_start:data_end]
        mac_re = re.compile(
            r'^(?P<mac>[\w\-]+)\s+(?P<vlan>\d+)/-(\/-)?\s+(?P<port>\S+)\s+(?P<type>\S+)$',
            re.MULTILINE
        )
        for line in data_lines:
            line = line.strip()
            if not line:
                continue
            match = mac_re.match(line)
            if match:
                mac = match.group("mac")
                vlan = match.group("vlan")
                port = match.group("port")
                normalized_port = normalize_port_name(port)
                if normalized_port not in exclude_ports:
                    mac_list.append((mac, vlan, port))
        return mac_list

    def parse_arp_info(output):
        try:
           arp_entries = []
           lines = output.strip().split('\n')
           current_entry = None
           vlan_pattern = re.compile(r'(\d+)')
           for i, line in enumerate(lines):
               line = line.strip()
               if not line or line.startswith('-'):
                   continue
               match = re.match(
                r'^(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F\-]+)\s*(\d*)\s*([DI])[^\n]*?\s+(\S+)\s*(.*)$',
                   line, re.IGNORECASE
               )
               if match:
                   if current_entry:
                       arp_entries.append(current_entry)
                   ip, mac, expire, type_, interface, extra = match.groups()
                   current_entry = {
                       'ip': ip,
                       'mac': mac,
                       'interface': interface,
                       'vlan': None,
                       'extra': extra
                   }
                   if 'Vlanif' in interface:
                       current_entry['vlan'] = vlan_pattern.search(interface).group()
               else:
                   if current_entry and current_entry['vlan'] is None:
                       vlan_match = vlan_pattern.search(line)
                       if vlan_match:
                           current_entry['vlan'] = vlan_match.group()
           if current_entry:
               arp_entries.append(current_entry)
           result = []
           for entry in arp_entries:
                vlan = entry['vlan']
                if not vlan:
                    extra_vlan = vlan_pattern.search(entry['extra'])
                    if extra_vlan:
                        vlan = extra_vlan.group()
                if vlan and vlan.isdigit():
                    result.append((entry['ip'], entry['mac'], vlan))
           return result
        except Exception as e:
           log_message = f"[设备IP: {device_ip}] ARP解析异常: {str(e)}"
           print(log_message)
           write_to_log(device_ip, log_message)
           return []

    def parse_huawei_port_info(output, trunk_ports):
        ports = []
        lines = output.strip().split('\n')
        data_start = False
        status_idx = -1
        for line in lines:
            if "Interface" in line and "PHY" in line:
                data_start = True
                columns = re.split(r'\s+', line.strip())
                if "PHY" in columns:
                    status_idx = columns.index("PHY")
                continue
            if data_start and line.strip():
                parts = re.split(r'\s+', line.strip())
                if len(parts) > status_idx:
                    interface = parts[0]
                    phy_status = parts[status_idx].lower()
                    normalized_interface = normalize_port_name(interface)
                    if (
                        re.match(r'^GigabitEthernet|XGigabitEthernet', interface) and
                        not re.search(r'\(', interface) and
                        normalized_interface not in trunk_ports
                    ):
                        if 'up' in phy_status:
                            status = 'UP'
                        elif '*down' in phy_status:
                            status = 'ADM'
                        else:
                            status = 'DOWN'
                        ports.append((interface, status))
                    elif normalized_interface in trunk_ports:
                        log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 排除trunk端口: {interface} (规范化: {normalized_interface})"
                        print(log_message)
                        write_to_collection_log(log_message)
                        write_to_log(device_ip, log_message)
        if not ports:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 未提取到任何非trunk端口，可能是输出格式不匹配或无有效端口行"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
        else:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功提取 {len(ports)} 个非trunk端口"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
        return ports

    def save_arp_to_mysql(arp_entries, device_ip, sysname, model):
        try:
            if not arp_entries:
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 无有效ARP条目需要存储"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
                return
            with pymysql.connect(**db_config) as db_conn:
                create_arp_table(db_conn)
                deleted = clear_old_records(db_conn, 'huawei_arp_info', device_ip)
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 已清理华为设备ARP旧记录 {deleted} 条"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
                insert_sql = """
                INSERT INTO huawei_arp_info (device_ip, sysname, model, ip, mac, vlan, collect_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data = [(device_ip, sysname, model, ip, mac, vlan, current_time) 
                        for ip, mac, vlan in arp_entries]
                with db_conn.cursor() as cursor:
                    cursor.executemany(insert_sql, data)
                db_conn.commit()
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功存储 {len(data)} 条华为设备ARP记录"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 华为设备ARP数据存储失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    def save_port_to_mysql(port_entries, device_ip, sysname, model):
        try:
            with pymysql.connect(**db_config) as db_conn:
                create_port_table(db_conn)
                deleted = clear_old_records(db_conn, 'huawei_port_info', device_ip)
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 已清理华为设备端口旧记录 {deleted} 条"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
                insert_sql = """
                INSERT INTO huawei_port_info (device_ip, sysname, model, port_name, status, collect_time)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data = [(device_ip, sysname, model, port, status, current_time) 
                        for port, status in port_entries]
                with db_conn.cursor() as cursor:
                    cursor.executemany(insert_sql, data)
                db_conn.commit()
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功存储 {len(data)} 条华为设备端口记录"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 华为设备端口数据存储失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    def create_arp_table(conn):
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS huawei_arp_info (
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

    def create_port_table(conn):
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS huawei_port_info (
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

    def create_table(conn):
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS huawei_mac_info (
            id INT AUTO_INCREMENT PRIMARY KEY,
            device_ip VARCHAR(45) NOT NULL,
            sysname VARCHAR(45) NOT NULL,
            device_model VARCHAR(100) NOT NULL,
            mac_address VARCHAR(45) NOT NULL,
            vlan_id INT NOT NULL,
            port VARCHAR(45) NOT NULL,
            collect_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        try:
            with conn.cursor() as cursor:
                cursor.execute(create_table_sql)
            conn.commit()
        except pymysql.MySQLError as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 创建表失败: {e}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    def clear_old_data(conn, current_device_ip):
        clear_sql = "DELETE FROM huawei_mac_info WHERE device_ip = %s"
        try:
            with conn.cursor() as cursor:
                cursor.execute(clear_sql, (current_device_ip,))
                deleted_rows = cursor.rowcount
            conn.commit()
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 已清理当前设备旧MAC地址、端口数据 {deleted_rows} 条"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
        except pymysql.MySQLError as e:
            log_message = f"[设备IP: {current_device_ip}][设备名称: {sysname}] 清空当前设备MAC地址、端口数据失败: {e}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(current_device_ip, log_message)
            conn.rollback()

    def insert_data(conn, data):
        insert_sql = """
        INSERT INTO huawei_mac_info (device_ip, sysname, device_model, mac_address, vlan_id, port)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        try:
            with conn.cursor() as cursor:
                cursor.executemany(insert_sql, data)
            conn.commit()
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功插入{len(data)}条MAC地址端口数据"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
        except pymysql.MySQLError as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 插入MAC地址端口数据失败: {e}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
            conn.rollback()

    def connect_to_db():
        try:
            return pymysql.connect(**db_config)
        except pymysql.MySQLError as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 数据库连接失败: {e}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
            return None

    def update_devinfo(conn, device_ip, sysname):
        update_sql = """
        UPDATE devinfo 
        SET devtype = %s, devname = %s 
        WHERE ip = %s
        """
        try:
            with conn.cursor() as cursor:
                cursor.execute(update_sql, ('HUAWEI', sysname, device_ip))
            conn.commit()
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 成功更新devinfo表: devtype='HUAWEI', devname='{sysname}'"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)
        except Exception as e:
            log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 更新devinfo表失败: {str(e)}"
            print(log_message)
            write_to_collection_log(log_message)
            write_to_log(device_ip, log_message)

    try:
        with ConnectHandler(**full_device) as net_connect:
            net_connect.enable()
            if sysname == "未知":
                config_output = net_connect.send_command("display current-configuration | include sysname")
                sysname = get_sysname(config_output)
            version_output = net_connect.send_command("display version")
            device_model = get_device_model(version_output)
            
            # Update devinfo table with devtype and devname
            conn_db = connect_to_db()
            if conn_db:
                update_devinfo(conn_db, device_ip, sysname)
            
            arp_output = net_connect.send_command("display arp all") or ""
            arp_entries = parse_arp_info(arp_output)
            if arp_entries:
                save_arp_to_mysql(arp_entries, device_ip, sysname, device_model)
            else:
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 未获取到有效ARP条目"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
            
            trunk_output = net_connect.send_command("dis port vlan | include trunk")
            trunk_ports = parse_trunk_ports(trunk_output)
            
            mac_output = net_connect.send_command("disp mac-address")
            mac_list = parse_mac_address(mac_output, trunk_ports)
            
            db_data = [(
                device_ip,
                sysname,
                device_model,
                mac,
                int(vlan),
                port
            ) for mac, vlan, port in mac_list]
            
            if conn_db:
                create_table(conn_db)
                clear_old_data(conn_db, device_ip)
                insert_data(conn_db, db_data)
            
            # New port information processing
            port_output = net_connect.send_command("display interface brief")
            port_entries = parse_huawei_port_info(port_output, trunk_ports)
            if port_entries:
                save_port_to_mysql(port_entries, device_ip, sysname, device_model)
            else:
                log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 未获取到有效端口信息"
                print(log_message)
                write_to_collection_log(log_message)
                write_to_log(device_ip, log_message)
            
            if conn_db:
                conn_db.close()
    except NetmikoTimeoutException:
        log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 连接交换机超时，请检查IP地址或网络连接"
        print(log_message)
        write_to_collection_log(log_message)
        write_to_log(device_ip, log_message)
    except NetmikoAuthenticationException:
        log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 认证失败，请检查用户名或密码"
        print(log_message)
        write_to_collection_log(log_message)
        write_to_log(device_ip, log_message)
    except Exception as e:
        log_message = f"[设备IP: {device_ip}][设备名称: {sysname}] 发生意外错误: {e}"
        print(log_message)
        write_to_collection_log(log_message)
        write_to_log(device_ip, log_message)