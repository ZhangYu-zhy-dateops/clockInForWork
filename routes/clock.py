# routes\clock.py
from datetime import datetime
import random
import os
from flask import Blueprint, request, jsonify, render_template
from config import CLOCK_TIME_WINDOWS
from db import get_db_conn
from openpyxl import load_workbook
import logging

# 获取当前模块日志实例，和项目统一日志输出
logger = logging.getLogger(__name__)

# 注册打卡模块蓝图
clock_bp = Blueprint("clock", __name__)

# 兜底激励文案列表，Excel读取失败时使用
MOTIVATION_LIST = [
    "打卡成功，今日继续加油！",
    "按时打卡，认真工作👍",
    "记录出勤，保持自律！",
    "新的一天，好好努力",
    "打卡完成，万事顺意",
    "坚持打卡，稳步前行",
]

# 激励文案Excel文件路径
TIPS_XLSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tips.xlsx")
# 文案缓存：记录文件修改时间和文案内容，避免重复读取文件
_tip_cache = {"mtime": 0, "tips": []}


def load_tips_from_excel():
    """读取激励文案Excel，文件未变更直接走缓存，文件不存在/读取异常返回兜底文案"""
    if not os.path.exists(TIPS_XLSX_PATH):
        return MOTIVATION_LIST
    try:
        mtime = os.path.getmtime(TIPS_XLSX_PATH)
        if mtime == _tip_cache["mtime"]:
            return _tip_cache["tips"]
        wb = load_workbook(TIPS_XLSX_PATH, read_only=True)
        ws = wb.active
        tips = []
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=1, values_only=True):
            val = row[0]
            if val is not None and str(val).strip() != "":
                tips.append(str(val).strip())
        wb.close()
        if not tips:
            tips = MOTIVATION_LIST
        _tip_cache["mtime"] = mtime
        _tip_cache["tips"] = tips
        return tips
    except Exception as e:
        logger.warning(f"[TIPS_LOAD_FAIL] 读取tips.xlsx异常: {str(e)}")
        return MOTIVATION_LIST


def get_random_tip():
    """随机获取一条激励文案"""
    tips = load_tips_from_excel()
    return random.choice(tips)


def get_uid_name_by_dev(dev: str):
    """根据设备ID查询绑定员工，返回(数据库连接, 查询结果行)；row为None代表设备未绑定员工"""
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT uid,name FROM staff WHERE bind_device_id=?", (dev,))
    row = cur.fetchone()
    return conn, row


def get_today_clock_info(uid: str, conn):
    """查询该员工今日打卡记录，拆分上班、下班两组时间，返回 list_in, list_out"""
    cur = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute("""
        SELECT clock_type, clock_dt FROM clock
        WHERE uid=? AND date(clock_dt) = ?
        ORDER BY clock_dt ASC
    """, (uid, today))
    rows = cur.fetchall()
    list_in = [r[1] for r in rows if r[0] == "in"]
    list_out = [r[1] for r in rows if r[0] == "out"]
    return list_in, list_out


@clock_bp.route("/check")
def check():
    """打卡前置校验接口：判断设备是否绑定、当前时段是否允许打卡、今日是否已打上下班卡"""
    client_ip = request.remote_addr
    dev = request.args.get("dev")
    if not dev:
        logger.warning(f"[CHECK_PARAM_ERR] IP:{client_ip} dev参数缺失")
        return jsonify({"ok": False, "msg": "参数缺失"})

    now = datetime.now()
    current_time = now.time()
    clock_type = None
    # 匹配当前属于上班窗口还是下班窗口
    for ctype, (start_str, end_str) in [("in", CLOCK_TIME_WINDOWS["in"]), ("out", CLOCK_TIME_WINDOWS["out"])]:
        start_t = datetime.strptime(start_str, "%H:%M").time()
        end_t = datetime.strptime(end_str, "%H:%M").time()
        if start_t <= current_time <= end_t:
            clock_type = ctype
            break

    conn, row = get_uid_name_by_dev(dev)
    if row:
        uid, name = row
        list_in, list_out = get_today_clock_info(uid, conn)
        has_in = len(list_in) > 0
        has_out = len(list_out) > 0
        conn.close()
        # logger.info(f"[CHECK_OK] IP:{client_ip} dev={dev} uid={uid} 可打卡:{clock_type is not None} type={clock_type}")
        return jsonify({
            "ok": True,
            "uid": uid,
            "name": name,
            "can_clock": clock_type is not None,
            "current_clock_type": clock_type,
            "has_in": has_in,
            "has_out": has_out
        })
    conn.close()
    logger.info(f"[CHECK_UNBIND] IP:{client_ip} dev={dev} 设备未绑定")
    return jsonify({
        "ok": False,
        "can_clock": clock_type is not None,
        "current_clock_type": clock_type
    })


