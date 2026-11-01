from pathlib import Path
import sys
import traceback

BASE = Path(__file__).parent.parent
sys.path.append(str(BASE))

from db import get_db_conn


# ==============================================
# ✅ 配置区（只在这里改，其他地方不用动）
# 写法说明：
# table_name       要迁移的表名
# tmp_suffix       临时表后缀，一般固定 _new 就行
# create_sql       新表建表语句
# copy_sql         数据复制SQL
# indexes          索引列表
#       type: expr=表达式索引（建失败自动跳过） normal=普通索引（必须成功）
# ==============================================
MIGRATE_TASKS = [
    {
        "table_name": "clock",
        "tmp_suffix": "_new",
        "create_sql": """
        CREATE TABLE IF NOT EXISTS clock_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            clock_dt TEXT NOT NULL,
            clock_type TEXT NOT NULL,
            remark TEXT
        )
        """,
        "copy_sql": "INSERT INTO clock_new(id,uid,clock_dt,clock_type,remark) SELECT id,uid,clock_dt,clock_type,remark FROM clock;",
        "indexes": [
            {"name": "uk_clock_uid_date_type", "sql": "CREATE UNIQUE INDEX IF NOT EXISTS uk_clock_uid_date_type ON clock(uid, date(clock_dt), clock_type);", "type": "expr"},
            {"name": "idx_clock_dt_uid_type", "sql": "CREATE INDEX IF NOT EXISTS idx_clock_dt_uid_type ON clock(date(clock_dt), uid, clock_type);", "type": "expr"},
            {"name": "idx_clock_uid_dt_type", "sql": "CREATE INDEX IF NOT EXISTS idx_clock_uid_dt_type ON clock(uid, clock_dt, clock_type);", "type": "normal"},
        ]
    }
]
# ==============================================


def run_task(conn, cur, task):
    tbl = task["table_name"]
    tmp_tbl = tbl + task["tmp_suffix"]
    create_sql = task["create_sql"]
    copy_sql = task["copy_sql"]
    index_list = task["indexes"]

    print("\n----------- 开始迁移【%s】 -----------" % tbl)

    # 检查原表是否存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (tbl,))
    if not cur.fetchone():
        print("ℹ️ 表【%s】不存在，跳过" % tbl)
        return

    # 临时表冲突保护
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (tmp_tbl,))
    if cur.fetchone():
        print("❌ 冲突：临时表【%s】已经存在！请手动删除它再运行" % tmp_tbl)
        raise Exception("临时表 %s 存在" % tmp_tbl)

    print("[1/5] 创建临时表：%s" % tmp_tbl)
    cur.execute(create_sql)

    print("[2/5] 复制数据到临时表")
    cur.execute(copy_sql)
    copied = cur.rowcount
    print("      已复制 %d 条记录" % copied)

    # 行数校验
    cur.execute("SELECT COUNT(*) FROM %s;" % tbl)
    old_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM %s;" % tmp_tbl)
    new_count = cur.fetchone()[0]
    if old_count != new_count:
        raise Exception("数据不一致！原表:%d 条，新表:%d 条" % (old_count, new_count))

    print("[3/5] 删除旧表【%s】" % tbl)
    cur.execute("DROP TABLE %s;" % tbl)

    print("[4/5] 将临时表重命名为【%s】" % tbl)
    cur.execute("ALTER TABLE %s RENAME TO %s;" % (tmp_tbl, tbl))

    print("[5/5] 创建索引：")
    for idx in index_list:
        iname = idx["name"]
        isql = idx["sql"]
        itype = idx["type"]
        if itype == "expr":
            try:
                cur.execute(isql)
                print("      ✅ %s 成功" % iname)
            except Exception:
                print("      ⚠️ %s （表达式索引，版本不支持，跳过）" % iname)
        else:
            cur.execute(isql)
            print("      ✅ %s 成功" % iname)

    print("✅ 【%s】迁移完成！" % tbl)


def main():
    print("==== SQLite 通用迁移工具 ====")
    print("⚠️  重要提醒：运行前务必备份好 kaoqin.db！")
    print("--------------------------------")
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        for task in MIGRATE_TASKS:
            run_task(conn, cur, task)
        conn.commit()
        print("\n🎉 全部任务执行完毕，已提交！")
    except Exception as err:
        conn.rollback()
        print("\n❌ 迁移失败！所有操作已回滚，没有改动数据库！")
        print("错误信息：%s" % err)
        traceback.print_exc()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    input("\n按回车键关闭窗口...")
