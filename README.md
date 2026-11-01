```markdown
# 考勤打卡系统
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.x-000000?logo=flask)](https://flask.palletsprojects.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-blue)](https://www.sqlite.org/)
[![OpenPyXL](https://img.shields.io/badge/OpenPyXL-Excel导出-green)](https://openpyxl.readthedocs.io/)

> 基于Flask + SQLite的内网轻量考勤打卡工具，内嵌WebView2可用，无需额外部署数据库。
> 项目初衷：给没有信息化系统的传统小公司使用，替代人工纸质登记考勤。

## ✨ 功能清单
- 员工打卡：上班打卡 / 下班打卡
- 打卡记录查询，按日期查看明细
- 管理员后台：登录、修改管理员密码（带密码错误锁定防暴力破解）
- 月度考勤矩阵报表，分页预览
- 打卡流水Excel导出、月度考勤汇总报表导出（带状态底色）
- 员工管理

## 📦 项目结构
```
clockInForWork/
├── app.py                 # flask启动入口
├── config.py              # 配置项
├── db.py                  # 数据库连接封装
├── main.py                # 入口
├── routes/
│   ├── admin_api.py       # 管理员接口（报表、导出、登录）
│   ├── clock.py           # 打卡接口
│   └── staff.py           # 员工管理接口
├── templates/             # 前端html页面
│   ├── all_record.html    # 管理员查询打卡流水
│   ├── change_pwd.html    # 修改管理员密码
│   ├── index.html         # 主页，也是打卡页
│   ├── list.html          # 个人打卡记录
│   ├── login.html         # 登录页
│   ├── menu.html          # 管理员进入菜单页
│   ├── report.html        # 管理员查全员月考勤矩阵
│   └── staff.html         # 员工管理
├── tools/                 # 辅助工具脚本，开发调试使用，不要发布到项目
│   ├── migrate_clock.py   # 迁移历史数据，开发过程中改了结构
│   ├── reset_admin_pwd.py # 重置管理员密码
│   └── sqlite_tool.py     # 通用SQLite 安全交互式工具，个人使用
├── tips.xlsx              # 手动存名人名言
├── .gitignore
└── README.md
```

## 🚀 运行方式
1. 创建虚拟环境
```bash
python -m venv venv
```
2. 激活环境
```bash
# Windows git bash
source venv/Scripts/activate
# Windows cmd
venv\Scripts\activate
```
3. 安装依赖
```bash
pip install flask openpyxl
```
4. 启动服务
```bash
python app.py
```
访问：`[http://127.0.0.1:5000](http://127.0.0.1:5000)`

## 🗄️ 数据库说明
使用 SQLite 单文件数据库，开箱即用，无需单独安装数据库服务。
工具目录下提供重置管理员密码、数据迁移脚本。

## 📋 报表说明
- 打卡流水：原始打卡记录导出
- 月度考勤汇总：自动识别【正常 / 缺上班卡 / 缺下班卡 / 全天缺卡】，带颜色标记、自动换行

## ⚠️ 注意事项
- 适合内网自用，不建议直接公网暴露
- 管理员密码存在数据库的config表，有登录失败次数锁定机制
- `.gitignore` 已忽略虚拟环境、数据库、缓存、本地临时文件

## 🙏 致谢
本项目代码100%由AI生成！从后端接口、前端页面、报表导出到调试排错，全程离不开豆包的给力支持，效率拉满，非常感谢🎉

## 📄 许可证
仅供内部学习、自用。
```