# tools/sqlite_tool.py
import sqlite3
import os
import re
import traceback

# 脚本所在目录的上一层，取绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "kaoqin.db"))

# ===================== 配置区，可自行修改 =====================
READ_ONLY_MODE = False        # True=只读，只能SELECT，禁止所有修改语句
MAX_QUERY_ROWS = 1000         # 查询最多返回多少行，防止超大结果刷屏
QUERY_TIMEOUT = 10            # 单条SQL超时秒数
# ============================================================

# 高危语句（整词匹配，命中后需要大写YES确认）
DANGER_SQL_KEYWORDS = {"drop", "truncate", "alter", "delete", "update", "insert", "replace"}
# 直接禁止，不给确认机会
FORBIDDEN_KEYWORDS = {"attach", "detach", "pragma", "load_extension", "vacuum", "reindex"}

# 正则：匹配独立单词，防止子串误判，不区分大小写
WORD_PATTERN = re.compile(r"\b(" + "|".join(DANGER_SQL_KEYWORDS | FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)

# 匹配引号外部的分号，正则语法校验无误
SPLIT_SEMICOLON_PATTERN = re.compile(r';(?=(?:[^\'"]|\'[^\']*\'|"[^"]*")*$)')


def get_conn() -> sqlite3.Connection:
    """获取连接，只读模式使用uri只读；只读模式不执行PRAGMA"""
    if READ_ONLY_MODE:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=QUERY_TIMEOUT)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=QUERY_TIMEOUT)
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def strip_sql_comment(sql: str) -> str:
    """简单去除 -- 单行注释，降低绕过风险（不是专业SQL解析，自用工具够用）"""
    lines = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        lines.append(line)
    return "\n".join(lines)


def count_statement_semicolon(sql: str) -> int:
    """统计SQL里不在单/双引号内部的分号数量"""
    return len(SPLIT_SEMICOLON_PATTERN.findall(sql))


def check_sql_safe(sql: str) -> tuple[bool, str, set]:
    """返回：是否允许执行, 提示信息, 命中的危险词集合"""
    sql_clean = strip_sql_comment(sql).strip()
    if not sql_clean:
        return False, "空SQL", set()

    if READ_ONLY_MODE and not sql_clean.lower().startswith("select"):
        return False, "只读模式，仅允许SELECT查询", set()

    hits = set(m.group(1).lower() for m in WORD_PATTERN.finditer(sql_clean))
    forbidden_hits = hits & FORBIDDEN_KEYWORDS
    if forbidden_hits:
        return False, f"包含禁止关键字：{forbidden_hits}", forbidden_hits

    danger_hits = hits & DANGER_SQL_KEYWORDS
    return True, "ok", danger_hits


def execute_sql(sql: str):
    sql = sql.strip()
    if sql == ";":
        print("❌ 无效语句：只有分号，不执行")
        return
    if not sql:
        return

    sql_clean = strip_sql_comment(sql).strip()
    if not sql_clean:
        print("❌ 空语句，不执行")
        return

    SQL_START_KEYWORDS = {"select", "insert", "update", "delete", "replace", "create", "drop", "alter"}
    tokens = sql_clean.split()
    first_word = tokens[0].lower() if tokens else ""
    if first_word not in SQL_START_KEYWORDS:
        print(f"❌ 识别不是SQL语句（首词：{first_word}），跳过数据库查询")
        return

    ok, msg, danger_hits = check_sql_safe(sql_clean)
    if not ok:
        print(f"❌ 安全拦截：{msg}")
        return

    sc_count = count_statement_semicolon(sql_clean)
    if sc_count >= 2:
        print("❌ 禁止多条SQL语句，仅支持单条SQL")
        return

    if danger_hits:
        print(f"⚠️ 高危操作，命中关键字：{danger_hits}")
        print("⚠️ 确认执行请输入大写 YES，其他输入取消")
        ans = input("确认：").strip()
        if ans != "YES":
            print("✅ 已取消执行")
            return

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(sql)

        if sql_clean.lower().startswith("select"):
            rows = cur.fetchmany(MAX_QUERY_ROWS)
            if not rows:
                print("【查询结果】无数据")
            else:
                headers = [d[0] for d in cur.description]
                print("\t".join(headers))
                print("-" * 70)
                for r in rows:
                    line = "\t".join(str(r[h]) for h in headers)
                    print(line)
                print(f"\n本次返回 {len(rows)} 条（上限 {MAX_QUERY_ROWS}）")
                if cur.fetchone():
                    print("⚠️ 还有更多数据，受上限限制没有全部取出")
        else:
            conn.commit()
            print(f"✅ 执行成功，影响行数：{cur.rowcount}")

    except sqlite3.OperationalError as e:
        print(f"❌ SQLite执行错误：{e}")
        print("-" * 60)
        traceback.print_exc()
        print("-" * 60)
        if conn is not None:
            conn.rollback()
    except Exception as e:
        print(f"❌ 未知异常：{e}")
        print("-" * 60)
        traceback.print_exc()
        print("-" * 60)
        if conn is not None:
            conn.rollback()
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def read_multiline_sql():
    """
    首行 >>>，续行 ...
    任意一行末尾带英文; 直接提交；也支持单独一行输入; 提交
    输入 \c 取消本轮，回到 >>>
    检测全角分号，报错后回到主循环
    """
    lines = []
    while True:
        try:
            prompt = ">>> " if len(lines) == 0 else "... "
            line = input(prompt)
        except EOFError:
            return "EOF"

        raw = line.strip()
        if raw.lower() == "exit":
            return "exit"
        if raw == r"\c":
            print("⚠️ 取消本次输入")
            return "RETRY"

        if "；" in line:
            print("❌ 检测到中文全角分号【；】，请使用英文分号 ;")
            return "RETRY"

        lines.append(line)

        if line.rstrip().endswith(";"):
            return "\n".join(lines)
        if raw == ";":
            return "\n".join(lines)


def main():
    print("==== SQLite 安全交互式工具 ====")
    print(f"数据库路径：{DB_PATH}")
    print(f"只读模式：{READ_ONLY_MODE}")
    print(f"查询行数上限：{MAX_QUERY_ROWS}")
    print("【使用规则】")
    print("  - 首行 >>>，续行自动变成 ...")
    print("  - 任意一行末尾带上英文; 自动提交执行")
    print("  - 也支持单独一行输入英文 ; 提交")
    print("  - 输入 \\c 取消当前这段SQL，重新开始")
    print("  - 单独一行输入 exit 退出程序")
    print("  - 禁止中文全角分号；")
    print("  - 仅支持单条SQL；禁止 attach / load_extension / pragma 等")
    print("  - drop / delete / update / insert / replace / alter / truncate 需要大写 YES 确认\n")
    while True:
        sql = read_multiline_sql()
        if sql == "exit":
            print("bye")
            break
        if sql == "EOF":
            print("\n检测到EOF，退出")
            break
        if sql == "RETRY":
            continue
        sql = sql.strip()
        if not sql:
            continue
        execute_sql(sql)


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"⚠️ 警告：数据库文件不存在 -> {DB_PATH}")
    else:
        print(f"✅ 数据库文件校验正常：{DB_PATH}")

    main()
