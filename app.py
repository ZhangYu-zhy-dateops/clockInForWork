# app.py 项目入口，全局安全拦截、蓝图注册、数据库表初始化
from datetime import timedelta
from flask import Flask, render_template, session, redirect, request
import logging
import time
from threading import Lock
from config import SECRET_KEY, SECRET_SALT, SESSION_LIFETIME, BLOCK_PATH_KEYWORDS, RATE_LIMIT_SECONDS, RATE_LIMIT_MAX_REQ
from db import get_db_conn
from routes.clock import clock_bp
from routes.staff import staff_bp
from routes.admin_api import admin_api_bp


class DynamicWerkzeugFilter(logging.Filter):
    """
    自定义werkzeug日志过滤器
    过滤掉werkzeug默认的普通访问INFO日志，只保留启动提示、警告及以上级别日志，减少日志刷屏
    """
    def __init__(self):
        super().__init__()
        self.enable_filter = True

    def filter(self, record):
        # 非werkzeug日志、过滤器关闭时直接放行
        if record.name != "werkzeug" or not self.enable_filter:
            return True
        msg = record.getMessage()
        # 保留启动提示行、warning及更高级别日志
        if "Running on" in msg or record.levelno >= logging.WARNING:
            return True
        return False


# 挂载日志过滤规则
filt = DynamicWerkzeugFilter()
logging.getLogger("werkzeug").addFilter(filt)


# 实例化Flask主应用
app = Flask(__name__)
# session过期时长
app.permanent_session_lifetime = timedelta(minutes=SESSION_LIFETIME)
# session加密密钥
app.secret_key = SECRET_KEY

# IP限流容器：key=客户端IP，value={"ts":窗口起始时间, "cnt":请求计数}
ip_req_map = dict()
# 限流字典线程锁，防止多线程并发修改字典异常
ip_req_lock = Lock()


@app.before_request
def security_check():
    """
    全局前置拦截器，所有请求进来优先执行
    1. 静态资源/图标直接放行
    2. 路径关键词黑名单拦截（防扫描攻击）
    3. IP简易滑动窗口限流，防高频刷接口
    """
    client_ip = request.remote_addr
    path = request.path.lower()
    ua = request.headers.get("User-Agent", "-")

    # 静态资源、favicon不做校验，直接放行
    if path.startswith("/static/") or path == "/favicon.ico":
        return

    # 黑名单关键词检测，命中直接403拒绝访问
    for kw in BLOCK_PATH_KEYWORDS:
        if kw in path:
            logging.warning(f"命中攻击关键词 [BLOCK] IP:{client_ip} UA:{ua} PATH:{request.path} 关键词:{kw}")
            return "Access denied", 403

    now = time.time()
    with ip_req_lock:
        if client_ip not in ip_req_map:
            # 新IP，初始化限流窗口
            ip_req_map[client_ip] = {"ts": now, "cnt": 1}
        else:
            item = ip_req_map[client_ip]
            # 窗口超时，重置计时和计数
            if now - item["ts"] > RATE_LIMIT_SECONDS:
                item["ts"] = now
                item["cnt"] = 1
            else:
                item["cnt"] += 1
                # 超过最大请求次数，返回429限流
                if item["cnt"] > RATE_LIMIT_MAX_REQ:
                    logging.warning(f"超出访问频率限制 [LIMIT] IP:{client_ip}")
                    return "Too many requests", 429


def init_db():
    """
    数据库初始化函数
    自动创建全部业务表、索引；不存在管理员密码时自动初始化默认管理员密码哈希
    表：staff员工表、clock打卡记录表、config配置表、admin_login_lock管理员登录锁定表
    """
    conn = get_db_conn()
    cur = conn.cursor()

    # 员工表：uid工号主键，bind_device_id绑定设备ID
    cur.execute('''
    CREATE TABLE IF NOT EXISTS staff (
        uid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        bind_device_id TEXT
    )
    ''')

    # 打卡主表：id自增主键，remark预留备注字段
    cur.execute('''
    CREATE TABLE IF NOT EXISTS clock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL,
        staff_name TEXT NOT NULL,
        clock_dt TEXT NOT NULL,
        clock_type TEXT NOT NULL,
        remark TEXT
    )
    ''')

    # 唯一索引：同一个员工同一天同类型打卡只能一条，兜底防重复打卡（代码层也有校验）
    try:
        cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS uk_clock_uid_date_type ON clock(uid, date(clock_dt), clock_type);')
    except Exception:
        pass

    # 普通索引：按月查询、按uid+时间查询加速
    cur.execute('CREATE INDEX IF NOT EXISTS idx_clock_ym_uid_type ON clock(date(clock_dt), uid, clock_type);')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_clock_uid_dt_type ON clock(uid, clock_dt, clock_type);')

    # 全局配置表，k-v结构，存放admin_pwd等系统配置
    cur.execute('''
    CREATE TABLE IF NOT EXISTS config (
        k TEXT PRIMARY KEY,
        v TEXT
    )
    ''')

    # 没有管理员密码时，写入默认密码hmac哈希
    cur.execute("SELECT v FROM config WHERE k='admin_pwd'")
    if not cur.fetchone():
        import hmac, hashlib
        h = hmac.new(SECRET_SALT, b"2333", hashlib.sha256).hexdigest()
        cur.execute("INSERT INTO config(k,v) VALUES (?,?)", ("admin_pwd", h))

    # 管理员登录锁定表：记录IP失败次数、锁定到期时间，防暴力破解后台
    cur.execute('''
    CREATE TABLE IF NOT EXISTS admin_login_lock (
        ip TEXT PRIMARY KEY,
        fail_count INTEGER NOT NULL DEFAULT 0,
        lock_until TEXT NOT NULL
    )
    ''')

    conn.commit()
    conn.close()
    logging.info("数据库初始化完成")


# 程序启动执行一次建表/初始化
init_db()

# 注册各个业务蓝图，路由拆分
app.register_blueprint(clock_bp)
app.register_blueprint(staff_bp)
app.register_blueprint(admin_api_bp)


@app.route('/')
def home():
    """首页，打卡入口页面"""
    return render_template("index.html")


@app.route("/login")
def login_page():
    """管理员登录页，已登录直接跳转菜单"""
    if session.get("is_admin"):
        return redirect("/menu")
    return render_template("login.html")


@app.route("/menu")
def menu_page():
    """后台管理菜单主页，未登录跳转登录"""
    if not session.get("is_admin"):
        return redirect("/login")
    return render_template("menu.html")


@app.route("/change_pwd")
def change_pwd_page():
    """管理员修改密码页面，需要登录态"""
    if not session.get("is_admin"):
        return redirect("/login")
    return render_template("change_pwd.html")


@app.route("/logout")
def logout():
    """后台登出，清除admin会话标记，跳转登录"""
    session.pop("is_admin", None)
    return redirect("/login")


@app.route("/all_record")
def all_record_page():
    """全部打卡记录页面，管理员权限"""
    if not session.get("is_admin"):
        return redirect("/login")
    return render_template("all_record.html")


@app.route("/report")
def report_page():
    """考勤报表页面，管理员权限"""
    if not session.get("is_admin"):
        return redirect("/login")
    return render_template("report.html")


# if __name__ == '__main__':
#     app.run(host="0.0.0.0", port=5000, debug=False)
