"""
领星ERP 采购单-待到货 到货量监控脚本

功能：
  1. 使用 Playwright 模拟登录领星ERP
  2. 导航到 采购 -> 采购单 -> 待到货 页面
  3. 提取采购单到货量数据，与上次数据对比
  4. 检测到变化时通过飞书 Webhook 机器人推送通知

使用方式：
  1. 填写下方配置区的 FEISHU_WEBHOOK_URL
  2. 首次运行：python monitor_lingxing.py
     （HEADLESS=False 会弹出浏览器，方便观察登录和页面加载）
  3. 如遇验证码，在弹出的浏览器中手动完成，脚本会等待
  4. 登录成功后会自动保存 storage_state，后续运行可复用
  5. 页面选择器如有偏差，参考 DEBUG 截图调整 extract_arrival_data 函数
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import logging
from datetime import datetime
from difflib import unified_diff
from pathlib import Path

import requests
from playwright.async_api import async_playwright, Page, BrowserContext

# ==================== 配置区 ====================

# 领星登录
LINGXING_LOGIN_URL = "https://erp.lingxing.com/login"
LINGXING_USERNAME = "YX-AI-WJH"
LINGXING_PASSWORD = "wjh123456"

# 飞书 Webhook 机器人地址（在飞书群 -> 设置 -> 群机器人 -> 添加自定义机器人 获取）
FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fcdca573-3ec3-40d2-b186-b79e9530d3f5"  # <-- 必填，形如 https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx

# 监控间隔（秒）
CHECK_INTERVAL = 100  # 默认 5 分钟,先调整为1分钟先

# 是否无头模式（首次运行建议 False，确认登录正常后改 True）
HEADLESS = False

# 调试模式：保存截图和页面 HTML，方便调整选择器
DEBUG = True

# 创建时间筛选范围（天）—— 只监控最近 N 天内创建的采购单
CREATE_TIME_DAYS = 45

# 登录超时（秒）—— 留足时间应对可能的验证码
LOGIN_TIMEOUT = 120

# 浏览器配置
# 使用 Microsoft Edge 浏览器（channel='msedge' 会自动查找系统安装的 Edge）
# 如果 channel 方式失败，可以取消注释并填写 EDGE_EXECUTABLE_PATH
BROWSER_CHANNEL = "msedge"
EDGE_EXECUTABLE_PATH = ""  # 如: r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 文件路径
SCRIPT_DIR = Path(__file__).parent.resolve()
STORAGE_STATE_FILE = SCRIPT_DIR / "storage_state.json"
DATA_FILE = SCRIPT_DIR / "arrival_data.json"
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"

# ==================== 日志配置 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("lingxing_monitor")


# ==================== 飞书通知 ====================

def send_feishu(message: str) -> bool:
    """通过飞书 Webhook 机器人发送文本消息"""
    if not FEISHU_WEBHOOK_URL:
        logger.warning("未配置 FEISHU_WEBHOOK_URL，跳过飞书通知")
        return False
    payload = {"msg_type": "text", "content": {"text": message}}
    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)
        result = resp.json()
        if result.get("code", 0) == 0 or result.get("StatusCode", 0) == 0:
            logger.info("飞书通知发送成功")
            return True
        else:
            logger.error(f"飞书通知发送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"飞书通知异常: {e}")
        return False


# ==================== 登录领星 ====================

async def login_lingxing(page: Page) -> bool:
    """
    登录领星ERP。
    返回 True 表示登录成功。
    如遇验证码，会在浏览器中等待用户手动完成。
    """
    logger.info(f"正在打开登录页: {LINGXING_LOGIN_URL}")
    await page.goto(LINGXING_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # 尝试定位账号密码输入框（领星页面可能的多种选择器）
    # 账号输入框
    username_input = await _find_input(
        page,
        placeholders=["请输入账号", "请输入用户名", "账号", "用户名", "手机号"],
        types=["text", "tel"],
    )
    if username_input:
        await username_input.fill(LINGXING_USERNAME)
        logger.info(f"已填入账号: {LINGXING_USERNAME}")
    else:
        logger.error("未找到账号输入框，请检查登录页面结构")
        await _debug_snapshot(page, "login_page")
        return False

    # 密码输入框
    password_input = await _find_input(
        page,
        placeholders=["请输入密码", "密码"],
        types=["password"],
    )
    if password_input:
        await password_input.fill(LINGXING_PASSWORD)
        logger.info("已填入密码")
    else:
        logger.error("未找到密码输入框，请检查登录页面结构")
        await _debug_snapshot(page, "login_page")
        return False

    # 点击登录按钮
    login_btn = await _find_login_button(page)
    if login_btn:
        await login_btn.click()
        logger.info("已点击登录按钮")
    else:
        # 尝试回车提交
        await password_input.press("Enter")
        logger.info("未找到登录按钮，尝试回车提交")

    # 等待登录完成 —— URL 变化或出现首页元素
    logger.info(f"等待登录完成（最多 {LOGIN_TIMEOUT} 秒）...")
    if LOGIN_TIMEOUT > 0:
        logger.info("如遇验证码/滑块，请在弹出的浏览器中手动完成")
    try:
        # 登录成功后 URL 通常会离开 /login
        await page.wait_for_url(
            lambda url: "/login" not in url,
            timeout=LOGIN_TIMEOUT * 1000,
        )
        logger.info(f"登录成功，当前页面: {page.url}")
        await page.wait_for_timeout(3000)  # 等待首页加载
        return True
    except Exception:
        # 检查是否已经在首页（有些情况 URL 不会变）
        if "/login" not in page.url:
            logger.info(f"登录成功，当前页面: {page.url}")
            return True
        logger.error("登录超时，请检查验证码或页面结构")
        await _debug_snapshot(page, "login_timeout")
        return False


async def _find_input(page: Page, placeholders: list[str], types: list[str]):
    """根据 placeholder 和 type 定位输入框"""
    for ph in placeholders:
        for t in types:
            el = page.locator(f'input[type="{t}"][placeholder*="{ph}"]')
            if await el.count() > 0:
                return el.first
    # 退而求其次：按 type 找
    for t in types:
        el = page.locator(f'input[type="{t}"]')
        if await el.count() > 0:
            return el.first
    return None


async def _find_login_button(page: Page):
    """定位登录按钮"""
    # 尝试多种定位方式
    candidates = [
        page.locator('button:has-text("登录")'),
        page.locator('button:has-text("登 录")'),
        page.locator('button:has-text("Login")'),
        page.locator('[class*="login"]>> button'),
        page.locator('button[type="submit"]'),
        page.locator('span:has-text("登录")'),
        page.locator('div:has-text("登录") >> nth=0'),
    ]
    for cand in candidates:
        try:
            if await cand.count() > 0 and await cand.first.is_visible():
                return cand.first
        except Exception:
            continue
    return None


# ==================== 导航到采购单-待到货 ====================

async def navigate_to_purchase_arrival(page: Page) -> bool:
    """
    导航到 采购 -> 采购单 -> 待到货 页面。
    会尝试多种方式定位菜单，如失败可参考 DEBUG 截图手动调整。
    """
    logger.info("开始导航到 采购-采购单-待到货 页面")

    # 方式1：尝试直接通过URL跳转（领星ERP的采购单URL，需根据实际调整）
    purchase_urls = [
        "https://erp.lingxing.com/erp/msupply/purchaseOrder",
        "https://erp.lingxing.com/purchase/purchaseOrder",
        "https://erp.lingxing.com/#/purchase/purchaseOrder",
        "https://erp.lingxing.com/purchase",
    ]
    for url in purchase_urls:
        try:
            resp = await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            # 检查是否到了采购页面（没有被重定向回登录页）
            if "/login" not in page.url:
                logger.info(f"通过URL跳转成功: {page.url}")
                break
        except Exception:
            continue
    else:
        # 方式2：通过点击菜单导航
        logger.info("URL跳转失败，尝试通过菜单导航...")
        if not await _navigate_by_menu(page):
            logger.error("菜单导航失败，请手动调整导航逻辑")
            await _debug_snapshot(page, "navigate_fail")
            return False

    # 切换到"待到货"标签/筛选
    await _switch_to_arrival_tab(page)
    await page.wait_for_timeout(2000)

    # 调整创建时间筛选为最近 N 天
    await _set_create_time_filter(page, days=CREATE_TIME_DAYS)

    await _debug_snapshot(page, "arrival_page")
    logger.info("已到达待到货页面")
    return True


async def _navigate_by_menu(page: Page) -> bool:
    """通过点击左侧菜单导航到采购单"""
    menu_items = ["采购", "采购管理", "采购单", "采购订单"]
    for item in menu_items:
        try:
            el = page.locator(f'text="{item}"').first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(1500)
                logger.info(f"点击了菜单: {item}")
        except Exception:
            continue

    # 再点击"采购单"
    for item in ["采购单", "采购订单"]:
        try:
            el = page.locator(f'text="{item}"').first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(2000)
                logger.info(f"点击了子菜单: {item}")
                return True
        except Exception:
            continue
    return False


async def _switch_to_arrival_tab(page: Page):
    """切换到"待到货"标签或筛选"""
    # 如果URL已经是采购单页面，检查是否已经有"待到货"处于激活状态
    if "purchaseOrder" in page.url:
        logger.info("当前已在采购单页面，检查是否需要切换待到货标签")
        # 尝试查找已激活的"待到货"标签（通常有active类或不同样式）
        try:
            active_tab = page.locator('[class*="active"]:has-text("待到货"), .is-active:has-text("待到货"), .active:has-text("待到货")')
            if await active_tab.count() > 0:
                logger.info("待到货标签已处于激活状态")
                return
        except Exception:
            pass

    tab_texts = ["待到货", "待到货中", "未到货"]
    for text in tab_texts:
        try:
            # 使用更精确的定位：查找标签/导航项而不是页面中任何包含该文本的元素
            selectors = [
                f'[role="tab"]:has-text("{text}")',
                f'.el-tabs__item:has-text("{text}")',
                f'.ant-tabs-tab:has-text("{text}")',
                f'.tab-item:has-text("{text}")',
                f'[class*="tab"]:has-text("{text}")',
            ]
            for sel in selectors:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(2000)
                    logger.info(f"切换到标签: {text}")
                    return
        except Exception:
            continue
    logger.warning("未找到'待到货'标签，可能在当前页面已默认显示")


async def _set_create_time_filter(page: Page, days: int = 45):
    """
    设置创建时间筛选为最近 N 天（包含今天）。
    例如今天是 2026-07-13，days=45，则范围为 2026-05-29 到 2026-07-13。
    """
    from datetime import datetime, timedelta

    today = datetime.today().date()
    start_date = today - timedelta(days=days - 1)  # 包含今天
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    logger.info(f"设置创建时间筛选: {start_str} 至 {end_str}（最近 {days} 天）")

    try:
        # 点击"创建时间"筛选器
        create_time_selectors = [
            'text=创建时间',
            '.filter-item:has-text("创建时间")',
            '[class*="filter"]:has-text("创建时间")',
            'span:has-text("创建时间")',
        ]
        clicked = False
        for sel in create_time_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(500)
                    clicked = True
                    logger.info("已点击创建时间筛选器")
                    break
            except Exception:
                continue

        if not clicked:
            logger.warning("未找到可点击的创建时间筛选器")
            return

        # 通过JS直接修改页面上的日期输入框（如果存在）
        modified = await page.evaluate(f"""
            () => {{
                const startStr = "{start_str}";
                const endStr = "{end_str}";
                let modified = false;

                // 尝试修改日期输入框
                const inputs = document.querySelectorAll('input');
                for (const input of inputs) {{
                    const type = input.getAttribute('type');
                    const placeholder = input.getAttribute('placeholder') || '';
                    const className = input.className || '';

                    // 通过placeholder或class判断
                    if (placeholder.includes('开始') || placeholder.includes('开始日期') ||
                        placeholder.includes('start') || className.includes('start') ||
                        input.name?.includes('start')) {{
                        input.value = startStr;
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        modified = true;
                    }}
                    if (placeholder.includes('结束') || placeholder.includes('结束日期') ||
                        placeholder.includes('end') || className.includes('end') ||
                        input.name?.includes('end')) {{
                        input.value = endStr;
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        modified = true;
                    }}
                }}

                return modified;
            }}
        """)

        if modified:
            logger.info("已通过输入框设置创建时间")

        # 尝试点击日期选择器中的"今天"或"最近30天"等快捷选项
        quick_options = ["最近30天", "最近45天", "最近60天", "最近90天"]
        for option_text in quick_options:
            try:
                el = page.locator(f'text="{option_text}"').first
                if await el.count() > 0 and await el.is_visible():
                    # 不点击，因为我们要精确45天
                    continue
            except Exception:
                continue

        # 点击确认按钮
        confirm_selectors = [
            'button:has-text("确定")',
            'button:has-text("确认")',
            '.el-button:has-text("确定")',
            'span:has-text("确定")',
        ]
        for sel in confirm_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(1500)
                    logger.info("已点击确定按钮")
                    break
            except Exception:
                continue

        # 如果没找到确定按钮，尝试点击空白处关闭日期面板
        if not modified:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

        # 点击查询/搜索按钮刷新列表
        search_selectors = [
            'button:has-text("查询")',
            'button:has-text("搜索")',
            '.el-button:has-text("查询")',
            'span:has-text("查询")',
        ]
        for sel in search_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(2000)
                    logger.info("已点击查询按钮刷新列表")
                    return
            except Exception:
                continue

        # 如果也没有查询按钮，尝试按Enter键
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)

    except Exception as e:
        logger.error(f"设置创建时间筛选失败: {e}")


# ==================== 提取到货量数据 ====================

async def extract_arrival_data(page: Page) -> list[dict]:
    """
    从采购单待到货页面提取数据，遍历所有分页。
    使用边滚动边增量采集策略，避免虚拟列表回收旧DOM导致数据丢失。
    只提取采购单号和到货量信息。

    返回格式：[{"采购单号": "PO260711011", "到货量": "0/900", ...}, ...]
    """
    logger.info("开始提取到货量数据")

    await page.wait_for_timeout(5000)

    all_records = []
    page_num = 1

    while True:
        logger.info(f"正在提取第 {page_num} 页数据...")

        # 使用边滚动边增量采集策略
        page_records = await _scroll_and_extract(page)
        if page_records:
            logger.info(f"第 {page_num} 页提取到 {len(page_records)} 个采购单")
            all_records.extend(page_records)
        else:
            logger.warning(f"第 {page_num} 页未提取到数据")

        # 检查是否有下一页
        has_next = await _has_next_page(page)
        if not has_next:
            logger.info("已到达最后一页")
            break

        # 点击下一页
        success = await _click_next_page(page)
        if not success:
            logger.warning("无法点击下一页，停止翻页")
            break

        await page.wait_for_timeout(3000)
        page_num += 1

    logger.info(f"总共提取到 {len(all_records)} 个采购单")
    return all_records


async def _scroll_and_extract(page: Page) -> list[dict]:
    """
    边滚动边增量采集：使用scrollIntoViewIfNeeded策略触发虚拟滚动加载，
    每次滚动到当前可见的最后一个采购单行，确保真正触发加载。
    用Set按采购单号去重，避免虚拟列表回收旧DOM导致数据丢失。
    """
    logger.info("开始边滚动边采集数据...")

    collected_pos = set()
    all_records = []

    same_count = 0
    max_iterations = 100

    for i in range(max_iterations):
        # 提取当前可见的采购单数据
        records = await _extract_po_rows_via_js(page)
        new_count = 0
        for rec in records:
            po_number = rec.get("采购单号", "")
            if po_number and po_number not in collected_pos:
                collected_pos.add(po_number)
                all_records.append(rec)
                new_count += 1

        logger.info(f"第 {i+1} 轮: 新增 {new_count} 个，累计 {len(collected_pos)} 个采购单")

        if new_count > 0:
            same_count = 0
        else:
            same_count += 1
            if same_count >= 8:
                logger.info(f"连续8轮无新增数据，停止滚动（累计 {len(collected_pos)} 个）")
                break

        # 使用scrollIntoViewIfNeeded策略滚动到最后一个可见的采购单行
        scrolled = await page.evaluate("""
            () => {
                const poPattern = /(PO|CG)\\d{6,}/;
                // 查找包含采购单号的行元素
                const poRows = [];
                const allElements = document.querySelectorAll('tr, div[class*="row"], div[class*="item"]');
                for (const el of allElements) {
                    if (poPattern.test(el.innerText)) {
                        poRows.push(el);
                    }
                }

                if (poRows.length === 0) {
                    return { scrolled: false, reason: 'no_po_rows' };
                }

                // 滚动到最后一个采购单行
                const lastRow = poRows[poRows.length - 1];
                lastRow.scrollIntoViewIfNeeded({ behavior: 'smooth', block: 'center' });

                return { scrolled: true, count: poRows.length };
            }
        """)

        if not scrolled.get("scrolled"):
            logger.warning(f"滚动失败: {scrolled.get('reason')}")
            # 尝试备用滚动方式
            await page.evaluate("window.scrollBy(0, 800)")

        await page.wait_for_timeout(1000)

        # 同时滚动表格容器（如果有独立滚动区域）
        await page.evaluate("""
            () => {
                const scrollables = document.querySelectorAll(
                    '.el-table__body-wrapper, .ant-table-body, [class*="table-body"], [class*="scroll"], .main-content, .content-wrapper'
                );
                for (const el of scrollables) {
                    if (el.scrollHeight > el.clientHeight) {
                        el.scrollTop += 600;
                    }
                }
            }
        """)
        await page.wait_for_timeout(800)

        # 检查是否到达底部
        reached_bottom = await page.evaluate("""
            () => {
                return window.innerHeight + window.scrollY >= document.body.scrollHeight - 100;
            }
        """)
        if reached_bottom:
            # 到达底部后再尝试几次，确保所有数据都加载了
            if same_count >= 3:
                logger.info(f"已滚动到页面底部（累计 {len(collected_pos)} 个）")
                break

    logger.info(f"滚动采集完成，本页共 {len(all_records)} 个采购单")
    return all_records


async def _has_next_page(page: Page) -> bool:
    """检查是否有下一页"""
    try:
        # 常见的下一页按钮选择器
        next_selectors = [
            'button:has-text("下一页"):not([disabled])',
            'a:has-text("下一页"):not(.disabled)',
            '.el-pagination .btn-next:not([disabled])',
            '.ant-pagination-next:not(.ant-pagination-disabled)',
            '[class*="pagination"] button:has-text(">"):not([disabled])',
            '[title="下一页"]:not([disabled])',
        ]
        for sel in next_selectors:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                return True
        return False
    except Exception:
        return False


async def _click_next_page(page: Page) -> bool:
    """点击下一页按钮"""
    try:
        next_selectors = [
            'button:has-text("下一页"):not([disabled])',
            'a:has-text("下一页"):not(.disabled)',
            '.el-pagination .btn-next:not([disabled])',
            '.ant-pagination-next:not(.ant-pagination-disabled)',
            '[class*="pagination"] button:has-text(">"):not([disabled])',
            '[title="下一页"]:not([disabled])',
        ]
        for sel in next_selectors:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                logger.info("已点击下一页")
                return True
        return False
    except Exception:
        return False


async def _extract_po_rows_via_js(page: Page) -> list[dict]:
    """
    使用JavaScript提取采购单父行（只提取包含采购单号的行）。
    过滤掉SKU子行，提取采购单号和到货量信息。
    """
    try:
        result = await page.evaluate("""
            () => {
                const records = [];
                const poPattern = /(PO|CG)\\d{6,}/;
                const foundPoNumbers = new Set();

                // 策略1：在table中查找包含采购单号的行
                const tables = document.querySelectorAll('table');
                for (const table of tables) {
                    const rows = table.querySelectorAll('tr');
                    for (const row of rows) {
                        const rowText = row.innerText.trim();
                        const poMatch = rowText.match(poPattern);
                        if (poMatch) {
                            const poNumber = poMatch[0];
                            // 避免重复
                            if (foundPoNumbers.has(poNumber)) continue;
                            foundPoNumbers.add(poNumber);

                            // 提取到货量信息（匹配 "X / Y" 或 "X/Y" 格式）
                            const arrivalMatch = rowText.match(/到货[量]*[:：]?\\s*(\\d+)\\s*[/／]\\s*(\\d+)/);
                            const cells = row.querySelectorAll('td');

                            const record = {
                                "采购单号": poNumber,
                                "_raw": rowText.substring(0, 200)  // 限制长度
                            };

                            if (arrivalMatch) {
                                record["到货量"] = arrivalMatch[1] + "/" + arrivalMatch[2];
                                record["已到货"] = arrivalMatch[1];
                                record["总数量"] = arrivalMatch[2];
                            }

                            // 尝试从单元格提取结构化数据
                            cells.forEach((cell, idx) => {
                                const cellText = cell.innerText.trim();
                                if (cellText) {
                                    // 尝试识别列名（通过表头）
                                    const headerRow = table.querySelector('tr');
                                    if (headerRow) {
                                        const headers = headerRow.querySelectorAll('th');
                                        if (idx < headers.length) {
                                            const headerText = headers[idx].innerText.trim();
                                            if (headerText) {
                                                record[headerText] = cellText;
                                            }
                                        }
                                    }
                                }
                            });

                            records.push(record);
                        }
                    }
                }

                // 策略2：如果table没找到，在div结构中查找
                if (records.length === 0) {
                    const allElements = document.querySelectorAll('div, tr, [class*="row"]');
                    for (const el of allElements) {
                        const text = el.innerText.trim();
                        const poMatch = text.match(poPattern);
                        if (poMatch) {
                            const poNumber = poMatch[0];
                            if (foundPoNumbers.has(poNumber)) continue;
                            foundPoNumbers.add(poNumber);

                            const arrivalMatch = text.match(/到货[量]*[:：]?\\s*(\\d+)\\s*[/／]\\s*(\\d+)/);
                            const record = {
                                "采购单号": poNumber,
                                "_raw": text.substring(0, 200)
                            };

                            if (arrivalMatch) {
                                record["到货量"] = arrivalMatch[1] + "/" + arrivalMatch[2];
                                record["已到货"] = arrivalMatch[1];
                                record["总数量"] = arrivalMatch[2];
                            }

                            records.push(record);
                        }
                    }
                }

                return { count: records.length, data: records };
            }
        """)

        if result and result.get("data"):
            logger.info(f"JS提取到 {result['count']} 个采购单")
            return result["data"]
        return []
    except Exception as e:
        logger.debug(f"JS提取异常: {e}")
        return []


async def _extract_via_js(page: Page) -> list[dict]:
    """使用JavaScript在页面内提取表格数据，支持div模拟表格（备用方案）"""
    try:
        result = await page.evaluate("""
            () => {
                const records = [];

                // 尝试1：查找标准table
                const tables = document.querySelectorAll('table');
                for (const table of tables) {
                    const rows = table.querySelectorAll('tr');
                    if (rows.length < 2) continue;

                    const headers = [];
                    const headerCells = rows[0].querySelectorAll('th, td');
                    headerCells.forEach(h => headers.push(h.innerText.trim()));

                    for (let i = 1; i < rows.length; i++) {
                        const cells = rows[i].querySelectorAll('td');
                        if (cells.length === 0) continue;
                        const record = {};
                        for (let j = 0; j < Math.min(cells.length, headers.length); j++) {
                            record[headers[j]] = cells[j].innerText.trim();
                        }
                        if (Object.keys(record).length > 0) {
                            records.push(record);
                        }
                    }
                }
                if (records.length > 0) return { type: 'table', data: records };

                // 尝试2：查找div模拟的表格行
                const rowSelectors = [
                    '.el-table__row',
                    '[class*="table-row"]',
                    '[class*="data-row"]',
                    '[class*="list-item"]',
                    '.ant-table-row',
                ];
                for (const sel of rowSelectors) {
                    const rows = document.querySelectorAll(sel);
                    if (rows.length > 1) {
                        const data = [];
                        rows.forEach((row, idx) => {
                            const text = row.innerText.trim();
                            if (text) {
                                data.push({ _raw: text, _index: idx });
                            }
                        });
                        if (data.length > 0) return { type: 'div-rows', data };
                    }
                }

                return { type: 'none', data: [] };
            }
        """)

        if result and result.get("data"):
            logger.info(f"JS提取类型: {result['type']}, 记录数: {len(result['data'])}")
            return result["data"]
        return []
    except Exception as e:
        logger.debug(f"JS提取异常: {e}")
        return []


async def _extract_from_table(page: Page) -> list[dict]:
    """从 table 元素提取数据"""
    try:
        table_count = await page.locator("table").count()
        if table_count == 0:
            return []

        # 获取最后一个表格（通常是数据表格，前面可能有筛选表格）
        table = page.locator("table").last
        rows = table.locator("tr")
        row_count = await rows.count()
        if row_count < 2:
            return []

        # 提取表头
        header_cells = rows.first.locator("th, td")
        headers = []
        for i in range(await header_cells.count()):
            text = await header_cells.nth(i).inner_text()
            headers.append(text.strip())

        logger.info(f"检测到表格表头: {headers}")

        # 提取数据行
        records = []
        for i in range(1, row_count):
            cells = rows.nth(i).locator("td")
            cell_count = await cells.count()
            if cell_count == 0:
                continue
            record = {}
            for j in range(min(cell_count, len(headers))):
                record[headers[j]] = (await cells.nth(j).inner_text()).strip()
            if record:
                records.append(record)

        return records
    except Exception as e:
        logger.debug(f"表格提取异常: {e}")
        return []


async def _extract_from_list(page: Page) -> list[dict]:
    """从列表项（非 table 结构）提取数据"""
    try:
        # 尝试常见的列表行选择器
        row_selectors = [
            "[class*='table-row']",
            "[class*='list-item']",
            "[class*='data-row']",
            ".el-table__row",
            "tr",
        ]
        for sel in row_selectors:
            rows = page.locator(sel)
            count = await rows.count()
            if count > 1:
                logger.info(f"使用选择器 {sel} 找到 {count} 行")
                records = []
                for i in range(count):
                    row_text = await rows.nth(i).inner_text()
                    # 将行文本按换行或多空格分割
                    parts = [p.strip() for p in row_text.split("\n") if p.strip()]
                    if parts:
                        records.append({"_raw": " | ".join(parts), "_index": i})
                return records
        return []
    except Exception as e:
        logger.debug(f"列表提取异常: {e}")
        return []


# ==================== 数据对比 ====================

def load_previous_data() -> dict:
    """加载上次保存的数据"""
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"加载历史数据失败: {e}")
    return {}


def save_current_data(data: dict):
    """保存当前数据"""
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def compare_data(previous: dict, current: dict) -> list[str]:
    """
    对比上次和当前的数据，只关注到货量变化和新增采购单。
    返回格式化的变化列表。

    数据格式: { "采购单号": {...}, ... } 或 { "PO260711011": {...}, ... }
    """
    changes = []

    # 如果没有历史数据（首次运行），返回空（不通知）
    if not previous:
        logger.info("首次运行，保存基准数据，暂不发通知")
        return []

    prev_keys = set(previous.keys())
    curr_keys = set(current.keys())

    # 只关注新增的采购单（消失的不关心）
    new_keys = curr_keys - prev_keys
    for key in new_keys:
        po_number = _extract_po_number(key)
        arrival_info = _extract_arrival_info(current[key])
        changes.append(f"🆕 新增采购单: {po_number}  到货量 {arrival_info}")

    # 到货量变化（只关注已存在的采购单）
    common_keys = prev_keys & curr_keys
    for key in common_keys:
        old_rec = previous[key]
        new_rec = current[key]

        # 提取到货量信息进行对比
        old_arrival = _extract_arrival_value(old_rec)
        new_arrival = _extract_arrival_value(new_rec)

        if old_arrival != new_arrival and new_arrival is not None:
            po_number = _extract_po_number(key)
            total = _extract_total_quantity(new_rec)
            if total:
                changes.append(f"📊 到货量变化: {po_number}  {old_arrival} → {new_arrival} / {total}")
            else:
                changes.append(f"📊 到货量变化: {po_number}  {old_arrival} → {new_arrival}")

    return changes


def _extract_po_number(record_key: str) -> str:
    """从记录key中提取采购单号"""
    if record_key.startswith("PO") or record_key.startswith("CG"):
        return record_key
    if isinstance(record_key, str) and len(record_key) > 10:
        return record_key[:20] + "..."
    return record_key


def _extract_arrival_info(record: dict) -> str:
    """提取到货量的完整信息（如 '0/900'）"""
    arrived = _extract_arrival_value(record)
    total = _extract_total_quantity(record)
    if arrived and total:
        return f"{arrived}/{total}"
    elif arrived:
        return f"{arrived}/?"
    elif total:
        return f"?/{total}"
    else:
        return "未知"


def _extract_arrival_value(record: dict) -> str | None:
    """提取已到货数量"""
    # 优先从新格式的字段提取
    if "已到货" in record and record["已到货"]:
        return record["已到货"].strip()
    if "到货量" in record and record["到货量"]:
        # 到货量格式为 "X/Y"，取X部分
        parts = record["到货量"].split("/")
        if len(parts) == 2:
            return parts[0].strip()

    # 尝试从结构化字段提取
    arrival_fields = ["已到货", "已到货数量", "实收数量", "入库数量", "到货数量", "已收货"]
    for field in arrival_fields:
        val = record.get(field, "")
        if val and val.strip():
            return val.strip()

    # 尝试从 _raw 文本中提取
    raw = record.get("_raw", "")
    match = re.search(r'(\d+)\s*/\s*\d+', raw)
    if match:
        return match.group(1).strip()

    return None


def _extract_total_quantity(record: dict) -> str | None:
    """提取总数量（采购数量/订单数量等）"""
    # 优先从新格式的字段提取
    if "总数量" in record and record["总数量"]:
        return record["总数量"].strip()
    if "到货量" in record and record["到货量"]:
        # 到货量格式为 "X/Y"，取Y部分
        parts = record["到货量"].split("/")
        if len(parts) == 2:
            return parts[1].strip()

    # 尝试从结构化字段提取
    total_fields = ["采购数量", "订单数量", "总数量", "应到货", "计划数量", "订购数量", "数量"]
    for field in total_fields:
        val = record.get(field, "")
        if val and val.strip() and val.strip().isdigit():
            return val.strip()

    # 尝试从 _raw 文本提取
    raw = record.get("_raw", "")
    match = re.search(r'\d+\s*/\s*(\d+)', raw)
    if match:
        return match.group(1).strip()

    return None


def records_to_dict(records: list[dict]) -> dict:
    """将记录列表转为字典，以采购单号或索引为key"""
    result = {}
    for i, rec in enumerate(records):
        # 尝试用采购单号作为key
        key = None
        for k in ["采购单号", "单号", "订单号", "单据编号", "编号"]:
            if k in rec and rec[k]:
                key = rec[k]
                break

        # 如果标准字段没找到，尝试从 _raw 中提取采购单号（如 PO260711011）
        if not key and "_raw" in rec:
            raw = rec["_raw"]
            # 匹配 PO 开头的采购单号
            po_match = re.search(r'PO\d{6,}', raw)
            if po_match:
                key = po_match.group(0)
            else:
                # 使用行的前30个字符作为key
                key = raw[:30].strip()

        if not key:
            key = f"row_{i}"
        result[key] = rec
    return result


# ==================== 调试工具 ====================

async def _debug_snapshot(page: Page, name: str):
    """保存截图和页面HTML用于调试"""
    if not DEBUG:
        return
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = SCREENSHOT_DIR / f"{name}_{timestamp}.png"
    html_path = SCREENSHOT_DIR / f"{name}_{timestamp}.html"
    try:
        await page.screenshot(path=str(screenshot_path), full_page=True)
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        logger.info(f"调试截图已保存: {screenshot_path}")
    except Exception as e:
        logger.warning(f"保存调试截图失败: {e}")


# ==================== 主监控逻辑 ====================

async def monitor_once(context: BrowserContext) -> list[str]:
    """执行一次监控，返回变化列表"""
    page = await context.new_page()
    try:
        # 导航到待到货页面
        if not await navigate_to_purchase_arrival(page):
            return ["⚠️ 导航到待到货页面失败，请检查截图"]

        # 提取数据
        records = await extract_arrival_data(page)
        if not records:
            if DEBUG:
                logger.info("数据提取失败，保持页面打开以便调试...")
                logger.info("请查看浏览器页面，分析表格结构后按 Ctrl+C 退出")
                await page.wait_for_timeout(300000)
            return ["⚠️ 未能提取到到货量数据，请检查页面结构"]

        # 转为字典便于对比
        current_data = records_to_dict(records)
        previous_data = load_previous_data()

        # 对比变化
        changes = compare_data(previous_data, current_data)

        # 保存当前数据
        save_current_data(current_data)
        logger.info(f"数据已保存，共 {len(current_data)} 条记录，检测到 {len(changes)} 处变化")

        return changes
    finally:
        await page.close()


async def run():
    """主运行函数"""
    if not FEISHU_WEBHOOK_URL:
        logger.warning("⚠️ 未配置 FEISHU_WEBHOOK_URL，通知功能将不可用")
        logger.warning("   请在脚本顶部配置区填入飞书 Webhook 机器人地址")

    logger.info("=" * 60)
    logger.info("领星ERP 采购单待到货监控 启动")
    logger.info(f"监控间隔: {CHECK_INTERVAL}秒 | 无头模式: {HEADLESS} | 调试: {DEBUG}")
    logger.info(f"浏览器: {'Edge (path)' if EDGE_EXECUTABLE_PATH else 'Edge (channel)'}")
    logger.info("=" * 60)

    async with async_playwright() as p:
        # 使用 Microsoft Edge 浏览器
        if EDGE_EXECUTABLE_PATH:
            logger.info(f"使用 Edge 可执行文件路径: {EDGE_EXECUTABLE_PATH}")
            browser = await p.chromium.launch(
                headless=HEADLESS,
                executable_path=EDGE_EXECUTABLE_PATH,
            )
        else:
            logger.info(f"使用 Edge channel: {BROWSER_CHANNEL}")
            browser = await p.chromium.launch(
                headless=HEADLESS,
                channel=BROWSER_CHANNEL,
            )

        # 尝试复用 storage_state
        has_storage = STORAGE_STATE_FILE.exists()
        if has_storage:
            logger.info("检测到已保存的登录状态，尝试复用...")
            context = await browser.new_context(
                storage_state=str(STORAGE_STATE_FILE),
                viewport={"width": 1920, "height": 1080},
            )
        else:
            logger.info("首次运行，需要登录...")
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
            )

        # 首次登录
        if not has_storage:
            login_page = await context.new_page()
            success = await login_lingxing(login_page)
            if not success:
                logger.error("登录失败，程序退出")
                await login_page.screenshot(path=str(SCREENSHOT_DIR / "login_fail.png"))
                await browser.close()
                return
            # 保存登录状态
            await context.storage_state(path=str(STORAGE_STATE_FILE))
            logger.info(f"登录状态已保存至 {STORAGE_STATE_FILE}")
            await login_page.close()

        # 检查登录状态是否仍然有效（访问首页看是否被重定向到登录页）
        test_page = await context.new_page()
        await test_page.goto("https://erp.lingxing.com", wait_until="domcontentloaded")
        await test_page.wait_for_timeout(3000)
        if "/login" in test_page.url:
            logger.warning("登录状态已过期，重新登录...")
            await test_page.close()
            # 删除旧状态重新登录
            STORAGE_STATE_FILE.unlink(missing_ok=True)
            login_page = await context.new_page()
            success = await login_lingxing(login_page)
            if not success:
                logger.error("重新登录失败，程序退出")
                await browser.close()
                return
            await context.storage_state(path=str(STORAGE_STATE_FILE))
            await login_page.close()
        else:
            await test_page.close()
            logger.info("登录状态有效")

        # 发送启动通知
        send_feishu("✅ 领星ERP采购单待到货监控已启动，开始实时检测...")

        # 监控循环
        round_num = 0
        try:
            while True:
                round_num += 1
                logger.info(f"--- 第 {round_num} 轮监控 ---")
                try:
                    changes = await monitor_once(context)
                    if changes:
                        msg = f"🔔 领星待到货监控报告 ({datetime.now().strftime('%H:%M:%S')})\n"
                        msg += f"检测到 {len(changes)} 处变化:\n\n"
                        msg += "\n\n".join(changes)
                        logger.info(f"检测到变化:\n{msg}")
                        send_feishu(msg)
                    else:
                        logger.info("无变化")
                except Exception as e:
                    logger.exception(f"监控异常: {e}")
                    send_feishu(f"⚠️ 领星监控异常: {e}")

                logger.info(f"等待 {CHECK_INTERVAL} 秒后进行下一轮监控...")
                await asyncio.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            logger.info("用户中断，程序退出")
            send_feishu("🛑 领星ERP监控已停止")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
