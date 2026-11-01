# config.py
# ================= 日志配置 =================
# 日志文件名
LOG_FILE = 'clockInForWork.log'
# 单个日志文件最大容量：5MB
LOG_MAX_SIZE = 5 * 1024 * 1024
# 日志滚动备份保留数量，最多保留3个历史日志
LOG_BACKUP_COUNT = 3
# ============================================

# ================ 安全配置区 =================
# IP限流：统计周期，单位秒
RATE_LIMIT_SECONDS = 10
# IP限流：周期内最大请求次数
RATE_LIMIT_MAX_REQ = 30

# 简单攻击特征路径，路由小写匹配命中直接拦截
BLOCK_PATH_KEYWORDS = [
    ".php", ".asp", ".jsp", ".aspx", ".shell", "eval", "backdoor",
    "wp-admin", "phpmyadmin", "admin.php", "webshell", "cgi-bin"
]
# =============================================

# NTP时间服务器地址，用于开机自动校正本机时间
NTP_HOST = "ntp.aliyun.com"
# NTP校验失败重试间隔，单位秒
LOOP_WAIT = 20
# 允许的本机时间与NTP标准时间最大偏差，单位秒
ALLOW_TIME_ERROR = 5
# 等待NTP时间校验的最大超时时长，单位秒（3600=1小时）
MAX_WAIT_SEC = 3600

# Flask会话加密密钥，用于session加密，生产环境务必更换为复杂随机串
SECRET_KEY = "abc123456xyz789"
# HMAC密码哈希盐值，用于管理员密码加密，不要随意修改，改了旧密码失效
SECRET_SALT = b"my-clock-salt-2026-0101"
# Session会话有效期，单位分钟
SESSION_LIFETIME = 30

# 登录最大错误次数
MAX_FAIL = 5
# 登录锁定时间分钟
LOCK_MINUTES = 1

# 打卡时段配置，支持多窗口，禁止时间重叠
# in：上班打卡窗口，out：下班打卡窗口
CLOCK_TIME_WINDOWS = {
    'in':("07:00", "08:10"),
    'out':("21:38", "23:00"),
}
