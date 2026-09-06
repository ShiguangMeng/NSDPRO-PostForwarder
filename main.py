# -*- coding: utf-8 -*-
"""
POST 转发器
- 监听来自白名单 IP 的 POST 请求（含 JSON 消息）
- 提取 content 字段并 URL 解码后，转发至 http://127.0.0.1:47300/api/activities（硬编码）
- 精美 UI 配置界面，监听端口默认 1403 可修改，白名单按行分割（留空=全部禁止）
- 系统托盘后台运行，双击托盘图标打开配置界面
- 配置持久化（Windows 注册表，不产生文件）
"""

import json
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import requests
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QBrush, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextBrowser, QFrame,
    QSpinBox, QGraphicsDropShadowEffect, QTextEdit, QSystemTrayIcon,
    QMenu, QSizePolicy,
)


APP_ORG = "NSDPRO"
APP_NAME = "PostForwarder"
DEFAULT_LISTEN_PORT = 1403

# 转发目标（硬编码，不允许在 UI 修改）
FORWARD_HOST = "127.0.0.1"
FORWARD_PORT = 47300
FORWARD_PATH = "/api/activities"


# ---------------------------------------------------------------------------
# HTTP 请求处理 & 转发
# ---------------------------------------------------------------------------
class ForwardHandler(BaseHTTPRequestHandler):
    """处理进来的请求，过滤来源 IP，转发到目标地址。"""

    # 类属性由 server 注入
    allowed_ips = []         # 白名单 IP 列表，空列表表示全部禁止
    log_signal = None        # pyqtSignal (level:str, msg:str)

    # 不打印默认日志
    def log_message(self, format, *args):
        return

    def _emit(self, level, msg):
        if self.log_signal is not None:
            try:
                self.log_signal.emit(level, msg)
            except Exception:
                pass

    def _client_ip(self):
        return self.client_address[0]

    def _is_allowed(self):
        # 白名单为空 -> 全部禁止
        if not self.allowed_ips:
            return False
        return self._client_ip() in self.allowed_ips

    def _forward(self):
        client_ip = self._client_ip()

        if not self._is_allowed():
            self._emit("warn", f"[拒绝] 来源 IP {client_ip} 不在白名单")
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": "forbidden", "detail": f"IP {client_ip} not in whitelist"},
                ensure_ascii=False).encode("utf-8"))
            return

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        # 转发目标 URL（硬编码，不使用手机端原始路径）
        forward_url = f"http://{FORWARD_HOST}:{FORWARD_PORT}{FORWARD_PATH}"

        # 构造转发头（去掉 hop-by-hop / host）
        hop_by_hop = {
            "connection", "keep-alive", "proxy-authenticate",
            "proxy-authorization", "te", "trailers",
            "transfer-encoding", "upgrade", "host", "content-length",
        }
        fwd_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in hop_by_hop:
                fwd_headers[k] = v

        method = self.command
        raw_preview = raw_body[:200].decode("utf-8", errors="replace")
        self._emit("info",
                   f"[收到] {method} {self.path}  来源 {client_ip}"
                   f"  body={raw_preview}")

        # 解析 body：手机端发来的是 from=...&content=%7B%22... 格式，
        # 只提取 content= 后面的内容并做 URL 解码（%XX -> 真实字符），
        # 然后以 JSON 形式转发到目标服务。
        body = raw_body
        try:
            body_str = raw_body.decode("utf-8", errors="replace")
            if "content=" in body_str:
                parsed = parse_qs(body_str, keep_blank_values=True)
                content_list = parsed.get("content")
                if content_list:
                    # parse_qs 已自动完成 URL 解码（%E7%9F%AD -> 短信）
                    decoded_content = content_list[0]
                    body = decoded_content.encode("utf-8")
                    # 转发时声明为 JSON
                    fwd_headers["Content-Type"] = "application/json; charset=utf-8"
                    self._emit("ok",
                               f"[提取content] 已URL解码，"
                               f"转发内容={decoded_content[:200]}")
        except Exception as e:
            self._emit("warn", f"[解析body失败，原样转发] {e}")
            body = raw_body

        try:
            resp = requests.request(
                method=method,
                url=forward_url,
                headers=fwd_headers,
                data=body,
                timeout=30,
                allow_redirects=False,
            )
        except requests.exceptions.RequestException as e:
            self._emit("error", f"[转发失败] {forward_url} -> {e}")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": "bad gateway", "detail": str(e)},
                ensure_ascii=False).encode("utf-8"))
            return

        # 原样回传响应
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in hop_by_hop and k.lower() != "content-encoding":
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.content)

        preview = resp.content[:200].decode("utf-8", errors="replace")
        self._emit("ok",
                   f"[转发] {method} {self.path} -> {forward_url}  "
                   f"状态 {resp.status_code}  resp={preview}")

    # 支持所有方法，但主要处理 POST
    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()


