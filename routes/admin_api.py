# routes\admin_api.py
# 管理员后台接口蓝图：登录校验、密码修改、考勤数据查询、预览、Excel导出、报表分页
from datetime import date, datetime, timedelta
import hmac, hashlib
from calendar import monthrange
from flask import Blueprint, request, jsonify, session, send_file
from openpyxl import Workbook
from openpyxl.styles import PatternFill
from io import BytesIO
from config import SECRET_SALT,MAX_FAIL,LOCK_MINUTES
from db import get_db_conn
import logging
from threading import Lock

logger = logging.getLogger(__name__)
# 内存登录锁缓存（辅助，核心锁数据持久化在admin_login_lock表）
login_limit = {}
# 登录计数并发锁，防止多请求同时修改计数
login_lock = Lock()
admin_api_bp = Blueprint("admin_api", __name__)


# ==================== 数据库通用封装（精简重复conn/cursor） ====================
def query_one(sql, params=()):
    """查询单条记录"""
    conn = get_db_conn()
    cur = conn.cursor()
    r = cur.execute(sql, params).fetchone()
    conn.close()
    return r

def query_all(sql, params=()):
    """查询多条记录，返回元组列表"""
    conn = get_db_conn()
    cur = conn.cursor()
    rs = cur.execute(sql, params).fetchall()
    conn.close()
    return rs

def execute(sql, params=()):
    """执行写操作：insert/update/delete，自动提交"""
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    conn.close()


# ==================== 业务小工具复用 ====================
def is_admin():
    """判断当前会话是否为管理员"""
    return session.get("is_admin", False)

def calc_hash(pwd: str):
    """hmac-sha256 密码哈希计算，加盐"""
    return hmac.new(SECRET_SALT, pwd.encode("utf-8"), hashlib.sha256).hexdigest()

def get_admin_hash():
    """读取配置表中保存的管理员密码哈希值"""
    r = query_one("SELECT v FROM config WHERE k='admin_pwd'")
    return r[0] if r else ""

def get_staff_by_uids(uids=None):
    """
    获取员工列表
    :param uids: 可选，指定工号数组，不传则返回全部员工
    """
    if uids and len(uids) > 0:
        ph = ",".join(["?"] * len(uids))
        return query_all(f"SELECT uid,name FROM staff WHERE uid IN ({ph}) ORDER BY uid", uids)
    return query_all("SELECT uid,name FROM staff ORDER BY uid")

def build_day_map(ym_str):
    """
    构造月度打卡状态字典
    返回结构：{uid: {"day": {"in":bool, "out":bool}}}
    """
    sql = '''
    SELECT uid, strftime('%d', date(clock_dt)) AS day_num, clock_type
    FROM clock
    WHERE strftime('%Y-%m', date(clock_dt)) = ?
    '''
    raw_rows = query_all(sql, (ym_str,))
    day_map = {}
    for uid, day_num, ctype in raw_rows:
        uid_key = str(uid)
        day_map.setdefault(uid_key, {}).setdefault(day_num, {"in": False, "out": False})
        if ctype == "in":
            day_map[uid_key][day_num]["in"] = True
        elif ctype == "out":
            day_map[uid_key][day_num]["out"] = True
    return day_map


# ==================== 登录锁逻辑 ====================
def clean_expire_lock():
    """清理数据库中已经过期的登录锁定记录"""
    now = datetime.now().isoformat()
    execute("DELETE FROM admin_login_lock WHERE lock_until < ?", (now,))
    logger.info(f"[CLEAN] 清理过期锁记录完成")

def check_login_lock(ip):
    """
    检查IP是否处于登录锁定状态
    返回 (是否锁定, 剩余秒数)
    """
    now = datetime.now()
    row = query_one("SELECT fail_count, lock_until FROM admin_login_lock WHERE ip = ?", (ip,))
    logger.info(f"[CHECK] ip={ip}, db_row={row}")
    if not row:
        return False, 0
    cnt, lock_str = row
    if cnt < MAX_FAIL:
        return False, 0
    lock_time = datetime.fromisoformat(lock_str)
    if now < lock_time:
        remain = int((lock_time - now).total_seconds())
        return True, remain
    return False, 0

