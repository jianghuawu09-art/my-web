import json
import time
import requests
import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

APP_ID = "cli_aacca74a7dfa1cb5"
APP_SECRET = "A30v5nUuMM0ld8JVPiaCfgAeI318FhJ4"

# 固定人员： open_id
CAIGOU_OPEN_ID = "4e523fe9"  # 采购的 open_id
WENYUAN_OPEN_ID = "b2a8183e"  # 文员的 open_id

# 运营人员姓名 -> user_id 映射（手动维护，避免群成员接口权限/可见性问题）
PERSON_USER_IDS = {
    "余丽满": "ac18e64b",
    "郑乐瑶": "fb9cf195",
    "张巧慧": "e5212776",
    "谭红美": "e117f4c9",
    "郭琪": "ac3d826f",
    "彭晶": "1bda432e",
    "马龙发": "7256eefg",
}


lark_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

# ==================== 品名对应人员映射 ====================
PERSON_PRODUCTS = {
    "余丽满": [
        "CA-OCX20", "CA-UC001", "UQY-OCX20", "UQY02", "UQY03", "UQY10", "UQY12",
        "UQY14", "UQY17", "UQY18", "UQY19", "UQY22", "UQY23", "UQY24", "UQY25",
        "UQY27", "UQY31", "UQY32", "UQY36", "UQY37", "UQY40", "UQY46", "UQY49",
        "UQY51", "UQY53", "YHT15", "YHT30", "YHT33", "YHT36", "YHT37", "YHT41",
        "YHT42", "YHT43", "YHT44", "YHT45", "YHT48", "YHT49", "YHT50", "YHT51",
        "YHT53", "YHT57", "YHT59", "YHT60", "YHT61", "YHT71", "YXL10", "YXL33",
        "YXL38", "YXL48", "YXL52", "YXL62", "YXP03", "YXP12", "YXP18", "YXP37",
        "YXP39", "YXP43", "YXP46", "YXQ05", "YXQ17", "YXQ19", "YXQ20", "YXQ23",
        "YXQ30", "YXQ31", "YXQ37", "YXQ56", "YXQ58", "YXQ66",
    ],
    "郑乐瑶": [
        "CA-UC002", "CA-UC003", "CA-UC004", "UQY01", "UQY05", "UQY11", "UQY15",
        "UQY16", "UQY21", "UQY26", "UQY28", "UQY30", "UQY33", "UQY35", "UQY39",
        "UQY43", "UQY45", "UQY48", "UQY57", "YXL01", "YXL13", "YXL17", "YXL18",
        "YXL26", "YXL27", "YXL28", "YXL35", "YXL40", "YXL46", "YXL50", "YXL51",
        "YXL69", "YXP06", "YXP29", "YXP35", "YXP36", "YXP40", "YXP45", "YXP50",
        "YXQ07", "YXQ11", "YXQ14", "YXQ15", "YXQ24", "YXQ26", "YXQ28", "YXQ34",
        "YXQ35", "YXQ40", "YXQ53", "YXQ70",
    ],
    "张巧慧": [
        "OCX01", "OCX02", "OCX06", "OCX07", "OCX08", "OCX09", "OCX10", "OCX12",
        "OCX13", "OCX16", "OCX18", "OCX19", "OCX20", "OCX22", "OCX23", "OCX24",
        "OCX25", "OCX26", "OCX28", "OCX29", "OCX30", "OCX31", "OCX32", "OCX33",
        "OCX34", "OCX35", "OCX36", "OCX37", "OCX38", "OCX39", "OCX40", "OCX42",
        "OCX43", "OCX44", "OCX54", "OCX55", "OCX57", "OCX58", "OCX59", "OCX60",
        "OCX62", "OCX63", "OCX66", "UQY04", "UQY06", "UQY07", "UQY08", "UQY09",
        "UQY13", "UQY20", "UQY29", "UQY34", "UQY38", "UQY41", "UQY44", "UQY47",
        "UQY50", "UQY56", "YHT01", "YHT03", "YHT04", "YHT05", "YHT07", "YHT08",
        "YHT10", "YHT12", "YHT16", "YHT17", "YHT19", "YHT20", "YHT21", "YHT22",
        "YHT23", "YHT25", "YHT27", "YHT28", "YHT31", "YHT34", "YHT35", "YHT38",
        "YHT39", "YHT40", "YHT46", "YHT47", "YHT52", "YHT65", "YHT68", "YHT70",
        "YHT72", "YHT73", "YHT74", "YHT75", "YXL03", "YXL04", "YXL07", "YXL08",
        "YXL09", "YXL11", "YXL12", "YXL14", "YXL15", "YXL20", "YXL21", "YXL54",
        "YXL67", "YXL72", "YXP01", "YXP02", "YXP04", "YXP05", "YXP07", "YXP08",
        "YXP09", "YXP10", "YXP13", "YXP19", "YXP20", "YXP22", "YXP23", "YXP25",
        "YXP28", "YXP34", "YXP41", "YXQ02", "YXQ03", "YXQ06", "YXQ08", "YXQ09",
        "YXQ12", "YXQ13", "YXQ16", "YXQ18", "YXQ21", "YXQ22", "YXQ25", "YXQ27",
        "YXQ43",
    ],
    "谭红美": [
        "OCX03", "OCX05", "OCX14", "OCX17", "OCX21", "OCX27", "OCX47", "OCX48",
        "OCX49", "OCX50", "OCX51", "OCX61", "YXL05", "YXL16", "YXL22", "YXL31",
        "YXL36", "YXL57", "YXL60", "YXL61", "YXL65", "YXP26", "YXP38", "YXP42",
        "YXP49", "YXQ01", "YXQ59", "YXQ61", "YXQ71",
    ],
    "郭琪": [
        "OCX04", "OCX11", "OCX15", "OCX41", "OCX45", "OCX46", "OCX52", "OCX53",
        "OCX56", "YXL23", "YXL29", "YXL32", "YXL49", "YXL64", "YXP16", "YXP31",
        "YXP52", "YXQ10", "YXQ38", "YXQ54", "YXQ62", "YXQ63", "YXQ74",
    ],
    "彭晶": [
        "YHT02", "YHT06", "YHT09", "YHT11", "YHT24", "YHT26", "YHT29", "YHT32",
        "YHT54", "YHT55", "YHT56", "YHT58", "YHT62", "YHT63", "YHT64", "YHT66",
        "YHT67", "YHT69", "YHT76", "YXL06", "YXL19", "YXL25", "YXL34", "YXL39",
        "YXL42", "YXL58", "YXL63", "YXL68", "YXP14", "YXP17", "YXP27", "YXP30",
        "YXP48", "YXQ04", "YXQ29", "YXQ36", "YXQ41", "YXQ51", "YXQ64", "YXQ65",
        "YXQ68",
    ],
    "马龙发": [
        "YXL02", "YXL24", "YXL37", "YXL43", "YXL45", "YXL53", "YXL55", "YXL56",
        "YXL66", "YXP24", "YXP32", "YXP27-N", "YXP44", "YXP47", "YXQ42", "YXQ45",
        "YXQ52", "YXQ55", "YXQ60", "YXQ67",
    ],
}

