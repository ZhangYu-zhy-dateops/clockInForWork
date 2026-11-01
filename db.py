import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def get_db_conn():
    BASE = Path(__file__).parent  # 此文件所在目录
    DB_FILE = BASE / "kaoqin.db"   # 数据库文件名
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        logger.debug(f"[DB_CONN_OK] 连接数据库: {DB_FILE}")
        return conn
    except Exception as e:
        logger.error(f"[DB_CONN_FAIL] 打开数据库失败 {DB_FILE}，err:{str(e)}")
        raise
