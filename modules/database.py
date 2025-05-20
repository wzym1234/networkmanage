import pymysql
from pymysql.cursors import DictCursor
import logging
from utils import db_config

def get_db_connection():
    """获取数据库连接"""
    try:
        return pymysql.connect(**db_config, cursorclass=DictCursor)
    except pymysql.MySQLError as e:
        logging.error(f"数据库连接失败: {str(e)}")
        raise

def initialize_database():
    """初始化数据库表"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 创建 terminal_access_info 表
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS terminal_access_info (
                ip VARCHAR(255) COMMENT 'IP 地址',
                mac VARCHAR(255) COMMENT 'MAC 地址',
                vlan INT COMMENT 'VLAN ID',
                port VARCHAR(255) COMMENT '接入端口',
                switch_ip VARCHAR(255) COMMENT '交换机 IP',
                switch_name VARCHAR(255) COMMENT '交换机名称',
                switch_model VARCHAR(255) COMMENT '交换机型号',
                PRIMARY KEY (mac)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='终端接入信息汇总表';
            """
            cursor.execute(create_table_sql)

            # 创建 syn_log 表
            sync_log_sql = """
            CREATE TABLE IF NOT EXISTS syn_log (
                id INT PRIMARY KEY AUTO_INCREMENT,
                sync_time DATETIME NOT NULL COMMENT '同步时间',
                insert_count INT NOT NULL COMMENT '新增数量',
                update_count INT NOT NULL COMMENT '更新数量'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='同步日志表';
            """
            cursor.execute(sync_log_sql)

            # 创建 changelog 表
            changelog_sql = """
            CREATE TABLE IF NOT EXISTS changelog (
                id INT PRIMARY KEY AUTO_INCREMENT,
                sync_id INT NOT NULL COMMENT '关联同步日志ID',
                old_switch_name VARCHAR(255) COMMENT '原交换机名称',
                old_port VARCHAR(255) COMMENT '原端口',
                old_ip VARCHAR(255) COMMENT '原IP',
                old_mac VARCHAR(255) COMMENT '原MAC',
                old_switch_ip VARCHAR(255) COMMENT '原交换机IP',
                old_vlan INT COMMENT '原VLAN',
                new_switch_name VARCHAR(255) COMMENT '新交换机名称',
                new_port VARCHAR(255) COMMENT '新端口',
                new_ip VARCHAR(255) COMMENT '新IP',
                new_mac VARCHAR(255) COMMENT '新MAC',
                new_switch_ip VARCHAR(255) COMMENT '新交换机IP',
                new_vlan INT COMMENT '新VLAN',
                FOREIGN KEY (sync_id) REFERENCES syn_log(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='变更明细表';
            """
            cursor.execute(changelog_sql)

            # 创建 devinfo 表
            create_devinfo_sql = """
            CREATE TABLE IF NOT EXISTS devinfo (
                id INT PRIMARY KEY AUTO_INCREMENT,
                ip VARCHAR(15) NOT NULL UNIQUE COMMENT '设备IP',
                user VARCHAR(50) NOT NULL COMMENT '用户名',
                pass VARCHAR(50) NOT NULL COMMENT '密码',
                devtype VARCHAR(50) COMMENT '设备类型（H3C或HUAWEI）',
                devname VARCHAR(50) COMMENT '设备名称'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备信息表';
            """
            cursor.execute(create_devinfo_sql)

            # 创建 scheduled_collections 表（支持周期性任务）
            create_schedule_sql = """
            CREATE TABLE IF NOT EXISTS scheduled_collections (
                id INT PRIMARY KEY AUTO_INCREMENT,
                frequency ENUM('daily', 'weekly', 'monthly') NOT NULL COMMENT '任务频率',
                start_date DATE NOT NULL COMMENT '开始日期',
                end_date DATE NOT NULL COMMENT '结束日期',
                execution_time TIME NOT NULL COMMENT '每日执行时间',
                next_execution DATETIME COMMENT '下一次执行时间',
                status ENUM('pending', 'completed', 'failed') DEFAULT 'pending' COMMENT '状态',
                created_at DATETIME NOT NULL COMMENT '创建时间',
                last_executed_at DATETIME COMMENT '最后执行时间'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='定时采集计划表';
            """
            cursor.execute(create_schedule_sql)

            # 创建 collection_status 表
            create_collection_status_sql = """
            CREATE TABLE IF NOT EXISTS collection_status (
                id INT PRIMARY KEY AUTO_INCREMENT,
                type ENUM('all', 'selected') NOT NULL COMMENT '采集类型',
                status ENUM('running', 'completed', 'failed') NOT NULL COMMENT '任务状态',
                start_time DATETIME NOT NULL COMMENT '开始时间',
                end_time DATETIME COMMENT '结束时间',
                message TEXT COMMENT '任务消息'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采集任务状态表';
            """
            cursor.execute(create_collection_status_sql)

            conn.commit()
    except pymysql.MySQLError as e:
        logging.error(f"数据库初始化失败: {str(e)}")
        raise RuntimeError(f"数据库初始化失败: {str(e)}")
    finally:
        if 'conn' in locals():
            conn.close()