# 构建反向查找: 品名(大写) -> 人员
CODE_TO_PERSON = {}
for _person, _codes in PERSON_PRODUCTS.items():
    for _code in _codes:
        CODE_TO_PERSON[_code.upper()] = _person

# ==================== Token管理（用于获取群成员） ====================
_token_cache = {"token": None, "expire": 0}

def get_tenant_access_token():
    if _token_cache["token"] and time.time() < _token_cache["expire"]:
        return _token_cache["token"]
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET})
    data = resp.json()
    if data.get("code") == 0:
        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expire"] = time.time() + 7000
        return _token_cache["token"]
    print("获取token失败:", data)
    return None

# ==================== 群成员缓存 ====================
_member_cache = {}

def get_member_open_id(chat_id, name):
    """从群成员中查找指定名字的open_id，带缓存"""
    if chat_id not in _member_cache:
        token = get_tenant_access_token()
        if not token:
            return None
        members = {}
        page_token = None
        while True:
            url = f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members"
            headers = {"Authorization": f"Bearer {token}"}
            params = {"member_id_type": "open_id", "page_size": 100}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=headers, params=params)
            data = resp.json()
            if data.get("code") == 0:
                for member in data.get("data", {}).get("items", []):
                    members[member.get("name")] = member.get("member_id")
                if data.get("data", {}).get("has_more"):
                    page_token = data.get("data", {}).get("page_token")
                else:
                    break
            else:
                print("获取群成员失败:", data.get("msg"))
                break
        _member_cache[chat_id] = members
        print(f"群成员缓存完成，共 {len(members)} 人")
    return _member_cache.get(chat_id, {}).get(name)