def add_fail_count(ip):
    """登录失败，累加错误次数；达到阈值后设置锁定时间"""
    now = datetime.now()
    row = query_one("SELECT fail_count FROM admin_login_lock WHERE ip = ?", (ip,))
    cnt = row[0] + 1 if row else 1
    if cnt >= MAX_FAIL:
        lock_until = (now + timedelta(minutes=LOCK_MINUTES)).isoformat()
    else:
        lock_until = (now + timedelta(days=365)).isoformat()
    if row:
        execute("UPDATE admin_login_lock SET fail_count=?, lock_until=? WHERE ip=?", (cnt, lock_until, ip))
    else:
        execute("INSERT INTO admin_login_lock(ip, fail_count, lock_until) VALUES (?, ?, ?)", (ip, cnt, lock_until))
    logger.info(f"[ADD] ip={ip} 错误次数更新")
    return cnt

def reset_fail_count(ip: str):
    """登录成功，清除该IP的失败计数与锁定记录"""
    execute("DELETE FROM admin_login_lock WHERE ip = ?", (ip,))
    logger.info(f"[RESET] ip={ip} 清除失败计数")


# ==================== 考勤矩阵 ====================
def build_attendance_matrix(y: int, m: int):
    """
    生成月度考勤矩阵
    输出表头、每行员工的每日打卡状态（正常/缺下班卡/缺上班卡/全天缺卡）
    附带正常天数、异常天数统计
    """
    ym_str = f"{y:04d}-{m:02d}"
    _, total_days = monthrange(y, m)
    days_key = [f"{d:02d}" for d in range(1, total_days + 1)]
    week_list = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    days_label = []
    for d in range(1, total_days + 1):
        dt = date(y, m, d)
        w_name = week_list[dt.weekday()]
        days_label.append(f"{d:02d}日\n({w_name})")
    header_label = ["姓名", "正常天数", "异常天数"] + days_label

    staff_list = get_staff_by_uids()
    day_map = build_day_map(ym_str)

    rows_list = []
    for uid, name in staff_list:
        uid_key = str(uid)
        day_status_arr = []
        for dk in days_key:
            info = day_map.get(uid_key, {}).get(dk, {"in": False, "out": False})
            has_in, has_out = info["in"], info["out"]
            if has_in and has_out:
                st = "正常"
            elif has_in:
                st = "缺下班卡"
            elif has_out:
                st = "缺上班卡"
            else:
                st = "全天缺卡"
            day_status_arr.append(st)
        cnt_normal = day_status_arr.count("正常")
        cnt_abnormal = sum(1 for s in day_status_arr if s in ("缺下班卡", "缺上班卡", "全天缺卡"))
        line = [name, cnt_normal, cnt_abnormal] + day_status_arr
        rows_list.append(line)
    return header_label, rows_list


# ==================== 路由接口 ====================
@admin_api_bp.route("/admin_login", methods=["POST"])
def admin_login():
    """管理员登录接口，密码校验+暴力破解锁定"""
    client_ip = request.remote_addr
    data = request.get_json()
    pwd = data.get("pwd", "")

    clean_expire_lock()
    locked, remain_sec = check_login_lock(client_ip)
    if locked:
        logger.warning(f"[ADMIN_LOGIN_LOCKED] IP:{client_ip} 还在锁定中，剩余{remain_sec}秒")
        return jsonify({"ok": False, "msg": f"密码错误次数过多，请{remain_sec//60}分{remain_sec%60}秒后重试！"})

    if calc_hash(pwd) == get_admin_hash():
        reset_fail_count(client_ip)
        session["is_admin"] = True
        session.permanent = True
        logger.info(f"[ADMIN_LOGIN_OK] IP:{client_ip} 管理员登录成功")
        return jsonify({"ok": True})

    current_cnt = add_fail_count(client_ip)
    left = MAX_FAIL - current_cnt
    logger.warning(f"[ADMIN_LOGIN_FAIL] IP:{client_ip} 密码错误，剩余可尝试次数：{left}")
    return jsonify({"ok": False, "msg": f"密码错误，还剩{left}次机会！"})


