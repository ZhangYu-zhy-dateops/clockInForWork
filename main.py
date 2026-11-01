# main.py
import subprocess, ctypes, socket, time, sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
# 从配置文件读取日志、NTP相关参数
from config import LOG_FILE,LOG_MAX_SIZE,LOG_BACKUP_COUNT,NTP_HOST,ALLOW_TIME_ERROR,MAX_WAIT_SEC,LOOP_WAIT


# 获取根日志对象，全局共用
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# 只在没有handler的时候初始化，避免重复添加
if not root_logger.handlers:
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_SIZE,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    stream_handler = logging.StreamHandler()
    # 日志格式：时间 [级别] 模块名 | 消息
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s | %(message)s")
    file_handler.setFormatter(fmt)
    stream_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)


def get_ntp_ts():
    """从NTP服务器获取标准时间戳"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(b'\x1b' + 47*b'\0', (NTP_HOST, 123))
        resp, _ = s.recvfrom(1024)
        return int.from_bytes(resp[40:44], "big") - 2208988800
    except Exception as e:
        root_logger.error("获取NTP时间异常", exc_info=True)
        return None
    finally:
        s.close()


def set_win_time(ts):
    """修改Windows系统时间，需要管理员权限"""
    if not ctypes.windll.shell32.IsUserAnAdmin():
        root_logger.warning("不是管理员，无法修改系统时间")
        return False
    t = datetime.fromtimestamp(ts)
    try:
        for cmd in (["date", t.strftime("%Y-%m-%d")], ["time", t.strftime("%H:%M:%S")]):
            ret = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            if ret.returncode != 0:
                root_logger.error(f"设置系统时间命令失败: {cmd}, stderr={ret.stderr}")
                return False
        root_logger.info("系统时间校正成功")
        return True
    except Exception as e:
        root_logger.error("执行修改系统时间异常", exc_info=True)
        return False


def check_time_ok(target_ts):
    """判断本机时间和标准NTP时间偏差是否在允许范围内"""
    return abs(target_ts - time.time()) <= ALLOW_TIME_ERROR


if __name__ == "__main__":
    root_logger.info("===== 等待网络就绪 + 时间校验 开始 =====")
    start = time.time()
    while True:
        root_logger.info("尝试获取NTP时间")

        # 超过最大等待时长，直接退出
        if time.time() - start > MAX_WAIT_SEC:
            root_logger.warning("等待外网超过1小时，超时退出！")
            input("按回车关闭程序...")
            sys.exit(1)

        target_ts = get_ntp_ts()
        if target_ts is None:
            root_logger.warning("NTP连接失败，等待网络，稍后重试")
            time.sleep(LOOP_WAIT)
            continue

        local_ts = time.time()
        diff = abs(target_ts - local_ts)
        nt = datetime.fromtimestamp(target_ts).strftime("%Y-%m-%d %H:%M:%S")
        lt = datetime.fromtimestamp(local_ts).strftime("%Y-%m-%d %H:%M:%S")
        root_logger.info(f"NTP:{nt} | 本机:{lt} | 差值:{diff:.1f}s")

        # 偏差超标，执行校正
        if diff > ALLOW_TIME_ERROR:
            root_logger.warning(f"偏差超过{ALLOW_TIME_ERROR}秒，执行时间校正")
            set_win_time(target_ts)

        # 校验通过，跳出循环启动Flask
        if check_time_ok(target_ts):
            root_logger.info("✅✅ 时间校验通过，准备启动Flask")
            break
        root_logger.warning("校验失败，时间未校正成功，请排查管理员权限或网络")
        time.sleep(LOOP_WAIT)

    # 时间校验通过后才导入app、初始化数据库，防止时间错乱写入脏数据
    from app import app
    try:
        app.run(host="0.0.0.0", port=5000, debug=False,use_reloader=False)
    except Exception as e:
        root_logger.error("Flask服务启动异常", exc_info=True)
        input("Flask服务异常，按回车关闭...")
