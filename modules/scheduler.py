import threading
import time as time_module
import logging
from datetime import datetime
from modules.database import get_db_connection
from modules.sync import sync_terminal_info
from modules.utils import calculate_next_execution
from getarpmac import main as collect_devices  # 改为导入 main 并重命名为 collect_devices

def check_scheduled_collections():
    """后台任务：检查并执行定时采集"""
    logging.info("调度器线程启动")
    while True:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # 获取待执行的周期性任务
                    cursor.execute("""
                        SELECT id, frequency, start_date, end_date, execution_time, next_execution, status
                        FROM scheduled_collections
                        WHERE status = 'pending'
                    """)
                    tasks = cursor.fetchall()
                    logging.info(f"检查到 {len(tasks)} 个待执行任务")

                    for task in tasks:
                        task_id = task['id']
                        next_execution = task['next_execution']
                        now = datetime.now()

                        # 如果任务没有下一次执行时间，计算并更新
                        if not next_execution:
                            next_execution = calculate_next_execution(task)
                            if next_execution:
                                cursor.execute(
                                    "UPDATE scheduled_collections SET next_execution = %s WHERE id = %s",
                                    (next_execution, task_id)
                                )
                                conn.commit()
                                logging.info(f"任务 ID={task_id} 更新下一次执行时间: {next_execution}")
                            else:
                                # 任务已超出时间范围，标记为完成
                                cursor.execute(
                                    "UPDATE scheduled_collections SET status = 'completed', last_executed_at = NOW() WHERE id = %s",
                                    (task_id,)
                                )
                                conn.commit()
                                logging.info(f"任务 ID={task_id} 已超出时间范围，标记为完成")
                                continue

                        # 检查是否到达执行时间
                        if next_execution and now >= next_execution:
                            logging.info(f"执行定时采集: ID={task_id}, 时间={next_execution}")
                            try:
                                # 检查是否已有正在运行的采集任务
                                cursor.execute("SELECT id FROM collection_status WHERE status = 'running'")
                                if cursor.fetchone():
                                    logging.info(f"已有正在运行的采集任务，跳过定时采集 ID={task_id}")
                                    continue

                                # 记录采集任务状态
                                cursor.execute(
                                    "INSERT INTO collection_status (type, status, start_time) VALUES (%s, %s, NOW())",
                                    ('all', 'running')
                                )
                                collection_id = cursor.lastrowid
                                conn.commit()

                                # 执行全网采集
                                collect_result = collect_devices()  # 调用 main 函数
                                sync_stats = sync_terminal_info()

                                # 更新采集任务状态
                                cursor.execute(
                                    """
                                    UPDATE collection_status 
                                    SET status = %s, end_time = NOW(), message = %s 
                                    WHERE id = %s
                                    """,
                                    ('completed' if collect_result['success'] else 'failed', collect_result['message'], collection_id)
                                )

                                # 更新最后执行时间
                                cursor.execute(
                                    "UPDATE scheduled_collections SET last_executed_at = NOW() WHERE id = %s",
                                    (task_id,)
                                )

                                # 计算下一次执行时间
                                next_execution = calculate_next_execution(task)
                                if next_execution:
                                    cursor.execute(
                                        "UPDATE scheduled_collections SET next_execution = %s WHERE id = %s",
                                        (next_execution, task_id)
                                    )
                                    logging.info(f"任务 ID={task_id} 下一次执行时间: {next_execution}")
                                else:
                                    # 任务已超出时间范围，标记为完成
                                    cursor.execute(
                                        "UPDATE scheduled_collections SET status = 'completed', next_execution = NULL WHERE id = %s",
                                        (task_id,)
                                    )
                                    logging.info(f"任务 ID={task_id} 已完成所有执行，标记为完成")

                                conn.commit()

                                device_list = collect_result.get("device_list", [])
                                failed_devices = collect_result.get("failed_devices", [])
                                total_devices = len(device_list)
                                success_count = total_devices - len(failed_devices)
                                message = (
                                    f"定时采集完成 (ID={task_id}): "
                                    f"成功采集 {success_count} 台设备，失败 {len(failed_devices)} 台设备. "
                                    f"数据同步：新增 {sync_stats['insert_count']} 条，变更 {sync_stats['update_count']} 条"
                                )
                                logging.info(message)

                            except Exception as e:
                                # 更新采集任务状态为失败
                                cursor.execute(
                                    """
                                    UPDATE collection_status 
                                    SET status = %s, end_time = NOW(), message = %s 
                                    WHERE id = %s
                                    """,
                                    ('failed', f"定时采集失败: {str(e)}", collection_id)
                                )

                                # 记录失败但不终止任务，继续调度下一次执行
                                logging.error(f"定时采集失败 (ID={task_id}): {str(e)}")
                                next_execution = calculate_next_execution(task)
                                if next_execution:
                                    cursor.execute(
                                        "UPDATE scheduled_collections SET next_execution = %s WHERE id = %s",
                                        (next_execution, task_id)
                                    )
                                    logging.info(f"任务 ID={task_id} 下一次执行时间: {next_execution}")
                                else:
                                    cursor.execute(
                                        "UPDATE scheduled_collections SET status = 'completed', next_execution = NULL WHERE id = %s",
                                        (task_id,)
                                    )
                                    logging.info(f"任务 ID={task_id} 已完成所有执行，标记为完成")
                                conn.commit()

        except Exception as e:
            logging.error(f"检查定时采集失败: {str(e)}")
            # 继续循环，避免线程退出
            time_module.sleep(60)  # 每分钟检查一次
            continue

        time_module.sleep(60)  # 每分钟检查一次

def start_scheduler():
    """启动调度器线程"""
    scheduler_thread = threading.Thread(target=check_scheduled_collections, daemon=True)
    scheduler_thread.start()
    logging.info("调度器线程已启动")