@clock_bp.route("/api_last_today_clock")
def api_last_today_clock():
    """查询该设备绑定员工今日最后一次打卡时间，前端用于展示最近打卡"""
    client_ip = request.remote_addr
    dev = request.args.get("dev")
    if not dev:
        logger.warning(f"[LAST_CLOCK_PARAM_ERR] IP:{client_ip} dev参数缺失")
        return jsonify({"ok": False, "msg": "参数缺失"})
    conn, row = get_uid_name_by_dev(dev)
    if not row:
        conn.close()
        logger.info(f"[LAST_CLOCK_UNBIND] IP:{client_ip} dev={dev} 设备未绑定")
        return jsonify({"ok": False, "msg": "设备未绑定"})
    uid, _ = row
    list_in, list_out = get_today_clock_info(uid, conn)
    conn.close()
    all_clock = list_in + list_out
    last_time = None
    if all_clock:
        all_clock.sort()
        last_time = all_clock[-1]
    logger.info(f"[LAST_CLOCK_OK] IP:{client_ip} dev={dev} uid={uid}")
    return jsonify({
        "ok": True,
        "last_time": last_time,
        "has_in": len(list_in) > 0,
        "has_out": len(list_out) > 0
    })


@clock_bp.route("/bind", methods=["POST"])
def bind():
    """设备绑定接口：将设备ID绑定到指定工号，新设备会覆盖原有绑定"""
    client_ip = request.remote_addr
    data = request.get_json()
    dev = data.get("dev")
    uid = data.get("uid")
    if not dev or not uid:
        logger.warning(f"[BIND_PARAM_ERR] IP:{client_ip} dev/uid为空")
        return jsonify({"ok": False, "msg": "参数不能为空"})
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM staff WHERE uid=?", (uid,))
    if not cur.fetchone():
        conn.close()
        logger.warning(f"[BIND_NOT_EXIST] IP:{client_ip} uid={uid} 工号不存在")
        return jsonify({"ok": False, "msg": "该工号不存在"})
    cur.execute("UPDATE staff SET bind_device_id=? WHERE uid=?", (dev, uid))
    conn.commit()
    conn.close()
    logger.info(f"[BIND_OK] IP:{client_ip} dev={dev} 绑定uid={uid}")
    return jsonify({"ok": True})


