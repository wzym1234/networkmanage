from flask import render_template, jsonify, request, send_file, send_from_directory, Response
import pymysql
import pandas as pd
import os

from io import BytesIO
from datetime import datetime, time, timedelta  # Added timedelta import
import logging
from modules.database import get_db_connection
from modules.sync import sync_terminal_info
from modules.utils import is_valid_ip, convert_to_time
from getarpmac import main as collect_devices, process_single_device
from concurrent.futures import ThreadPoolExecutor
from utils import write_to_collection_log

def register_routes(app):
    """注册所有 Flask 路由"""
    
    @app.route('/')
    def home():
        return render_template('index.html')

    @app.route('/terminal_monitor')
    def terminal_monitor():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT * FROM syn_log ORDER BY sync_time DESC LIMIT 1")
                    latest_log = cursor.fetchone() or {
                        "sync_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "insert_count": 0,
                        "update_count": 0,
                        "id": 0
                    }
                    cursor.execute(
                        "SELECT * FROM changelog WHERE sync_id = %s",
                        (latest_log["id"],)
                    )
                    update_details = cursor.fetchall()
            return render_template(
                'terminal_monitor.html',
                sync_time=latest_log["sync_time"],
                insert_count=latest_log["insert_count"],
                update_count=latest_log["update_count"],
                update_details=update_details
            )
        except Exception as e:
            logging.error(f"终端监控页面加载失败: {str(e)}")
            return f"页面加载失败: {str(e)}", 500

    @app.route('/terminal_query')
    def terminal_query():
        return render_template('terminal_query.html')

    @app.route('/device_management')
    def device_management():
        return render_template('device_management.html')

    @app.route('/data_collection')
    def data_collection():
        return render_template('data_collection.html')

    @app.route('/device_names')
    def get_device_names():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT DISTINCT devname FROM devinfo WHERE devname IS NOT NULL;")
                    results = cursor.fetchall()
            device_names = [row['devname'] for row in results if row['devname']]
            logging.info(f"获取设备名称: {device_names}")
            return jsonify(device_names)
        except pymysql.MySQLError as e:
            logging.error(f"获取设备名称失败: {str(e)}")
            return jsonify({"error": f"数据库错误: {str(e)}"}), 500

    @app.route('/switch_names')
    def get_switch_names():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT DISTINCT switch_name FROM terminal_access_info WHERE switch_name IS NOT NULL AND switch_name != ''")
                    results = cursor.fetchall()
            switch_names = [row['switch_name'] for row in results]
            if not switch_names:
                logging.warning("terminal_access_info 表中未找到有效的 switch_name")
            logging.info(f"获取交换机名称: {switch_names}")
            return jsonify(switch_names)
        except pymysql.MySQLError as e:
            logging.error(f"获取 switch_name 失败: {str(e)}")
            return jsonify({"error": f"数据库错误: {str(e)}"}), 500

    @app.route('/get_devices')
    def get_devices():
        try:
            ip = request.args.get('ip', '')
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    if ip:
                        cursor.execute("SELECT ip, user, pass, devname FROM devinfo WHERE ip = %s", (ip,))
                    else:
                        cursor.execute("SELECT ip, user, pass, devname FROM devinfo")
                    results = cursor.fetchall()
            devices = [dict(row) for row in results]
            logging.info(f"获取设备列表: 共 {len(devices)} 条")
            return jsonify({"devices": devices})
        except pymysql.MySQLError as e:
            logging.error(f"获取设备列表失败: {str(e)}")
            return jsonify({"error": f"数据库错误: {str(e)}"}), 500

    @app.route('/delete_devices', methods=['POST'])
    def delete_devices():
        try:
            data = request.json
            ips = data.get('ips', [])
            if not ips:
                logging.warning("未选择任何设备进行删除")
                return jsonify({"success": False, "message": "未选择任何设备"}), 400
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM devinfo WHERE ip IN ({})".format(','.join(['%s'] * len(ips))),
                        ips
                    )
                    deleted_rows = cursor.rowcount
                    conn.commit()
            logging.info(f"设备删除成功: 共删除 {deleted_rows} 条记录")
            return jsonify({"success": True, "message": f"成功删除 {deleted_rows} 个设备"})
        except pymysql.MySQLError as e:
            logging.error(f"删除设备失败: {str(e)}")
            return jsonify({"success": False, "message": f"数据库错误: {str(e)}"}), 500
        except Exception as e:
            logging.error(f"删除设备失败: {str(e)}")
            return jsonify({"success": False, "message": f"系统错误: {str(e)}"}), 500

    @app.route('/edit_device', methods=['POST'])
    def edit_device():
        try:
            original_ip = request.form['original_ip']
            ip = request.form['ip']
            user = request.form['user']
            passw = request.form['pass']
            if not is_valid_ip(ip):
                logging.warning(f"无效的IP地址: {ip}")
                return jsonify({"success": False, "message": "无效的IP地址"}), 400
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE devinfo SET ip = %s, user = %s, pass = %s WHERE ip = %s",
                        (ip, user, passw, original_ip)
                    )
                    if cursor.rowcount == 0:
                        logging.warning(f"未找到IP为 {original_ip} 的设备")
                        return jsonify({"success": False, "message": "设备不存在"}), 404
                    conn.commit()
            logging.info(f"设备编辑成功: IP={original_ip} 更新为 IP={ip}")
            return jsonify({"success": True, "message": "设备编辑成功"})
        except pymysql.MySQLError as e:
            logging.error(f"编辑设备失败: {str(e)}")
            return jsonify({"success": False, "message": f"数据库错误: {str(e)}"}), 500
        except Exception as e:
            logging.error(f"编辑设备失败: {str(e)}")
            return jsonify({"success": False, "message": f"系统错误: {str(e)}"}), 500

    @app.route('/query')
    def query_data():
        try:
            ip = request.args.get('ip', '')
            mac = request.args.get('mac', '')
            switch_names = request.args.get('switch_names', '').split(',') if request.args.get('switch_names') else []
            conditions = []
            params = []
            if ip:
                conditions.append("ip LIKE %s")
                params.append(f"%{ip}%")
            if mac:
                conditions.append("mac LIKE %s")
                params.append(f"%{mac}%")
            if switch_names:
                conditions.append("switch_name IN ({})".format(','.join(['%s'] * len(switch_names))))
                params.extend(switch_names)
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            query_sql = f"SELECT * FROM terminal_access_info {where_clause};"
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query_sql, params)
                    results = cursor.fetchall()
            logging.info(f"查询数据: 条件={where_clause}, 结果数={len(results)}")
            return jsonify([dict(row) for row in results])
        except pymysql.MySQLError as e:
            logging.error(f"查询数据失败: {str(e)}")
            return jsonify({"error": f"数据库错误: {str(e)}"}), 500

    @app.route('/api/data')
    def api_data():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT * FROM terminal_access_info;")
                    results = cursor.fetchall()
            logging.info(f"API 数据查询: 结果数={len(results)}")
            return jsonify([dict(row) for row in results])
        except pymysql.MySQLError as e:
            logging.error(f"API 数据查询失败: {str(e)}")
            return jsonify({"error": f"数据库错误: {str(e)}"}), 500

    @app.route('/get_collection_status')
    def get_collection_status():
        """获取当前采集任务状态"""
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT type, status, start_time, end_time, message
                        FROM collection_status
                        WHERE status = 'running'
                        ORDER BY start_time DESC
                        LIMIT 1
                    """)
                    running_task = cursor.fetchone()
                    if running_task:
                        return jsonify({
                            "is_running": True,
                            "type": running_task['type'],
                            "start_time": running_task['start_time'].strftime('%Y-%m-%d %H:%M:%S'),
                            "message": running_task['message']
                        })
                    # 检查最近完成或失败的任务
                    cursor.execute("""
                        SELECT type, status, start_time, end_time, message
                        FROM collection_status
                        WHERE status IN ('completed', 'failed')
                        ORDER BY end_time DESC
                        LIMIT 1
                    """)
                    last_task = cursor.fetchone()
                    if last_task:
                        return jsonify({
                            "is_running": False,
                            "type": last_task['type'],
                            "status": last_task['status'],
                            "start_time": last_task['start_time'].strftime('%Y-%m-%d %H:%M:%S'),
                            "end_time": last_task['end_time'].strftime('%Y-%m-%d %H:%M:%S') if last_task['end_time'] else None,
                            "message": last_task['message']
                        })
                    return jsonify({
                        "is_running": False,
                        "type": None,
                        "status": None,
                        "start_time": None,
                        "end_time": None,
                        "message": None
                    })
        except pymysql.MySQLError as e:
            logging.error(f"获取采集状态失败: {str(e)}")
            return jsonify({"error": f"数据库错误: {str(e)}"}), 500

    @app.route('/collect_all')
    def collect_all():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # 检查是否已有正在运行的采集任务
                    cursor.execute("SELECT id FROM collection_status WHERE status = 'running'")
                    if cursor.fetchone():
                        logging.warning("已有正在运行的采集任务")
                        return jsonify({
                            "success": False,
                            "message": "已有正在运行的采集任务，请稍后再试"
                        }), 400

                    # 记录采集任务状态
                    cursor.execute(
                        "INSERT INTO collection_status (type, status, start_time) VALUES (%s, %s, NOW())",
                        ('all', 'running')
                    )
                    collection_id = cursor.lastrowid
                    conn.commit()

            collect_result = collect_devices()  # 调用 main 函数
            sync_stats = sync_terminal_info()
            device_list = collect_result.get("device_list", [])
            failed_devices = collect_result.get("failed_devices", [])
            total_devices = len(device_list)
            success_count = total_devices - len(failed_devices)
            
            message = []
            message.append(f"成功采集 {success_count} 台设备，失败 {len(failed_devices)} 台设备")
            if failed_devices:
                message.append("失败设备详情：")
                message.extend([f"- IP {d['ip']}: {d['error']}" for d in failed_devices])
            message.append(f"数据同步：新增 {sync_stats['insert_count']} 条，变更 {sync_stats['update_count']} 条")
            
            success = success_count > 0

            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE collection_status 
                        SET status = %s, end_time = NOW(), message = %s 
                        WHERE id = %s
                        """,
                        ('completed' if success else 'failed', '\n'.join(message), collection_id)
                    )
                    conn.commit()

            logging.info(f"全网采集: {message[0]}")
            return jsonify({
                "success": success,
                "message": "\n".join(message)
            }), 200 if success else 400
        except Exception as e:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE collection_status 
                        SET status = %s, end_time = NOW(), message = %s 
                        WHERE id = %s
                        """,
                        ('failed', f"系统错误：全网采集失败，详情请查看日志: {str(e)}", collection_id)
                    )
                    conn.commit()
            logging.error(f"全网采集失败: {str(e)}")
            return jsonify({
                "success": False,
                "message": f"系统错误：全网采集失败，详情请查看日志"
            }), 500

    @app.route('/collect_selected', methods=['POST'])
    def collect_selected():
        # 清空 collection.log 文件
        with open('collection.log', 'w', encoding='utf-8') as f:
            f.write('')
        try:
            switch_names = request.json.get('switch_names', [])
            if not switch_names:
                logging.warning("未选择任何设备")
                return jsonify({
                    "success": False,
                    "message": "未选择任何设备"
                }), 400
            
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # 检查是否已有正在运行的采集任务
                    cursor.execute("SELECT id FROM collection_status WHERE status = 'running'")
                    if cursor.fetchone():
                        logging.warning("已有正在运行的采集任务")
                        return jsonify({
                            "success": False,
                            "message": "已有正在运行的采集任务，请稍后再试"
                        }), 400

                    # 记录采集任务状态
                    cursor.execute(
                        "INSERT INTO collection_status (type, status, start_time) VALUES (%s, %s, NOW())",
                        ('selected', 'running')
                    )
                    collection_id = cursor.lastrowid
                    conn.commit()

            logging.info(f"开始采集设备: {switch_names}")
            
            switch_ip_mapping = {}
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT devname, ip FROM devinfo WHERE devname IN ({}) AND ip IS NOT NULL".format(
                            ','.join(['%s'] * len(switch_names))
                        ),
                        switch_names
                    )
                    results = cursor.fetchall()
                    for row in results:
                        if is_valid_ip(row['ip']):
                            switch_ip_mapping[row['devname']] = row['ip']
                        else:
                            logging.warning(f"无效的ip: {row['ip']} for devname: {row['devname']}")
            
            device_ips = list(set(switch_ip_mapping.values()))
            missing_switches = [name for name in switch_names if name not in switch_ip_mapping]
            
            if missing_switches:
                logging.warning(f"以下设备名称无有效IP: {missing_switches}")
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE collection_status 
                            SET status = %s, end_time = NOW(), message = %s 
                            WHERE id = %s
                            """,
                            ('failed', f"以下设备名称无有效IP，请检查设备信息数据: {', '.join(missing_switches)}", collection_id)
                        )
                        conn.commit()
                return jsonify({
                    "success": False,
                    "message": f"以下设备名称无有效IP，请检查设备信息数据: {', '.join(missing_switches)}"
                }), 400
            
            if not device_ips:
                logging.warning(f"选择的设备 {switch_names} 无有效IP")
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE collection_status 
                            SET status = %s, end_time = NOW(), message = %s 
                            WHERE id = %s
                            """,
                            ('failed', f"选择的设备 {switch_names} 无有效IP，请检查设备信息数据", collection_id)
                        )
                        conn.commit()
                return jsonify({
                    "success": False,
                    "message": f"选择的设备 {switch_names} 无有效IP，请检查设备信息数据"
                }), 400
            
            logging.info(f"设备名称到IP映射: {switch_ip_mapping}")
            logging.info(f"查询到有效设备IP: {device_ips}")
            
            device_list = []
            missing_ips = []
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT ip, user, pass FROM devinfo WHERE ip IN ({})".format(
                            ','.join(['%s'] * len(device_ips))
                        ),
                        device_ips
                    )
                    rows = cursor.fetchall()
                    device_list = [
                        {"ip": row["ip"], "user": row["user"], "pass": row["pass"]}
                        for row in rows
                        if row["ip"] and row["user"] and row["pass"]
                    ]
                    found_ips = {row["ip"] for row in rows}
                    missing_ips = [ip for ip in device_ips if ip not in found_ips]
            
            if missing_ips:
                missing_switches = [name for name, ip in switch_ip_mapping.items() if ip in missing_ips]
                logging.warning(f"以下设备IP未在设备列表中: {missing_ips} (对应设备名称: {missing_switches})")
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE collection_status 
                            SET status = %s, end_time = NOW(), message = %s 
                            WHERE id = %s
                            """,
                            ('failed', f"以下设备IP未在设备列表中，请在设备管理中添加: {', '.join(missing_ips)} (对应设备名称: {', '.join(missing_switches)})", collection_id)
                        )
                        conn.commit()
                return jsonify({
                    "success": False,
                    "message": f"以下设备IP未在设备列表中，请在设备管理中添加: {', '.join(missing_ips)} (对应设备名称: {', '.join(missing_switches)})"
                }), 400
            
            if not device_list:
                logging.warning("设备信息列表为空")
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE collection_status 
                            SET status = %s, end_time = NOW(), message = %s 
                            WHERE id = %s
                            """,
                            ('failed', "未找到有效的设备信息，请在设备管理中添加设备", collection_id)
                        )
                        conn.commit()
                return jsonify({
                    "success": False,
                    "message": "未找到有效的设备信息，请在设备管理中添加设备"
                }), 400
            
            logging.info(f"设备信息: {device_list}")
            
            failed_devices = []
            with ThreadPoolExecutor(max_workers=25) as executor:
                futures = []
                for device in device_list:
                    futures.append(executor.submit(process_single_device, device))
                
                for future, device in zip(futures, device_list):
                    try:
                        future.result()
                        logging.info(f"设备 {device['ip']} 采集成功")
                    except Exception as e:
                        error_msg = f"设备 {device['ip']} 采集失败: {str(e)}"
                        logging.error(error_msg)
                        failed_devices.append({"ip": device['ip'], "error": str(e)})
            
            sync_stats = sync_terminal_info()
            
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    if failed_devices:
                        message = (
                            f"部分设备采集失败：\n" +
                            "\n".join([f"IP {d['ip']}: {d['error']}" for d in failed_devices]) +
                            f"\n成功采集数据：新增 {sync_stats['insert_count']} 条，变更 {sync_stats['update_count']} 条"
                        )
                        logging.warning(message)
                        success = len(failed_devices) < len(device_list)
                        cursor.execute(
                            """
                            UPDATE collection_status 
                            SET status = %s, end_time = NOW(), message = %s 
                            WHERE id = %s
                            """,
                            ('completed' if success else 'failed', message, collection_id)
                        )
                        conn.commit()
                        return jsonify({
                            "success": success,
                            "message": message
                        }), 200 if success else 400
                    else:
                        message = f"指定设备采集完成！新增数据：{sync_stats['insert_count']}条，变更数据：{sync_stats['update_count']}条"
                        logging.info(message)
                        cursor.execute(
                            """
                            UPDATE collection_status 
                            SET status = %s, end_time = NOW(), message = %s 
                            WHERE id = %s
                            """,
                            ('completed', message, collection_id)
                        )
                        conn.commit()
                        return jsonify({
                            "success": True,
                            "message": message
                        })

        except Exception as e:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE collection_status 
                        SET status = %s, end_time = NOW(), message = %s 
                        WHERE id = %s
                        """,
                        ('failed', f"指定设备采集失败: {str(e)}", collection_id)
                    )
                    conn.commit()
            logging.error(f"指定设备采集失败: {str(e)}")
            return jsonify({
                "success": False,
                "message": f"指定设备采集失败: {str(e)}"
            }), 500

    @app.route('/export_excel')
    def export_excel():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT * FROM terminal_access_info;")
                    results = cursor.fetchall()
                    data = [dict(row) for row in results]
            if not data:
                logging.warning("无数据可导出")
                return jsonify({"error": "无数据可导出"}), 400
            df = pd.DataFrame(data)
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='终端接入信息')
            output.seek(0)
            logging.info("数据导出为 Excel")
            return send_file(
                output,
                as_attachment=True,
                download_name=f"终端接入信息-{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except pymysql.MySQLError as e:
            logging.error(f"导出 Excel 失败: {str(e)}")
            return jsonify({"error": f"数据库错误: {str(e)}"}), 500
        except Exception as e:
            logging.error(f"导出 Excel 失败: {str(e)}")
            return jsonify({"error": f"导出失败: {str(e)}"}), 500

    @app.route('/add_device', methods=['POST'])
    def add_device():
        try:
            ip = request.form['ip']
            user = request.form['user']
            passw = request.form['pass']
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    insert_sql = "INSERT INTO devinfo (ip, user, pass) VALUES (%s, %s, %s)"
                    cursor.execute(insert_sql, (ip, user, passw))
                    conn.commit()
            logging.info(f"设备添加成功: IP={ip}")
            return jsonify({"success": True, "message": "设备添加成功"})
        except pymysql.MySQLError as e:
            logging.error(f"添加设备失败: {str(e)}")
            return jsonify({"success": False, "message": f"数据库错误: {str(e)}"}), 500

    @app.route('/import_devices', methods=['POST'])
    def import_devices():
        try:
            file = request.files['file']
            df = pd.read_excel(file)
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    for index, row in df.iterrows():
                        ip = row['ip']
                        user = row['user']
                        passw = row['pass']
                        insert_sql = "INSERT INTO devinfo (ip, user, pass) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE user=%s, pass=%s"
                        cursor.execute(insert_sql, (ip, user, passw, user, passw))
                    conn.commit()
            logging.info(f"设备导入成功: 共 {len(df)} 条")
            return jsonify({"success": True, "message": "设备导入成功"})
        except Exception as e:
            logging.error(f"设备导入失败: {str(e)}")
            return jsonify({"success": False, "message": f"导入失败: {str(e)}"}), 500

    @app.route('/schedule_collection', methods=['POST'])
    def schedule_collection():
        try:
            data = request.json
            frequency = data.get('frequency')  # daily, weekly, monthly
            start_date = data.get('start_date')  # YYYY-MM-DD
            end_date = data.get('end_date')  # YYYY-MM-DD
            execution_time = data.get('execution_time')  # HH:MM

            # 验证输入
            if not all([frequency, start_date, end_date, execution_time]):
                logging.warning("缺少必要参数")
                return jsonify({"success": False, "message": "缺少必要参数"}), 400

            if frequency not in ['daily', 'weekly', 'monthly']:
                logging.warning(f"无效的频率: {frequency}")
                return jsonify({"success": False, "message": "无效的频率"}), 400

            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
                # 存储 execution_time 作为字符串（HH:MM:00）
                exec_time_str = f"{execution_time}:00"
                datetime.strptime(exec_time_str, '%H:%M:%S')  # 验证格式
            except ValueError:
                logging.warning("日期或时间格式无效")
                return jsonify({"success": False, "message": "日期或时间格式无效"}), 400

            now = datetime.now().date()
            if start_dt < now or end_dt < start_dt:
                logging.warning("开始日期必须晚于今天，且结束日期必须晚于开始日期")
                return jsonify({"success": False, "message": "开始日期必须晚于今天，且结束日期必须晚于开始日期"}), 400

            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # 删除现有待执行任务（仅允许一个任务）
                    cursor.execute("DELETE FROM scheduled_collections WHERE status = 'pending'")
                    # 插入新任务
                    cursor.execute(
                        """
                        INSERT INTO scheduled_collections (
                            frequency, start_date, end_date, execution_time, status, created_at
                        ) VALUES (%s, %s, %s, %s, 'pending', NOW())
                        """,
                        (frequency, start_date, end_date, exec_time_str)
                    )
                    conn.commit()
            
            logging.info(f"周期性采集任务已设置: 频率={frequency}, 开始={start_date}, 结束={end_date}, 时间={execution_time}")
            return jsonify({
                "success": True,
                "message": f"周期性采集任务已设置: {frequency} 在 {start_date} 至 {end_date} 的 {execution_time}"
            })
        except pymysql.MySQLError as e:
            logging.error(f"设置周期性采集失败: {str(e)}")
            return jsonify({"success": False, "message": f"数据库错误: {str(e)}"}), 500
        except Exception as e:
            logging.error(f"设置周期性采集失败: {str(e)}")
            return jsonify({"success": False, "message": f"系统错误: {str(e)}"}), 500

    @app.route('/cancel_schedule', methods=['POST'])
    def cancel_schedule():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM scheduled_collections WHERE status = 'pending'")
                    deleted_rows = cursor.rowcount
                    conn.commit()
            
            logging.info(f"周期性采集任务已取消: 删除 {deleted_rows} 条记录")
            if deleted_rows > 0:
                return jsonify({"success": True, "message": "周期性采集任务已取消"})
            else:
                return jsonify({"success": True, "message": "无待执行的周期性采集任务"})
        except pymysql.MySQLError as e:
            logging.error(f"取消周期性采集失败: {str(e)}")
            return jsonify({"success": False, "message": f"数据库错误: {str(e)}"}), 500
        except Exception as e:
            logging.error(f"取消周期性采集失败: {str(e)}")
            return jsonify({"success": False, "message": f"系统错误: {str(e)}"}), 500

    @app.route('/get_schedule')
    def get_schedule():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT id, frequency, start_date, end_date, execution_time, status, next_execution
                        FROM scheduled_collections
                        WHERE status = 'pending'
                        ORDER BY created_at DESC
                        LIMIT 1
                    """)
                    schedule = cursor.fetchone()
            
            if schedule:
                # 处理 execution_time，确保为字符串或 time 对象
                execution_time = schedule['execution_time']
                if isinstance(execution_time, timedelta):
                    execution_time = convert_to_time(execution_time)
                elif isinstance(execution_time, str):
                    execution_time = datetime.strptime(execution_time, '%H:%M:%S').time()
                
                logging.info(f"获取周期性任务: ID={schedule['id']}, 频率={schedule['frequency']}, 下一次执行={schedule['next_execution']}")
                return jsonify({
                    "success": True,
                    "id": schedule['id'],
                    "frequency": schedule['frequency'],
                    "start_date": schedule['start_date'].strftime('%Y-%m-%d'),
                    "end_date": schedule['end_date'].strftime('%Y-%m-%d'),
                    "execution_time": execution_time.strftime('%H:%M'),
                    "status": schedule['status'],
                    "next_execution": schedule['next_execution'].strftime('%Y-%m-%d %H:%M:%S') if schedule['next_execution'] else None
                })
            else:
                logging.info("无待执行的周期性任务")
                return jsonify({
                    "success": True,
                    "id": None,
                    "frequency": None,
                    "start_date": None,
                    "end_date": None,
                    "execution_time": None,
                    "status": None,
                    "next_execution": None
                })
        except pymysql.MySQLError as e:
            logging.error(f"获取周期性采集任务失败: {str(e)}")
            return jsonify({"success": False, "message": f"数据库错误: {str(e)}"}), 500
        except Exception as e:
            logging.error(f"获取周期性采集任务失败: {str(e)}")
            return jsonify({"success": False, "message": f"系统错误: {str(e)}"}), 500
    
    @app.route('/get_logs', methods=['GET'])
    def get_logs():
        """Return the contents of collection.log."""
        log_file = 'collection.log'
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                return Response(content, mimetype='text/plain')
            else:
                return Response('Log file not found', status=404, mimetype='text/plain')
        except Exception as e:
            logging.error(f"Failed to read log file: {str(e)}")
            return Response(f'Error reading log file: {str(e)}', status=500, mimetype='text/plain')

    @app.route('/css/<path:filename>')
    def serve_css(filename):
        return send_from_directory('css', filename)

    @app.route('/js/<path:filename>')
    def serve_js(filename):
        return send_from_directory('js', filename)

    @app.route('/webfonts/<path:filename>')
    def serve_webfonts(filename):
        return send_from_directory('webfonts', filename)