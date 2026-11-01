# routes\staff.py
import re
import logging
from flask import Blueprint, request, jsonify, redirect, session, render_template
from db import get_db_conn

# 模块日志实例
logger = logging.getLogger(__name__)
# 注册员工管理蓝图
staff_bp = Blueprint("staff", __name__)


def is_admin():
    """判断当前会话是否为管理员"""
    return session.get("is_admin", False)


@staff_bp.route("/staff")
def staff_page():
    """员工管理页面入口，非管理员直接跳转登录页"""
    if not is_admin():
        logger.warning(f"[STAFF_PAGE_FORBIDDEN] IP:{request.remote_addr} 非管理员尝试访问员工页面")
        return redirect("/login")
    logger.info(f"[STAFF_PAGE_OK] IP:{request.remote_addr} 管理员进入员工管理页")
    return render_template("staff.html")


@staff_bp.route("/staff_save", methods=["POST"])
def staff_save():
    """
    新增/编辑员工
    old_uid为空代表新增；old_uid有值代表编辑，当前仅支持修改姓名
    工号规则：固定4位数字
    """
    client_ip = request.remote_addr
    if not is_admin():
        logger.warning(f"[STAFF_SAVE_FORBIDDEN] IP:{client_ip} 无权限调用保存接口")
        return jsonify({"ok": False, "msg": "无管理员权限"}), 403
    data = request.get_json()
    old_uid = data.get("old_uid", "").strip()
    uid = data.get("uid", "").strip()
    name = data.get("name", "").strip()

    if not name:
        logger.warning(f"[STAFF_SAVE_NAME_EMPTY] IP:{client_ip} 姓名为空")
        return jsonify({"ok": False, "msg": "姓名不能为空"})

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        if old_uid == "":
            # 新增员工分支
            if not uid:
                logger.warning(f"[STAFF_SAVE_UID_EMPTY] IP:{client_ip} 新增工号为空")
                return jsonify({"ok": False, "msg": "工号不能为空"})
            # 校验工号必须是4位纯数字
            if not re.fullmatch(r"\d{4}", uid):
                logger.warning(f"[STAFF_SAVE_UID_FORMAT_ERR] IP:{client_ip} uid={uid} 不是4位数字")
                return jsonify({"ok": False, "msg": "工号必须是4位数字！"})
            # 检查工号是否已存在
            cur.execute("SELECT 1 FROM staff WHERE uid=?", (uid,))
            if cur.fetchone():
                logger.warning(f"[STAFF_SAVE_UID_EXIST] IP:{client_ip} uid={uid} 工号已存在")
                return jsonify({"ok": False, "msg": "该工号已存在"})
            # 新增，默认设备绑定ID为NULL（未绑定）
            cur.execute("INSERT INTO staff(uid,name,bind_device_id) VALUES (?,?,NULL)", (uid, name))
            conn.commit()
            logger.info(f"[STAFF_ADD_OK] IP:{client_ip} uid={uid} name={name} 新增员工")
        else:
            # 编辑员工分支：仅修改姓名
            cur.execute("UPDATE staff SET name=? WHERE uid=?", (name, old_uid))
            conn.commit()
            logger.info(f"[STAFF_EDIT_OK] IP:{client_ip} old_uid={old_uid} new_name={name} 修改员工姓名")
    finally:
        conn.close()
    return jsonify({"ok": True})


@staff_bp.route("/staff_del", methods=["POST"])
def staff_del():
    """删除员工（物理删除）"""
    client_ip = request.remote_addr
    if not is_admin():
        logger.warning(f"[STAFF_DEL_FORBIDDEN] IP:{client_ip} 无权限删除员工")
        return jsonify({"ok": False, "msg": "无管理员权限"}), 403
    data = request.get_json()
    uid = data.get("uid")
    if not uid:
        logger.warning(f"[STAFF_DEL_PARAM_ERR] IP:{client_ip} uid参数缺失")
        return jsonify({"ok": False, "msg": "缺少工号"})
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM staff WHERE uid=?", (uid,))
        conn.commit()
        logger.info(f"[STAFF_DEL_OK] IP:{client_ip} uid={uid} 删除员工")
    finally:
        conn.close()
    return jsonify({"ok": True})


@staff_bp.route("/api_staff_list", methods=["POST"])
def api_staff_list():
    """获取全部员工简单列表（只返回uid、name，用于下拉选择）"""
    client_ip = request.remote_addr
    if not is_admin():
        logger.warning(f"[STAFF_LIST_FORBIDDEN] IP:{client_ip} 无权限获取员工列表")
        return jsonify({"ok": False, "msg": "无管理员权限"}),403
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT uid,name FROM staff ORDER BY uid")
    rows = cur.fetchall()
    conn.close()
    lst = []
    for r in rows:
        lst.append({"uid": r[0], "name": r[1]})
    logger.info(f"[STAFF_LIST_OK] IP:{client_ip} 查询全部员工列表")
    return jsonify({"ok": True, "list": lst})


@staff_bp.route("/api_staff_page", methods=["POST"])
def api_staff_page():
    """分页查询员工列表，返回绑定设备ID，管理页面表格使用"""
    client_ip = request.remote_addr
    if not is_admin():
        logger.warning(f"[STAFF_PAGE_FORBIDDEN] IP:{client_ip} 无权限分页查询员工")
        return jsonify({"ok": False, "msg": "无管理员权限"}), 403
    data = request.get_json()
    page = max(int(data.get("page",1)), 1)
    page_size = 20
    offset = (page-1)*page_size

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        # 先统计总条数
        cur.execute("SELECT COUNT(*) FROM staff")
        total = cur.fetchone()[0]
        # 分页查询，按uid倒序
        cur.execute("SELECT uid,name,bind_device_id FROM staff ORDER BY uid DESC LIMIT ? OFFSET ?", (page_size, offset))
        records_raw = cur.fetchall()
        records = []
        for r in records_raw:
            records.append({
                "uid": r[0],
                "name": r[1],
                "bind_device_id": r[2]
            })
    finally:
        conn.close()
    logger.info(f"[STAFF_PAGE_OK] 查询员工信息成功, IP:{client_ip} page={page} pageSize={page_size} total={total}")
    return jsonify({
        "ok": True,
        "records": records,
        "total": total,
        "page": page,
        "pageSize": page_size
    })


@staff_bp.route("/staff_unbind", methods=["POST"])
def staff_unbind():
    """解绑员工绑定的设备，把bind_device_id置为NULL"""
    client_ip = request.remote_addr
    if not is_admin():
        logger.warning(f"[STAFF_UNBIND_FORBIDDEN] IP:{client_ip} 无权限解绑设备")
        return jsonify({"ok": False, "msg": "无管理员权限"}), 403
    data = request.get_json()
    uid = data.get("uid")
    if not uid:
        logger.warning(f"[STAFF_UNBIND_PARAM_ERR] IP:{client_ip} uid参数缺失")
        return jsonify({"ok": False, "msg": "缺少工号"})
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE staff SET bind_device_id=NULL WHERE uid=?", (uid,))
        conn.commit()
        logger.info(f"[STAFF_UNBIND_OK] IP:{client_ip} uid={uid} 解绑设备")
    finally:
        conn.close()
    return jsonify({"ok": True})
