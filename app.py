from flask import Flask
import logging
from modules.database import initialize_database
from modules.scheduler import start_scheduler
from modules.routes import register_routes

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

if __name__ == '__main__':
    try:
        initialize_database()
        logging.info("数据库初始化完成")
        start_scheduler()
        register_routes(app)
        app.run(debug=True, host="0.0.0.0", port=8686)
    except Exception as e:
        logging.error(f"应用程序启动失败: {str(e)}")
        raise