class ForwardServer(QThread):
    """在子线程中运行 HTTP 服务器。"""

    log = pyqtSignal(str, str)          # level, message
    status_changed = pyqtSignal(bool)   # running

    def __init__(self, host, port, allowed_ips):
        super().__init__()
        self.host = host
        self.port = port
        self.allowed_ips = allowed_ips
        self._server = None
        self._running = False

    def run(self):
        # 注入配置到 handler 类属性
        ForwardHandler.allowed_ips = self.allowed_ips
        ForwardHandler.log_signal = self.log

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), ForwardHandler)
            self._running = True
            self.status_changed.emit(True)
            ip_desc = "、".join(self.allowed_ips) if self.allowed_ips else "（空=全部禁止）"
            self.log.emit("info",
                          f"服务已启动  监听 0.0.0.0:{self.port}"
                          f"  转发 -> http://{FORWARD_HOST}:{FORWARD_PORT}{FORWARD_PATH}"
                          f"  白名单IP: {ip_desc}")
            self._server.serve_forever()
        except OSError as e:
            self._running = False
            self.status_changed.emit(False)
            self.log.emit("error", f"服务启动失败: {e}")
        except Exception as e:
            self._running = False
            self.status_changed.emit(False)
            self.log.emit("error", f"服务异常: {e}\n{traceback.format_exc()}")

    def stop(self):
        if self._server is not None:
            self._running = False
            self.status_changed.emit(False)
            self._server.shutdown()
            self._server.server_close()
            self.log.emit("info", "服务已停止")
            self._server = None