@admin_api_bp.route("/api_change_pwd", methods=["POST"])
def api_change_pwd():
    """管理员修改密码接口，校验原密码、两次新密码一致性"""
    client_ip = request.remote_addr
    if not is_admin():
        logger.warning(f"[CHANGE_PWD_FORBIDDEN] IP:{client_ip} 未登录尝试修改密码")
        return jsonify({"ok": False, "msg": "无权限，请刷新页面后重新登陆！"}), 403

    data = request.get_json()
    old = data.get("old_pwd", "")
    new = data.get("new_pwd", "").strip()
    confirm = data.get("confirm_pwd", "").strip()

    if calc_hash(old) != get_admin_hash():
        logger.warning(f"[CHANGE_PWD_WRONG_OLD] IP:{client_ip} 原密码校验失败")
        return jsonify({"ok": False, "msg": "原密码不对"})
    if not new:
        logger.info(f"[CHANGE_PWD_EMPTY] IP:{client_ip} 新密码为空")
        return jsonify({"ok": False, "msg": "新密码不能为空"})
    if new != confirm:
        logger.info(f"[CHANGE_PWD_NOT_MATCH] IP:{client_ip} 两次输入密码不一致")
        return jsonify({"ok": False, "msg": "两次密码不一致"})

    h = calc_hash(new)
    execute("UPDATE config SET v=? WHERE k='admin_pwd'", (h,))
    logger.info(f"[CHANGE_PWD_OK] IP:{client_ip} 管理员密码修改成功")
    return jsonify({"ok": True, "msg": "修改成功"})


@admin_api_bp.route("/api_admin_exist_year", methods=["POST"])
def api_admin_exist_year():
    """查询数据库里存在打卡记录的所有年份，用于下拉选择"""
    if not is_admin():
        return jsonify({"ok": False, "msg": "无权限，请刷新页面后重新登陆！"}), 403
    rows = query_all("SELECT DISTINCT strftime('%Y', date(clock_dt)) y FROM clock ORDER BY y DESC")
    logger.info(f"[GET_YEAR_OK] 获取已有打卡年份列表成功，years={[x[0] for x in rows]}")
    return jsonify({"ok": True, "years": [str(x[0]) for x in rows]})


@admin_api_bp.route("/api_admin_records_month", methods=["POST"])
def api_admin_records_month():
    """按月查询员工每日打卡状态矩阵（前端报表页面预览用），支持按员工筛选"""
    if not is_admin():
        return jsonify({"ok": False, "msg": "无权限，请刷新页面后重新登陆！"}), 403
    data = request.get_json()
    y = data.get("year")
    m = data.get("month")
    uids = data.get("uids", [])
    try:
        y_int = int(y)
        m_int = int(m)
        ym_str = f"{y_int:04d}-{m_int:02d}"
    except Exception as e:
        logger.warning(f"[MONTH_RECORD_PARAM_ERR] 参数年月解析失败, y={y},m={m},err={str(e)}")
        return jsonify({"ok": False, "msg": "年月错误"})

    staff_list = get_staff_by_uids(uids)
    day_map = build_day_map(ym_str)
    _, total_days = monthrange(y_int, m_int)
    days_key = [f"{d:02d}" for d in range(1, total_days + 1)]

    result_list = []
    for uid, name in staff_list:
        uid_key = str(uid)
        day_arr = []
        for dk in days_key:
            info = day_map.get(uid_key, {}).get(dk, {"in": False, "out": False})
            has_in, has_out = info["in"], info["out"]
            if has_in and has_out:
                st = "正常"
            elif has_in:
                st = "缺下班卡"
            elif has_out:
                st = "缺上班卡"
            else:
                st = "全天缺卡"
            day_arr.append({"day": dk, "status": st})
        result_list.append({"uid": uid, "name": name, "day_list": day_arr})
    logger.info(f"[MONTH_RECORD_OK] 月度打卡矩阵查询成功 ym={ym_str}, uids={uids}, count={len(result_list)}")
    return jsonify({"ok": True, "list": result_list})


