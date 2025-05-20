import pymysql
import logging
from datetime import datetime
from modules.database import get_db_connection, initialize_database

# SQL query for terminal access info
SELECT_SQL = """
SELECT 
    arp.terminal_ip AS ip,
    arp.terminal_mac AS mac,
    arp.terminal_vlan AS vlan,
    mac.access_port AS port,
    mac.switch_ip AS switch_ip,
    mac.switch_name AS switch_name,
    mac.switch_model AS switch_model
FROM 
    (
    SELECT 
        a.ip AS terminal_ip,
        a.mac AS terminal_mac,
        a.vlan AS terminal_vlan
    FROM 
        arp_info a
    UNION ALL
    SELECT 
        ha.ip AS terminal_ip,
        ha.mac AS terminal_mac,
        ha.vlan AS terminal_vlan
    FROM 
        huawei_arp_info ha
    WHERE 
        ha.ip IS NOT NULL 
        AND ha.mac IS NOT NULL
    ) arp  
INNER JOIN 
    (
    SELECT 
        mac.mac AS terminal_mac,
        mac.port AS access_port,
        mac.device_ip AS switch_ip,
        mac.sysname AS switch_name,
        mac.model AS switch_model
    FROM 
        mac_info mac
    WHERE 
        mac.mac IS NOT NULL 
        AND mac.port IS NOT NULL
    UNION ALL
    SELECT 
        huawei_mac.mac_address AS terminal_mac,
        huawei_mac.port AS access_port,
        huawei_mac.device_ip AS switch_ip,
        huawei_mac.sysname AS switch_name,
        huawei_mac.device_model AS switch_model
    FROM 
        huawei_mac_info huawei_mac
    WHERE 
        huawei_mac.mac_address IS NOT NULL 
        AND huawei_mac.port IS NOT NULL
    ) mac  
ON arp.terminal_mac = mac.terminal_mac  
ORDER BY 
    mac.switch_ip, mac.terminal_mac;
"""

def sync_terminal_info():
    """核心同步函数"""
    stats = {
        "insert_count": 0,
        "update_count": 0,
        "update_details": []
    }
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            initialize_database()

            cursor.execute("SELECT mac, switch_ip, ip, vlan, port, switch_name, switch_model FROM terminal_access_info")
            old_data = {row["mac"]: row for row in cursor.fetchall()}

            cursor.execute(SELECT_SQL)
            new_records = cursor.fetchall()

            if not new_records:
                logging.info("无新记录可同步")
                return stats

            columns = list(new_records[0].keys())
            insert_sql = f"""
            INSERT INTO terminal_access_info (
                {', '.join(columns)}
            ) VALUES (
                {', '.join(['%s'] * len(columns))}
            ) ON DUPLICATE KEY UPDATE 
                ip = IF(VALUES(ip) != ip, VALUES(ip), ip),
                vlan = IF(VALUES(vlan) != vlan, VALUES(vlan), vlan),
                port = IF(VALUES(port) != port, VALUES(port), port),
                switch_ip = IF(VALUES(switch_ip) != switch_ip, VALUES(switch_ip), switch_ip),
                switch_name = IF(VALUES(switch_name) != switch_name, VALUES(switch_name), switch_name),
                switch_model = IF(VALUES(switch_model) != switch_model, VALUES(switch_model), switch_model)
            """
            cursor.executemany(insert_sql, [tuple(row.values()) for row in new_records])
            conn.commit()

            cursor.execute("SELECT mac, switch_ip, ip, vlan, port, switch_name, switch_model FROM terminal_access_info")
            new_data = {row["mac"]: row for row in cursor.fetchall()}

            for mac in new_data:
                if mac not in old_data:
                    stats["insert_count"] += 1
                else:
                    old_row = old_data[mac]
                    new_row = new_data[mac]
                    if (old_row["ip"] != new_row["ip"] or 
                        old_row["vlan"] != new_row["vlan"] or 
                        old_row["port"] != new_row["port"] or 
                        old_row["switch_ip"] != new_row["switch_ip"] or 
                        old_row["switch_name"] != new_row["switch_name"] or 
                        old_row["switch_model"] != new_row["switch_model"]):
                        stats["update_count"] += 1
                        stats["update_details"].append({
                            "old_switch_name": old_row["switch_name"],
                            "old_port": old_row["port"],
                            "old_ip": old_row["ip"],
                            "old_mac": old_row["mac"],
                            "old_switch_ip": old_row["switch_ip"],
                            "old_vlan": old_row["vlan"], 
                            "new_switch_name": new_row["switch_name"],
                            "new_port": new_row["port"],
                            "new_ip": new_row["ip"],
                            "new_mac": new_row["mac"],
                            "new_switch_ip": new_row["switch_ip"],
                            "new_vlan": new_row["vlan"]
                        })

            if stats["insert_count"] > 0 or stats["update_count"] > 0:
                cursor.execute(
                    "INSERT INTO syn_log (sync_time, insert_count, update_count) VALUES (%s, %s, %s)",
                    (datetime.now(), stats["insert_count"], stats["update_count"])
                )
                sync_id = cursor.lastrowid

                if stats["update_count"] > 0:
                    changelog_data = [
                        (
                            sync_id,
                            detail["old_switch_name"],
                            detail["old_port"],
                            detail["old_ip"],
                            detail["old_mac"],
                            detail["old_switch_ip"],
                            detail["old_vlan"],
                            detail["new_switch_name"],
                            detail["new_port"],
                            detail["new_ip"],
                            detail["new_mac"],
                            detail["new_switch_ip"],
                            detail["new_vlan"]
                        ) for detail in stats["update_details"]
                    ]
                    cursor.executemany(
                        """INSERT INTO changelog (
                            sync_id, old_switch_name, old_port, old_ip, old_mac, old_switch_ip, old_vlan,
                            new_switch_name, new_port, new_ip, new_mac, new_switch_ip, new_vlan
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        changelog_data
                    )
                conn.commit()

            logging.info(f"数据同步完成：新增 {stats['insert_count']} 条，变更 {stats['update_count']} 条")
            return stats

    except pymysql.MySQLError as e:
        logging.error(f"数据同步失败: {str(e)}")
        raise RuntimeError(f"数据同步失败: {str(e)}")
    finally:
        if 'conn' in locals():
            conn.close()