@clock_bp.route("/clock", methods=["POST"])
def clock():
    """核心打卡接口：时段校验、防重复打卡、短间隔防连击，写入打卡记录"""
    client_ip = request.remote_addr
    data = request.get_json()
    dev = data.get("dev")
    if not dev:
        logger.warning(f"[CLOCK_PARAM_ERR] IP:{client_ip} dev参数缺失")
        return jsonify({"ok": False, "msg": "参数缺失"})
    conn, row = get_uid_name_by_dev(dev)
    if not row:
        conn.close()
        logger.warning(f"[CLOCK_UNBIND] IP:{client_ip} dev={dev} 未绑定尝试打卡")
        return jsonify({"ok": False, "msg": "设备未绑定"})
    uid, staff_name = row

    now = datetime.now()
    current_time = now.time()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    clock_type = None
    for ctype, (start_str, end_str) in [("in", CLOCK_TIME_WINDOWS["in"]), ("out", CLOCK_TIME_WINDOWS["out"])]:
        start_t = datetime.strptime(start_str, "%H:%M").time()
        end_t = datetime.strptime(end_str, "%H:%M").time()
        if start_t <= current_time <= end_t:
            clock_type = ctype
            break
    if clock_type is None:
        conn.close()
        logger.warning(f"[CLOCK_TIME_DENY] IP:{client_ip} dev={dev} uid={uid} 不在打卡时段")
        return jsonify({"ok": False, "msg": "不在允许打卡时间段内"})

    list_in, list_out = get_today_clock_info(uid, conn)
    if clock_type == "in" and len(list_in) >= 1:
        conn.close()
        logger.warning(f"[CLOCK_DUP_IN] IP:{client_ip} dev={dev} uid={uid} 重复上班打卡")
        return jsonify({"ok": False, "msg": "今日上班卡已经打过了"})
    if clock_type == "out" and len(list_out) >= 1:
        conn.close()
        logger.warning(f"[CLOCK_DUP_OUT] IP:{client_ip} dev={dev} uid={uid} 重复下班打卡")
        return jsonify({"ok": False, "msg": "今日下班卡已经打过了"})

    all_clock = list_in + list_out
    if all_clock:
        last_dt = datetime.strptime(all_clock[-1], "%Y-%m-%d %H:%M:%S")
        if (now - last_dt).total_seconds() < 60:
            conn.close()
            logger.warning(f"[CLOCK_FAST] IP:{client_ip} dev={dev} uid={uid} 间隔过短，防重复")
            return jsonify({"ok": False, "msg": "间隔太短，请勿重复打卡"})

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO clock(uid, staff_name, clock_dt, clock_type)
        VALUES (?, ?, ?, ?)
    """, (uid, staff_name, now_str, clock_type))
    conn.commit()
    conn.close()

    tip = get_random_tip()
    logger.info(f"[CLOCK_OK] 打卡成功, IP:{client_ip} dev={dev} uid={uid} name={staff_name} type={clock_type} dt={now_str} ")
    return jsonify({
        "ok": True,
        "time": now_str,
        "tip": tip,
        "clock_type": clock_type
    })


@clock_bp.route("/list")
def show_list():
    """员工个人打卡记录页面渲染入口"""
    return render_template("list.html")


@clock_bp.route("/api_my_exist_year", methods=["POST"])
def api_my_exist_year():
    """员工个人端：查询自己有打卡记录的年份，下拉框使用"""
    client_ip = request.remote_addr
    data = request.get_json()
    dev = data.get("dev")
    if not dev:
        logger.warning(f"[MY_YEAR_PARAM_ERR] IP:{client_ip} dev参数缺失")
        return jsonify({"ok": False, "msg": "参数缺失"})
    conn, row = get_uid_name_by_dev(dev)
    if not row:
        conn.close()
        logger.info(f"[MY_YEAR_UNBIND] IP:{client_ip} dev={dev} 未绑定")
        return jsonify({"ok": False, "msg": "设备未绑定"})
    uid, _ = row
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT strftime('%Y', date(clock_dt)) y FROM clock WHERE uid=? ORDER BY y DESC", (uid,))
    rows = cur.fetchall()
    conn.close()
    logger.info(f"[MY_YEAR_OK] 年份列出成功, IP:{client_ip} dev={dev} uid={uid}")
    return jsonify({"ok": True, "years": [x[0] for x in rows]})


@clock_bp.route("/api_my_records_month", methods=["POST"])
def api_my_records_month():
    """员工个人端：按月查询自己打卡流水记录，倒序返回"""
    client_ip = request.remote_addr
    data = request.get_json()
    dev = data.get("dev")
    year = data.get("year")
    month = data.get("month")

    if not dev or not year or not month:
        logger.warning(f"[MY_REC_PARAM_ERR] IP:{client_ip} 参数缺失 dev={dev} y={year} m={month}")
        return jsonify({"ok": False, "msg": "参数缺失"})

    try:
        year = int(year)
        month = int(month)
    except ValueError:
        logger.warning(f"[MY_REC_YM_ERR] IP:{client_ip} 年月格式错误 y={year} m={month}")
        return jsonify({"ok": False, "msg": "年月参数格式错误"})

    conn, row = get_uid_name_by_dev(dev)
    if not row:
        conn.close()
        logger.info(f"[MY_REC_UNBIND] IP:{client_ip} dev={dev} 未绑定")
        return jsonify({"ok": False, "msg": "设备未绑定"})
    uid, _ = row
    month_str = f"{year}-{month:02d}"
    cur = conn.cursor()
    cur.execute("""
        SELECT clock_type, clock_dt FROM clock
        WHERE uid=? AND strftime('%Y-%m', date(clock_dt)) = ?
        ORDER BY clock_dt DESC
    """, (uid, month_str))
    rows = cur.fetchall()
    conn.close()

    records = []
    for r in rows:
        dt_str = r[1]
        records.append({
            "type": r[0],
            "dt": dt_str
        })
    logger.info(f"[MY_REC_OK] 个人打卡记录查询成功, IP:{client_ip} dev={dev} uid={uid} ym={month_str}")
    return jsonify({
        "ok": True,
        "records": records
    })