@admin_api_bp.route("/api_admin_preview_clock", methods=["POST"])
def api_admin_preview_clock():
    """打卡流水预览接口，返回原始打卡明细（上班/下班文字转换）"""
    if not is_admin():
        return jsonify({"ok": False, "msg": "无权限，请刷新页面后重新登陆！"}), 403
    data = request.get_json()
    y = data.get("year")
    m = data.get("month")
    uids = data.get("uids", [])
    try:
        ym = f"{int(y):04d}-{int(m):02d}"
    except Exception as e:
        logger.warning(f"[PREVIEW_PARAM_ERR] 年月解析失败 y={y},m={m},err={str(e)}")
        return jsonify({"ok": False, "msg": "年月错误"})

    staff_list = get_staff_by_uids(uids)
    staff_map = {str(r[0]): r[1] for r in staff_list}
    sql_clock = '''
    SELECT uid, clock_dt, clock_type
    FROM clock
    WHERE strftime('%Y-%m', date(clock_dt))=?
    ORDER BY id DESC
    '''
    clock_rows = query_all(sql_clock, (ym,))
    type_map = {"in": "上班", "out": "下班"}
    group = {}
    for uid, clock_dt, ctype in clock_rows:
        uk = str(uid)
        if uk not in staff_map:
            continue
        group.setdefault(uk, []).append({
            "uid": uid,
            "name": staff_map[uk],
            "clock_dt": str(clock_dt),
            "clock_type": type_map.get(ctype, str(ctype))
        })
    records = []
    for uid, _ in staff_list:
        uk = str(uid)
        if uk not in group:
            continue
        records.extend(group[uk])
    logger.info(f"[PREVIEW_OK] 打卡流水预览成功 ym={ym}, uids={uids}, record_count={len(records)}")
    return jsonify({"ok": True, "records": records})


@admin_api_bp.route("/api_admin_export_clock", methods=["POST"])
def api_admin_export_clock():
    """导出原始打卡流水Excel：工号、姓名、打卡时间、打卡类型"""
    if not is_admin():
        return jsonify({"ok": False, "msg": "无权限，请刷新页面后重新登陆！"}), 403
    data = request.get_json()
    y = data.get("year")
    m = data.get("month")
    uids = data.get("uids", [])
    try:
        ym = f"{int(y):04d}-{int(m):02d}"
    except Exception as e:
        logger.warning(f"[EXPORT_CLOCK_PARAM_ERR] 年月解析失败 y={y},m={m},err={str(e)}")
        return jsonify({"ok": False, "msg": "年月错误"})

    staff_list = get_staff_by_uids(uids)
    staff_map = {str(r[0]): r[1] for r in staff_list}
    clock_rows = query_all("""
        SELECT uid, clock_dt, clock_type
        FROM clock
        WHERE strftime('%Y-%m', date(clock_dt)) = ?
        ORDER BY id DESC
    """, (ym,))
    group = {}
    type_map = {"in": "上班", "out": "下班"}
    for uid, clock_dt, ctype in clock_rows:
        uk = str(uid)
        if uk not in staff_map:
            continue
        group.setdefault(uk, []).append((uid, staff_map[uk], clock_dt, ctype))

    wb = Workbook()
    ws = wb.active
    ws.title = "打卡流水"
    ws.append(["工号", "姓名", "打卡时间", "打卡类型"])
    for uid, name in staff_list:
        uk = str(uid)
        if uk not in group:
            continue
        for row_data in group[uk]:
            uid_val, name_val, dt_val, ctype_val = row_data
            show_type = type_map.get(ctype_val, str(ctype_val))
            ws.append([str(uid_val), str(name_val), str(dt_val), show_type])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    logger.info(f"[EXPORT_CLOCK_OK] 导出流水 {ym}, 筛选uid={uids}")
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@admin_api_bp.route("/api_report_data", methods=["POST"])
def api_report_data():
    """考勤报表分页查询接口，返回带表头、分页信息的矩阵数据"""
    try:
        if not is_admin():
            return jsonify({"ok": False, "msg": "无管理员权限"}), 403
        data = request.get_json()
        y = int(data.get("year"))
        m = int(data.get("month"))
        page = int(data.get("page", 1))
        page_size = 10

        header_label, rows_list = build_attendance_matrix(y, m)
        total = len(rows_list)
        total_page = (total + page_size - 1) // page_size
        start = (page - 1) * page_size
        end = start + page_size
        page_rows = rows_list[start:end]

        obj_list = []
        for row in page_rows:
            row_obj = {}
            for col_name, val in zip(header_label, row):
                row_obj[col_name] = str(val)
            obj_list.append(row_obj)
        logger.info(f"[REPORT_DATA_OK] 考勤报表分页查询成功 {y}-{m}, page={page}, total={total}")
        return jsonify({
            "ok": True,
            "header_label": header_label,
            "list": obj_list,
            "total": total,
            "total_page": total_page,
            "current_page": page
        })
    except Exception as e:
        logger.error("[REPORT_DATA_EXCEPTION]", exc_info=True)
        return jsonify({"ok": False, "msg": f"报表异常：{str(e)}"})