# ==================== 品名匹配 ====================
def find_products_in_text(text):
    """从文本中查找品名，返回 [(code, person), ...]"""
    upper_text = text.upper()
    matches = []
    for code, person in CODE_TO_PERSON.items():
        if code in upper_text:
            matches.append((code, person))
    # 过滤掉被更长品名包含的短品名（如 YXP27 被 YXP27-N 包含）
    filtered = []
    for i, (code1, person1) in enumerate(matches):
        is_substring = False
        for j, (code2, person2) in enumerate(matches):
            if i != j and code1 in code2 and code1 != code2:
                is_substring = True
                break
        if not is_substring:
            filtered.append((code1, person1))
    return filtered

# ==================== 话题回复 ====================
def reply_in_thread(message_id: str, text: str, client):
    """在指定消息下开话题回复"""
    body = (
        lark.api.im.v1.ReplyMessageRequestBody.builder()
        .msg_type("text")
        .content(json.dumps({"text": text}))
        .reply_in_thread(True)
        .build()
    )
    req = (
        lark.api.im.v1.ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.reply(req)
    print("回复结果:", resp)
    if resp.success():
        print("✓ 话题回复成功")
    else:
        print("✗ 失败:", resp.code, resp.msg)

# ==================== 消息处理 ====================
def handle_event(data: P2ImMessageReceiveV1):
    try:
        print("【收到事件】")
        msg = data.event.message
        sender = data.event.sender
        if msg.chat_type != "group" or msg.message_type != "text":
            return

        text = json.loads(msg.content).get("text", "").strip()
        message_id = msg.message_id
        chat_id = msg.chat_id

        if "到货" not in text:
            return

        matches = find_products_in_text(text)
        if not matches:
            return

        people = list(set(person for _, person in matches))
        at_parts = []
        for person in people:
            user_id = PERSON_USER_IDS.get(person)
            if user_id:
                at_parts.append(f'<at user_id="{user_id}">{person}</at>')
            else:
                at_parts.append(person)
                print(f"未配置 user_id: {person}")

        # 固定@采购人员
        fqz_open_id = CAIGOU_OPEN_ID or PERSON_USER_IDS.get("方全钟")
        if fqz_open_id:
            at_parts.append(f'<at user_id="{fqz_open_id}">方全钟</at>')
        else:
            at_parts.append("方全钟")
            print("未找到方全钟的 user_id")

        # 固定@人员
        smq_open_id = WENYUAN_OPEN_ID or PERSON_USER_IDS.get("孙美琴")
        if smq_open_id:
            at_parts.append(f'<at user_id="{smq_open_id}">孙美琴</at>')
        else:
            at_parts.append("孙美琴")
            print("未找到孙美琴的 user_id")

        matched_codes = [code for code, _ in matches]
        reply_text = " ".join(at_parts) + f" {'、'.join(matched_codes)} 到货，请注意查收。"
        print(f"品名匹配: {matches} -> {reply_text}")
        reply_in_thread(message_id, reply_text, lark_client)
    except Exception as e:
        print("处理失败:", e)

# ==================== 启动长连接 ====================
event_handler = (
    lark.EventDispatcherHandler.builder("", "", lark.LogLevel.INFO)
    .register_p2_im_message_receive_v1(handle_event)
    .build()
)

lark.ws.Client(
    APP_ID,
    APP_SECRET,
    event_handler=event_handler,
    log_level=lark.LogLevel.INFO,
).start()
