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

pyautogui.FAILSAFE = False

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
)
from lark_oapi.event.dispatcher_handler import (
    EventDispatcherHandler,
    P2ImMessageReceiveV1Processor,
)
from lark_oapi import EventContext
from lark_oapi import ws

FEISHU_APP_ID = "机器人"
FEISHU_APP_SECRET = "机器人"

YINGDAO_EXE = r"C:\Program Files\ShadowBot\ShadowBot.exe"
SEARCH_BOX_POS = (1502, 181)
RUN_APP_POS = (1486, 280)

FLAG_FOLDER = r"D:\yd_run_flag"
os.makedirs(FLAG_FOLDER, exist_ok=True)

RUN_STATE_LOCK = asyncio.Lock()
RUN_QUEUE = deque()
WORKER_TASK: Optional[asyncio.Task] = None
CURRENT_APP: Optional[str] = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("feishu_bot")


def is_yingdao_busy() -> Tuple[bool, str]:
    try:
        files = os.listdir(FLAG_FOLDER)
        running = [f.replace(".run", "") for f in files if f.endswith(".run")]
        if running:
            return True, f"✅ 正在运行：{', '.join(running)}"
        return False, "🟢 影刀空闲，无任务运行"
    except:
        return False, "⚠️ 状态检测异常"


def run_yingdao_app(app_name: str, target_rp: str = "") -> str:
    logger.info(f"启动应用: {app_name}，批次号target_rp={target_rp}")
    param_file = os.path.join(FLAG_FOLDER, "temp_param.txt")
    if target_rp.strip():
        with open(param_file, "w", encoding="utf-8") as f:
            f.write(f'target_rp="{target_rp}"')
        time.sleep(0.8)
    else:
        if os.path.exists(param_file):
            try:
                os.remove(param_file)
            except Exception as e:
                logger.warning(f"删除旧参数文件失败：{e}")
        time.sleep(0.3)

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

    if target_rp.strip():
        return f"✅ 已启动：{app_name}，目标批次号：{target_rp}"
    else:
        return f"✅ 已启动：{app_name}"


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
            app_name, target_rp, reply_func, receive_msg = RUN_QUEUE.popleft()
            CURRENT_APP = app_name

        try:
            result = await asyncio.to_thread(run_yingdao_app, app_name, target_rp)
            await reply_func(receive_msg, result)
        except Exception as e:
            err_msg = f"❌ 运行失败：{str(e)}"
            await reply_func(receive_msg, err_msg)

        await asyncio.sleep(1)


client = lark.Client.builder() \
    .app_id(FEISHU_APP_ID) \
    .app_secret(FEISHU_APP_SECRET) \
    .log_level(lark.LogLevel.INFO) \
    .build()


async def reply_text(event: P2ImMessageReceiveV1, content: str):
    message = event.event.message
    sender = event.event.sender
    at_user = ""
    if sender.sender_type == "user" and sender.sender_id and sender.sender_id.open_id:
        at_user = f'<at user_id="{sender.sender_id.open_id}"></at> '
    request = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
                      .receive_id(message.chat_id)
                      .msg_type("text")
                      .content(lark.JSON.marshal({"text": at_user + content}))
                      .build()) \
        .build()
    resp = await client.im.v1.message.acreate(request)
    if not resp.success():
        logger.error(f"回复消息失败: {resp.msg}, {resp.code}")


def on_message(event: P2ImMessageReceiveV1):
    logger.info(">>> on_message 回调被触发")
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_handle_message(event))
    except RuntimeError:
        logger.error("没有可用的事件循环，尝试新线程")
        import threading
        threading.Thread(target=lambda: asyncio.run(_handle_message(event)), daemon=True).start()


async def _handle_message(event: P2ImMessageReceiveV1):
    try:
        logger.info(">>> _handle_message 开始处理")
        message = event.event.message
        sender = event.event.sender
        if sender.sender_type == "bot":
            logger.info(">>> 跳过机器人自身消息")
            return
        text = lark.JSON.unmarshal(message.content, dict).get("text", "").strip()
        text = text.replace("@_user_1", "").strip()
        logger.info(f">>> 收到飞书消息: {text}")

        if any(k in text for k in ["状态", "查看状态", "运行状态", "当前状态"]):
            busy, info = is_yingdao_busy()
            await reply_text(event, info)
            return

        if "运行" in text:
            raw_content = text.replace("运行", "").strip()
            parts = raw_content.split()
            if len(parts) == 0:
                tip = "⚠️ 指令格式错误！\n1.运行 流程名\n2.运行 流程名 RP批次号\n示例：运行 美工参考图生组图 RP260627006"
                await reply_text(event, tip)
                return

            target_rp = ""
            if len(parts) >= 2 and parts[-1].startswith("RP"):
                target_rp = parts[-1]
                app_name = " ".join(parts[:-1])
            else:
                app_name = " ".join(parts)

            tip_msg = f"收到指令，准备运行：{app_name}"
            if target_rp:
                tip_msg += f"，批次号：{target_rp}"
            await reply_text(event, tip_msg)

            is_busy, info = is_yingdao_busy()
            if is_busy:
                await reply_text(event, f"⚠️ 目前有任务在执行，无法重复运行\n{info}")
                return

            async with RUN_STATE_LOCK:
                RUN_QUEUE.append((app_name, target_rp, reply_text, event))
            await _ensure_worker()
            return
        else:
            tip = "⚠️ 无效指令\n可用指令：查询状态 / 运行 流程名 / 运行 流程名 RP批次号"
            await reply_text(event, tip)
    except Exception as e:
        logger.exception(f">>> _handle_message 处理异常: {e}")


def main():
    logger.info("✅ 飞书机器人已启动，等待消息...")
    event_handler = EventDispatcherHandler.builder("", "", lark.LogLevel.INFO) \
        .register_p2_im_message_receive_v1(on_message) \
        .build()
    ws_client = ws.Client(FEISHU_APP_ID, FEISHU_APP_SECRET, lark.LogLevel.INFO, event_handler)
    ws_client.start()


if __name__ == "__main__":
    main()