@admin_api_bp.route("/api_report_export", methods=["POST"])
def api_report_export():
    """导出月度考勤汇总报表Excel，带条件底色、自动换行、边框、自适应行高"""
    try:
        if not is_admin():
            return jsonify({"ok": False, "msg": "无管理员权限"}), 403
        data = request.get_json()
        y = int(data.get("year"))
        m = int(data.get("month"))
        header_label, rows_list = build_attendance_matrix(y, m)

        # 状态对应的填充色
        fill_normal = PatternFill(start_color="d1fae5", end_color="d1fae5", fill_type="solid")
        fill_missout = PatternFill(start_color="fef3c7", end_color="fef3c7", fill_type="solid")
        fill_missin = PatternFill(start_color="fed7aa", end_color="fed7aa", fill_type="solid")
        fill_missall = PatternFill(start_color="fecdd3", end_color="fecdd3", fill_type="solid")

        from openpyxl.styles import Border, Side, Alignment
        from openpyxl.utils import get_column_letter
        import math

        thin = Side(style="thin", color="000000")
        border_all = Border(left=thin, right=thin, top=thin, bottom=thin)
        align_center_wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)

        wb = Workbook()
        ws = wb.active
        ws.title = "月度考勤汇总"
        ws.append(header_label)

        # 设置固定列宽
        col_widths = {}
        for ci, _ in enumerate(header_label, start=1):
            if ci == 1:
                col_widths[ci] = 8
            elif ci in (2, 3):
                col_widths[ci] = 6
            else:
                col_widths[ci] = 6
            ws.column_dimensions[get_column_letter(ci)].width = col_widths[ci]

        # 填充内容、边框、对齐、状态底色
        for row_idx, row in enumerate(rows_list, start=2):
            ws.append(row)
            for col_idx, val in enumerate(row, start=1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.border = border_all
                cell.alignment = align_center_wrap
                if col_idx >= 5:
                    if val == "正常":
                        cell.fill = fill_normal
                    elif val == "缺下班卡":
                        cell.fill = fill_missout
                    elif val == "缺上班卡":
                        cell.fill = fill_missin
                    elif val == "全天缺卡":
                        cell.fill = fill_missall

        # 表头样式
        for col_idx in range(1, len(header_label) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.border = border_all
            cell.alignment = align_center_wrap

        # 根据换行文字自动计算行高
        for row_idx in range(1, ws.max_row + 1):
            max_lines = 1
            for col_idx in range(1, len(header_label) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                val = str(cell.value or "")
                width = col_widths[col_idx]
                per_line = max(1, int(width / 2))
                lines = 0
                for seg in val.split("\n"):
                    lines += max(1, math.ceil(len(seg) / per_line))
                max_lines = max(max_lines, lines)
            ws.row_dimensions[row_idx].height = max_lines * 16 + 4

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        logger.info(f"[EXPORT_REPORT_OK] 导出月度报表 {y}-{m}")
        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        logger.error("[REPORT_EXPORT_EXCEPTION]", exc_info=True)
        return jsonify({"ok": False, "msg": f"导出异常：{str(e)}"})
