# NSDPRO PostForwarder

接收手机端 POST 请求，提取 `content` 字段并 URL 解码后，转发至本地 `http://127.0.0.1:47300/api/activities`。

## 功能

- 监听指定端口（默认 1403）的 POST 请求
- IP 白名单过滤（每行一个 IP，留空 = 全部禁止）
- 自动提取 `content=` 后的内容并 URL 解码（`%7B` → `{`、`%E7%9F%AD%E4%BF%A1` → `短信`）
- 转发至硬编码目标 `http://127.0.0.1:47300/api/activities`
- 系统托盘后台运行，双击托盘图标打开界面
- 配置持久化（Windows 注册表，不产生文件）
- 单 exe 文件，无需安装依赖

## 快速开始

### 直接使用（免安装）

1. 从 [Releases](../../releases) 下载 `PostForwarder.exe`
2. 双击运行
3. 在白名单中填入手机 IP（每行一个），点击「启动服务」
4. 手机端向 `http://<电脑IP>:1403` 发送 POST 请求即可

### 从源码构建

```bash
# 1. 安装依赖
pip install PyQt6 requests Pillow

# 2. 生成图标
python make_icon.py

# 3. 打包为单 exe
pip install pyinstaller
pyinstaller --onefile --windowed --name PostForwarder --icon icon.ico main.py
```

生成的 exe 在 `dist/PostForwarder.exe`。

## 使用说明

### 配置界面

| 配置项 | 说明 |
|--------|------|
| 监听端口 | HTTP 监听端口，默认 1403，可修改 1-65535 |
| 允许来源 IP 白名单 | 每行一个 IP，留空 = 全部禁止 |
| 转发目标 | 固定 `http://127.0.0.1:47300/api/activities`，不可修改 |

### 工作流程

```
手机端 ──POST──> http://<电脑IP>:1403/任意路径
  body: from=10329362791&content=%7B%22id%22%3A%22message%22...%7D
                    │
                    ▼
          PostForwarder 提取 content= 后的内容
          URL 解码：%7B → {、%22 → "、%E7%9F%AD%E4%BF%A1 → 短信
                    │
                    ▼
          POST http://127.0.0.1:47300/api/activities
          body: {"id":"message","title":"10329362791","kind":"短信"}
          Content-Type: application/json; charset=utf-8
                    │
                    ▼
          目标服务返回响应 → 原样回传给手机端
```

### 系统托盘

- 关闭窗口 → 最小化到托盘，服务继续后台运行
- **双击**托盘图标 → 打开配置界面
- 右键托盘图标 → 菜单：打开配置界面 / 退出程序

### 防火墙

手机端需要能访问电脑，请确保 Windows 防火墙放行监听端口（默认 1403）：

```powershell
New-NetFirewallRule -DisplayName "PostForwarder" -Direction Inbound -Protocol TCP -LocalPort 1403 -Action Allow
```

## 请求格式示例

### 手机端发送

```
POST http://<电脑IP>:1403/whatever
Content-Type: application/x-www-form-urlencoded

from=10329362791&content=%7B%22id%22%3A%20%22message%22%2C%20%22title%22%3A%20%2210329362791%22%2C%20%22subtitle%22%3A%20%22test%22%2C%20%22kind%22%3A%20%22%E7%9F%AD%E4%BF%A1%22%7D
```

### 转发到目标服务

```
POST http://127.0.0.1:47300/api/activities
Content-Type: application/json; charset=utf-8

{"id": "message", "title": "10329362791", "subtitle": "test", "kind": "短信"}
```

### 非白名单 IP

```
HTTP 403 Forbidden
{"error": "forbidden", "detail": "IP x.x.x.x not in whitelist"}
```

## 技术栈

- Python 3 + PyQt6 + requests
- PyInstaller（单 exe 打包）
- 配置存储：Windows 注册表 `HKCU\Software\NSDPRO\PostForwarder`

## 项目结构

```
├── main.py            # 主程序源码
├── make_icon.py       # 图标生成脚本
├── icon.ico           # 应用图标（make_icon.py 生成）
├── PostForwarder.spec # PyInstaller 打包配置
└── README.md
```