# ---------------------------------------------------------------------------
# 主窗口 UI
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("POST 转发器")
        self.resize(860, 680)
        # 最小窗口尺寸：保证配置区完整显示不被挤压 + 日志区有足够高度
        self.setMinimumSize(760, 700)

        self.server_thread = None
        self.tray_icon = None

        # 读取持久化配置
        self.settings = QSettings(QSettings.Format.NativeFormat,
                                  QSettings.Scope.UserScope,
                                  APP_ORG, APP_NAME)
        self._load_settings()

        self._build_ui()
        self._apply_style()
        self._setup_icon_and_tray()

    # ---- 配置持久化 ----
    def _load_settings(self):
        # 白名单按行分割存储为单个字符串
        self.saved_allowed_ips = self.settings.value("allowed_ips", "", type=str)
        self.saved_listen_port = int(self.settings.value("listen_port",
                                                         DEFAULT_LISTEN_PORT, type=int))

    def _save_settings(self):
        self.settings.setValue("allowed_ips", self.ip_edit.toPlainText())
        self.settings.setValue("listen_port", self.port_spin.value())
        self.settings.sync()

    def _parse_allowed_ips(self):
        """从多行文本解析白名单 IP 列表，去空去重。"""
        text = self.ip_edit.toPlainText()
        ips = []
        for line in text.splitlines():
            ip = line.strip()
            if ip and ip not in ips:
                ips.append(ip)
        return ips

    # ---- UI 构建 ----
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # ===== 标题区 =====
        title_row = QHBoxLayout()
        title_row.setSpacing(14)

        icon_label = QLabel()
        icon_label.setFixedSize(44, 44)
        icon_label.setPixmap(self._make_icon_pixmap(44))
        icon_label.setObjectName("iconLabel")

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel("POST 转发器")
        title_lbl.setObjectName("titleLbl")
        sub_lbl = QLabel("接收手机端 POST 请求并完整转发到本地目标服务")
        sub_lbl.setObjectName("subLbl")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)

        title_row.addWidget(icon_label)
        title_row.addLayout(title_box)
        title_row.addStretch(1)
        root.addLayout(title_row)

        # ===== 配置卡片 =====
        cfg_card = QFrame()
        cfg_card.setObjectName("card")
        # 配置卡片只允许左右缩放，不允许上下缩放
        cfg_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        cfg_layout = QVBoxLayout(cfg_card)
        cfg_layout.setContentsMargins(20, 18, 20, 18)
        cfg_layout.setSpacing(14)

        cfg_head = QLabel("服务配置")
        cfg_head.setObjectName("cardHead")
        cfg_layout.addWidget(cfg_head)

        # 行1: 监听端口
        row1 = QHBoxLayout()
        row1.setSpacing(16)

        port_box = self._labeled_field("监听端口", port_default=str(self.saved_listen_port),
                                       is_port=True)
        self.port_spin = port_box["spin"]
        # 端口输入框限制最大宽度，避免窗口过宽时被拉得太长
        self.port_spin.setMaximumWidth(160)

        row1.addWidget(port_box["widget"])
        row1.addStretch(1)
        cfg_layout.addLayout(row1)

        # 行2: 允许来源 IP 白名单（多行，按行分割；留空=全部禁止）
        ip_lbl = QLabel("允许来源 IP 白名单（每行一个，留空=全部禁止）")
        ip_lbl.setObjectName("fieldLbl")
        cfg_layout.addWidget(ip_lbl)

        self.ip_edit = QTextEdit()
        self.ip_edit.setObjectName("input")
        self.ip_edit.setPlaceholderText("例如：\n192.168.1.100\n192.168.1.101")
        self.ip_edit.setPlainText(self.saved_allowed_ips)
        # 白名单输入框固定高度，不随窗口上下缩放
        self.ip_edit.setFixedHeight(90)
        self.ip_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        cfg_layout.addWidget(self.ip_edit)

        # 转发目标提示（硬编码，不可修改）
        hint_lbl = QLabel(f"转发目标（固定）：http://{FORWARD_HOST}:{FORWARD_PORT}{FORWARD_PATH}")
        hint_lbl.setObjectName("hintLbl")
        cfg_layout.addWidget(hint_lbl)

        # 启动按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.toggle_btn = QPushButton("启动服务")
        self.toggle_btn.setObjectName("toggleBtn")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._on_toggle)
        self.toggle_btn.setMinimumHeight(42)

        self.status_badge = QLabel("● 未运行")
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_badge.setMinimumHeight(42)

        btn_row.addWidget(self.toggle_btn, 3)
        btn_row.addWidget(self.status_badge, 2)
        cfg_layout.addLayout(btn_row)

        root.addWidget(cfg_card)

        # ===== 日志卡片 =====
        log_card = QFrame()
        log_card.setObjectName("card")
        # 日志卡片双向伸缩，占据剩余空间
        log_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(20, 14, 20, 14)
        log_layout.setSpacing(10)

        log_head_row = QHBoxLayout()
        log_head = QLabel("运行日志")
        log_head.setObjectName("cardHead")
        self.clear_log_btn = QPushButton("清空")
        self.clear_log_btn.setObjectName("ghostBtn")
        self.clear_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_log_btn.clicked.connect(self._clear_log)
        log_head_row.addWidget(log_head)
        log_head_row.addStretch(1)
        log_head_row.addWidget(self.clear_log_btn)
        log_layout.addLayout(log_head_row)

        self.log_view = QTextBrowser()
        self.log_view.setObjectName("logView")
        self.log_view.setOpenExternalLinks(False)
        # 日志区最小高度，保证可读性
        self.log_view.setMinimumHeight(160)
        log_layout.addWidget(self.log_view)

        root.addWidget(log_card, 1)

    def _labeled_field(self, label_text, default="", placeholder="",
                       port_default="", is_port=False):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lbl = QLabel(label_text)
        lbl.setObjectName("fieldLbl")
        lay.addWidget(lbl)

        if is_port:
            spin = QSpinBox()
            spin.setRange(1, 65535)
            spin.setObjectName("input")
            spin.setMinimumHeight(32)
            try:
                spin.setValue(int(port_default))
            except Exception:
                spin.setValue(DEFAULT_LISTEN_PORT)
            lay.addWidget(spin)
            return {"widget": wrap, "spin": spin}
        else:
            edit = QLineEdit(default)
            edit.setObjectName("input")
            edit.setPlaceholderText(placeholder)
            edit.setMinimumHeight(36)
            lay.addWidget(edit)
            return {"widget": wrap, "edit": edit}

    # ---- 样式 ----
    def _apply_style(self):
        qss = """
        QWidget#central {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #16182a, stop:1 #1f2238);
        }
        QLabel#titleLbl {
            color: #ffffff;
            font-size: 22px;
            font-weight: 700;
        }
        QLabel#subLbl {
            color: #9aa0b8;
            font-size: 12px;
        }
        QFrame#card {
            background: #252840;
            border: 1px solid #32365a;
            border-radius: 14px;
        }
        QLabel#cardHead {
            color: #e6e8f5;
            font-size: 14px;
            font-weight: 600;
            padding-bottom: 2px;
        }
        QLabel#fieldLbl {
            color: #b8bdd6;
            font-size: 12px;
            font-weight: 500;
        }
        QLineEdit#input, QSpinBox#input, QTextEdit#input {
            background: #1b1e30;
            border: 1px solid #3a3f63;
            border-radius: 8px;
            color: #f0f1fa;
            padding: 7px 12px;
            font-size: 13px;
            selection-background-color: #6c5ce7;
        }
        QLineEdit#input:focus, QSpinBox#input:focus, QTextEdit#input:focus {
            border: 1px solid #7c6cff;
            background: #1f2238;
        }
        QLabel#hintLbl {
            color: #7c8099;
            font-size: 11px;
            padding: 4px 2px 0 2px;
        }
        QPushButton#toggleBtn {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #6c5ce7, stop:1 #8e7bff);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
        }
        QPushButton#toggleBtn:hover {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #7c6cf0, stop:1 #9e8bff);
        }
        QPushButton#toggleBtn:pressed {
            background: #5b4cc7;
        }
        QPushButton#toggleBtn:disabled {
            background: #45486b;
            color: #8b8fa8;
        }
        QPushButton#ghostBtn {
            background: transparent;
            color: #b8bdd6;
            border: 1px solid #3a3f63;
            border-radius: 8px;
            padding: 6px 16px;
            font-size: 12px;
        }
        QPushButton#ghostBtn:hover {
            background: #32365a;
            color: #ffffff;
        }
        QLabel#statusBadge {
            background: #2a2d47;
            color: #ff7a7a;
            border: 1px solid #4a3050;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 600;
        }
        QTextBrowser#logView {
            background: #14162a;
            border: 1px solid #2c3050;
            border-radius: 10px;
            color: #d6d8e8;
            font-family: Consolas, "Courier New", monospace;
            font-size: 12px;
            padding: 10px;
        }
        QScrollBar:vertical {
            background: #1b1e30;
            width: 10px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #3a3f63;
            border-radius: 5px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover { background: #4a5080; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """
        self.setStyleSheet(qss)

        # 卡片阴影
        for card in self.findChildren(QFrame):
            if card.objectName() == "card":
                shadow = QGraphicsDropShadowEffect()
                shadow.setBlurRadius(24)
                shadow.setOffset(0, 6)
                shadow.setColor(QColor(0, 0, 0, 90))
                card.setGraphicsEffect(shadow)

    def _make_icon_pixmap(self, size):
        """绘制程序图标：紫色圆角底 + 白色转发箭头。"""
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 背景：紫色渐变圆角方块
        radius = max(2, int(size * 0.22))
        p.setBrush(QBrush(QColor("#7c6cff")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, size, size, radius, radius)
        # 白色转发箭头（折线）
        p.setPen(QColor("#ffffff"))
        lw = max(2, int(size * 0.10))
        p.setPen(QColor("#ffffff"))
        pen = p.pen()
        pen.setWidth(lw)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        m = int(size * 0.26)
        # 从左下到中右再到右上
        p.drawLine(m, size - m, size - m, size // 2)
        p.drawLine(size - m, size // 2, m, m)
        # 箭头头部
        ah = int(size * 0.16)
        p.drawLine(m, m, m + ah, m - ah // 2)
        p.drawLine(m, m, m + ah // 2, m + ah)
        p.end()
        return pix

    def _app_icon(self):
        """返回应用 QIcon（用于窗口、托盘、任务栏等所有位置）。"""
        icon = QIcon()
        for s in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(self._make_icon_pixmap(s))
        return icon

    def _setup_icon_and_tray(self):
        """设置窗口图标并创建系统托盘。"""
        app_icon = self._app_icon()
        self.setWindowIcon(app_icon)

        # 系统托盘
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(app_icon, self)
        self.tray_icon.setToolTip("POST 转发器")

        # 托盘菜单
        menu = QMenu(self)
        show_action = QAction("打开配置界面", self)
        show_action.triggered.connect(self._show_window)
        quit_action = QAction("退出程序", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray_icon.setContextMenu(menu)

        # 双击左键打开/还原窗口
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit_app(self):
        if self.server_thread is not None and self.server_thread.isRunning():
            self.server_thread.stop()
            self.server_thread.wait(2000)
        try:
            self._save_settings()
        except Exception:
            pass
        if self.tray_icon is not None:
            self.tray_icon.hide()
        QApplication.quit()

    # ---- 业务逻辑 ----
    def _on_toggle(self):
        if self.server_thread is None or not self.server_thread.isRunning():
            self._start_server()
        else:
            self._stop_server()

    def _start_server(self):
        port = self.port_spin.value()
        allowed_ips = self._parse_allowed_ips()

        # 保存配置
        self._save_settings()

        # 禁用编辑
        self._set_edits_enabled(False)
        self.toggle_btn.setText("停止服务")

        self.server_thread = ForwardServer(
            host="0.0.0.0",
            port=port,
            allowed_ips=allowed_ips,
        )
        self.server_thread.log.connect(self._append_log)
        self.server_thread.status_changed.connect(self._on_status_changed)
        self.server_thread.finished.connect(self._on_thread_finished)
        self.server_thread.start()

    def _stop_server(self):
        if self.server_thread is not None:
            self.server_thread.stop()

    def _on_status_changed(self, running):
        if running:
            self.status_badge.setText("● 运行中")
            self.status_badge.setStyleSheet(
                "QLabel#statusBadge { background:#1e3a2a; color:#5fe0a1;"
                " border:1px solid #2f6b4a; border-radius:10px;"
                " font-size:13px; font-weight:600; }")
        else:
            self.status_badge.setText("● 未运行")
            self.status_badge.setStyleSheet(
                "QLabel#statusBadge { background:#2a2d47; color:#ff7a7a;"
                " border:1px solid #4a3050; border-radius:10px;"
                " font-size:13px; font-weight:600; }")

    def _on_thread_finished(self):
        self._set_edits_enabled(True)
        self.toggle_btn.setText("启动服务")
        self.server_thread = None

    def _set_edits_enabled(self, enabled):
        for w in (self.port_spin, self.ip_edit):
            w.setEnabled(enabled)

    def _append_log(self, level, msg):
        ts = time.strftime("%H:%M:%S")
        color_map = {
            "info": "#9aa0b8",
            "ok": "#5fe0a1",
            "warn": "#ffb86b",
            "error": "#ff7a7a",
        }
        color = color_map.get(level, "#d6d8e8")
        level_tag = {"info": "INFO", "ok": " OK ", "warn": "WARN",
                     "error": "ERR "}.get(level, "INFO")
        html = (f'<div style="color:{color}; margin:1px 0;">'
                f'<span style="color:#6b7090;">[{ts}]</span> '
                f'<span style="color:{color};font-weight:600;">[{level_tag}]</span> '
                f'{msg}</div>')
        self.log_view.append(html)
        # 限制日志条数，避免内存膨胀
        doc = self.log_view.document()
        if doc.blockCount() > 2000:
            cursor = self.log_view.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Down,
                                cursor.MoveMode.KeepAnchor, 200)
            cursor.removeSelectedText()

    def _clear_log(self):
        self.log_view.clear()

    # ---- 窗口关闭 ----
    def closeEvent(self, event):
        # 关闭窗口时最小化到托盘，服务继续在后台运行
        try:
            self._save_settings()
        except Exception:
            pass
        if self.tray_icon is not None and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            self.tray_icon.showMessage(
                "POST 转发器", "程序已最小化到托盘，双击托盘图标可打开界面",
                QSystemTrayIcon.MessageIcon.Information, 2000)
            event.ignore()
        else:
            # 无托盘环境则正常退出
            if self.server_thread is not None and self.server_thread.isRunning():
                self.server_thread.stop()
                self.server_thread.wait(2000)
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    # 高 DPI
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    # 关闭最后一个窗口不自动退出（托盘后台运行）
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    # 应用图标到任务栏/Alt-Tab
    app.setWindowIcon(win._app_icon())
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
