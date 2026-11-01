import hmac
import hashlib
from config import SECRET_SALT
from db import get_db_conn
import traceback

def calc_hash(pwd: str):
    # 和接口里的哈希函数完全复制，保持一致
    return hmac.new(SECRET_SALT, pwd.encode("utf-8"), hashlib.sha256).hexdigest()

# 重置管理员密码
def reset_admin_pwd(new_pwd: str):
    new_hash = calc_hash(new_pwd.strip())
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE config SET v=? WHERE k='admin_pwd'", (new_hash,))
    cur.execute("SELECT changes()")
    affected = cur.fetchone()[0]
    if affected <= 0:
        # 没有这条配置记录则插入
        cur.execute("INSERT INTO config(k, v) VALUES ('admin_pwd', ?)", (new_hash,))
    conn.commit()
    conn.close()
    print("✅ 重置完成！")

if __name__ == "__main__":
    try:
        p = input("请输入新管理员密码：")
        reset_admin_pwd(p)
        print("新密码已写入数据库，可以登录了")
    except:
        print('错误：\n')
        traceback.print_exc()
    input("\n按回车关闭窗口...")
