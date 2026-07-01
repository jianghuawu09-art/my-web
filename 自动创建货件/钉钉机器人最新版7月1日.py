from __future__ import annotations
import asyncio
import os
import time
import subprocess
import pyautogui
import pyperclip
import logging
from collections import deque
from typing import Optional, Tuple

pyautogui.FAILSAFE = False  # 关闭角标触发停止

import dingtalk_stream
from dingtalk_stream import AckMessage

# ======================== 全局配置 ========================
DT_CLIENT_ID = "dingyzwmwrqoumv2xyjb"
DT_CLIENT_SECRET = "mh0WB_3cqnOI3znHRr7Yj1BD5MQjwr_rrFHTYZtaJAx-4J2oQavuRk-KwU8kddH3"

YINGDAO_EXE = r"C:\Program Files\ShadowBot\ShadowBot.exe"
SEARCH_BOX_POS = (1476, 160)  # 影刀首次开机默认位置1502, 181
RUN_APP_POS = (1460, 259)  # 影刀重启开机默认位置1486，280

FLAG_FOLDER = r"D:\yd_run_flag"
os.makedirs(FLAG_FOLDER, exist_ok=True)
# ============================================================

RUN_STATE_LOCK = asyncio.Lock()
RUN_QUEUE = deque()
WORKER_TASK: Optional[asyncio.Task] = None
CURRENT_APP: Optional[str] = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("dingbot")

# ======================================================================
# 检测影刀是否正在运行
# ======================================================================
def is_yingdao_busy() -> Tuple[bool, str]:
    try:
        files = os.listdir(FLAG_FOLDER)
        running = [f.replace(".run", "") for f in files if f.endswith(".run")]
        if running:
            return True, f"✅ 正在运行：{', '.join(running)}"
        return False, "🟢 影刀空闲，无任务运行"
    except:
        return False, "⚠️ 状态检测异常"

# ======================================================================
# 鼠标启动影刀，兼容有无批次号
# ======================================================================
def run_yingdao_app(app_name: str, target_rp: str = "") -> str:
    logger.info(f"启动应用: {app_name}，批次号target_rp={target_rp}")
    subprocess.Popen(YINGDAO_EXE)
    time.sleep(6)

    pyautogui.click(SEARCH_BOX_POS)
    time.sleep(1)
    pyautogui.hotkey("ctrl", "a")
    pyperclip.copy(app_name)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.5)

    pyautogui.press("enter")
    time.sleep(2)

    pyautogui.moveTo(RUN_APP_POS, duration=0.3)
    pyautogui.click(RUN_APP_POS)
    time.sleep(2)

    param_file = os.path.join(FLAG_FOLDER, "temp_param.txt")
    # 有批次号写入参数文件，无则删除旧文件避免残留
    if target_rp.strip():
        with open(param_file, "w", encoding="utf-8") as f:
            f.write(f'target_rp="{target_rp}"')
        return f"✅ 已启动：{app_name}，目标批次号：{target_rp}"
    else:
        if os.path.exists(param_file):
            os.remove(param_file)
        return f"✅ 已启动：{app_name}"

# ======================================================================
# 排队执行
# ======================================================================
async def _ensure_worker():
    global WORKER_TASK
    async with RUN_STATE_LOCK:
        if WORKER_TASK and not WORKER_TASK.done():
            return
        WORKER_TASK = asyncio.create_task(_queue_worker())

async def _queue_worker():
    global CURRENT_APP
    while True:
        async with RUN_STATE_LOCK:
            if not RUN_QUEUE:
                CURRENT_APP = None
                return
            app_name, target_rp, handler, msg = RUN_QUEUE.popleft()
            CURRENT_APP = app_name

        try:
            result = await asyncio.to_thread(run_yingdao_app, app_name, target_rp)
            handler.reply_text(result, msg)
        except Exception as e:
            handler.reply_text(f"❌ 运行失败：{str(e)}", msg)

        await asyncio.sleep(1)

# ======================================================================
# 消息处理
# ======================================================================
class UniversalHandler(dingtalk_stream.ChatbotHandler):
    async def process(self, callback: dingtalk_stream.CallbackMessage):
        msg = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        text = msg.text.content.strip() if hasattr(msg, "text") else ""
        logger.info(f"消息：{text}")

        # 查询状态
        if any(k in text for k in ["状态", "查看状态", "运行状态", "当前状态"]):
            busy, info = is_yingdao_busy()
            self.reply_text(info, msg)
            return AckMessage.STATUS_OK, "OK"

        # 运行指令解析
        if "运行" in text:
            raw_content = text.replace("运行", "").strip()
            parts = raw_content.split()
            if len(parts) == 0:
                self.reply_text("⚠️ 指令格式错误！\n1.运行 流程名\n2.运行 流程名 RP批次号\n示例：运行 美工参考图生组图 RP260627006", msg)
                return AckMessage.STATUS_OK, "OK"

            target_rp = ""
            if len(parts) >= 2 and parts[-1].startswith("RP"):
                target_rp = parts[-1]
                app_name = " ".join(parts[:-1])
            else:
                app_name = " ".join(parts)

            self.reply_text(f"收到指令，准备运行：{app_name}" + (f"，批次号：{target_rp}" if target_rp else ""), msg)

            is_busy, info = is_yingdao_busy()
            if is_busy:
                self.reply_text(f"⚠️ 目前有任务在执行，无法重复运行\n{info}", msg)
                return AckMessage.STATUS_OK, "OK"

            async with RUN_STATE_LOCK:
                RUN_QUEUE.append((app_name, target_rp, self, msg))

            await _ensure_worker()
            return AckMessage.STATUS_OK, "OK"
        else:
            self.reply_text("⚠️ 无效指令\n可用指令：查询状态 / 运行 流程名 / 运行 流程名 RP批次号", msg)
            return AckMessage.STATUS_OK, "OK"

# ======================================================================
# 启动（已修复构造函数错误）
# ======================================================================
def main():
    credential = dingtalk_stream.Credential(DT_CLIENT_ID, DT_CLIENT_SECRET)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(dingtalk_stream.chatbot.ChatbotMessage.TOPIC, UniversalHandler())
    logger.info("✅ 钉钉机器人已启动")
    client.start_forever()

if __name__ == "__main__":
    main()