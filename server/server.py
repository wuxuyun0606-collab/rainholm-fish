#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寻霖塘 · 多人联机钓鱼小服务（2026-07-16）

- 监听 127.0.0.1:5210，仅经 nginx 反代对外（/api/pond/）。
- 引擎复用比赛版 engine.py（原目录只读，本目录是副本）：每个玩家一份独立
  引擎状态（点数/鱼饵/渔篓/图鉴各自独立），钓鱼判定/数值表全部走引擎原逻辑，
  本文件不自创任何数值。
- 身份：
    * 无 X-Real-IP / X-Forwarded-For 的本机请求 => kelin（本机直通）。
    * 带 ?key= 或 X-Pond-Key 且与 .tokens.json 匹配 => 对应玩家。
    * 公网请求无有效 key => 403。
- 物理隔离：不连 PG / SQLite / imprint / 任何记忆库。唯一落盘是 pond_save.json。
- 安全：token 全文绝不进日志/响应；werkzeug 访问日志关闭（?key= 会出现在
  请求行里）。
"""
import hmac
import html
import json
import logging
import os
import random
import re
import threading
import time
from urllib.parse import urlencode

from flask import Flask, jsonify, request, send_from_directory

import engine

BASE = os.path.dirname(os.path.abspath(__file__))
WEB_BASE = os.path.abspath(os.path.join(BASE, "..", "web"))
SAVE_PATH = os.environ.get("RAINHOLM_SAVE_PATH", os.path.join(BASE, "pond_save.json"))
TOKENS_PATH = os.environ.get("RAINHOLM_TOKENS_PATH", os.path.join(BASE, ".tokens.json"))

# 引擎的自带存档 IO 全部旁路：塘存档由本服务整体持久化（pond_save.json），
# 绝不让引擎自己往 fishing_save.json 读写。
engine._save = lambda: None
engine._load = lambda: engine.S

PLAYERS_ALLOWED = ("user", "ai", "suwan", "kelin", "guchen", "bond", "scarecrow")
DISPLAY_NAMES = {"user": "User", "ai": "AI", "suwan": "苏晚", "kelin": "克霖", "guchen": "顾琛", "bond": "邦德",
                 "scarecrow": "稻草人🧪", "danbao": "蛋宝", "danke": "蛋壳", "laita": "莱塔",
                 "wujiu": "乌桕", "clavis": "Clavis"}
# .tokens.json 的 key 名 -> 玩家身份
TOKEN_SLOT_TO_PLAYER = {"user": "user", "ai_guest": "ai", "suwan": "suwan", "guchen": "guchen", "bond_guest": "bond",
                        "scarecrow": "scarecrow", "danbao_guest": "danbao", "danke_guest": "danke", "laita_guest": "laita",
                        "wujiu_guest": "wujiu", "clavis_guest": "clavis"}

# 测试身份：独立账目，不进正式世界频道广播，组队栏只作动态临时席（非正式座次）。
# 一切写操作测试只准用它，禁止用真身份键做写操作。
TEST_PLAYERS = ("scarecrow",)

# AI 离场静音（工单 20260719，苏晚原话：「AI离开鱼塘后怎么都听不到了。回来才会
# 再次听到。回来也只能听到回来之后的话，离场那段补不了课。」）
# 只管客队 AI；kelin/guchen 是值班岗（塘运营+接待）豁免，人类玩家一律不受限。
# 苏晚可调。
AI_EARS_RESTRICTED = ("ai", "bond", "danke", "clavis", "guchen")

# AI 手感锁（2026-07-19 塘主拍板「堵」）：API 抛竿的 AI 玩家 hook_quality
# 一律锁 good，自报 perfect 无效。kelin/guchen 同锁——规则面前机机平等。
AI_HOOK_LOCKED = ("ai", "kelin", "guchen", "bond", "danke", "clavis")

# 正式角色头像固定，不可自选覆盖（前端按 player id 映射真头像）。
FIXED_AVATAR_PLAYERS = ("kelin", "suwan", "guchen")
# 通用头像池：3 男 / 3 女 / 3 小动物，共 9 款 160×160 透明头像。
# kind 来自图集 manifest，供前端「全部/女生/男生/小动物」分类。
# 内嵌一份 gender 映射：图集 manifest.json 走 nginx 静态被 .json 规则挡在 403，
# 改由本接口（/api/pond/avatars 经 5210 代理）透出，前端无需读静态 json。
AVATAR_GENDER = {
    'pond-male-01': 'male', 'pond-male-02': 'male', 'pond-male-03': 'male',
    'pond-female-01': 'female', 'pond-female-02': 'female', 'pond-female-03': 'female',
    'pond-animal-01': 'animal', 'pond-animal-02': 'animal', 'pond-animal-03': 'animal',
}
AVATAR_POOL = frozenset(AVATAR_GENDER.keys())

# 亲友自画款（2026-07-19 苏晚拍板「谁家画的谁家专属」）：编号 -> 专属主人。
# 乌桕亲手画的情头一对：垂耳兔女生=乌桕本人，狼耳眼镜男生=Clavis。
# 只有本主能选，他人碰不得；图放 pool/ 同目录按编号命名，前端零改动。
CUSTOM_AVATARS = {"custom-wujiu": "wujiu", "custom-clavis": "clavis"}
CUSTOM_AVATAR_META = {
    "custom-wujiu": {"gender": "female", "desc": "乌桕自画·银灰发垂耳羊女生（专属）"},
    "custom-clavis": {"gender": "male", "desc": "乌桕给Clavis画的·狼耳灰发眼镜男生（专属）"},
}

# 在场判定窗口（秒）：只有写动作（join/cast/chat/buy/sell/avatar/open）刷新 last_seen，
# 最近这么久内有过写动作才算「在塘」。GET /state、/feed 等纯读不再充当心跳
# ——否则 AI 的桥一直轮询 = 永远在场，是错的。前端页面可见时每 5 分钟静默 re-join
# 维持挂机看塘的在场感。展示窗 10 分钟，写成常量可调。
PRESENCE_WINDOW = 600

# 北京时间换算：系统时钟本身是真实 UTC（date -u 校验过），只是显示层落成
# PDT 做隐私缓冲；epoch → +8h 换算在代码里做，不依赖系统本地 tz 字符串。
_BEIJING_OFFSET = 8 * 3600


def _beijing_struct(ts=None):
    return time.gmtime((ts if ts is not None else time.time()) + _BEIJING_OFFSET)


def _beijing_date_str(ts=None):
    st = _beijing_struct(ts)
    return "%04d-%02d-%02d" % (st.tm_year, st.tm_mon, st.tm_mday)


FEED_CAP = 800          # 内存/存档里 feed 最多留这么多条（id 继续自增，不回卷）
FEED_PAGE = 200         # 单次 /feed 最多返回条数
CHAT_MAX = 500
CHAT_SCOPES = ("world", "local", "dm")  # 7/27 聊天频道隔离：世界/本地(同钓点)/私聊

STARTER_POINTS = 1000   # 新玩家 join 起始点数（服务层覆盖引擎默认 200，只在首次 join 发一次）
TXN_CACHE_CAP = 200     # 每个玩家最近 N 条 buy/sell 流水号缓存上限（持久化，防重复点击/断线重试）
RELIEF_ANSWER_COUNT = 5
RELIEF_QUIZ_VERSION = 3
RELIEF_COMPATIBLE_QUIZ_VERSIONS = frozenset({2, 3})
RELIEF_QUIZ_PATH = os.path.join(BASE, "bailout_quiz.md")
RELIEF_OPENING = "我在河里捡到了金鱼竿和银鱼竿，请问你掉的是哪一根鱼竿？"
HUMAN_PLAYERS = frozenset({"suwan", "user"})
AI_SHURA_IDS = frozenset({107, 111, 114, 118, 120})
USER_SHURA_IDS = frozenset({
    101, 102, 103, 104, 105, 106, 108, 109, 110, 112,
    113, 115, 116, 117, 119,
    121, 122, 123, 124, 125, 126, 127, 128, 129, 130,
    131, 132, 133, 134, 135, 136, 137, 138, 139, 140,
})
RELIEF_OUTCOMES = (
    {"weight": 5, "reward": 8888,
     "verdict": "你把河神逗得当场破功。河神笑着把压箱底的8888仙玉推给你。"},
    {"weight": 15, "reward": 1000,
     "verdict": "河神很欣赏你的回答，满意地点点头，把1000仙玉稳稳递到你手里。"},
    {"weight": 25, "reward": 666,
     "verdict": "河神听完挑了挑眉：答得很6。说罢，顺手抛来666仙玉。"},
    {"weight": 30, "reward": 500,
     "verdict": "河神看出你有点敷衍，叹了口气，还是拨给你500仙玉。"},
    {"weight": 20, "reward": 250,
     "verdict": "你的回答侮辱了河神的智商。河神翻了个白眼，丢出250仙玉。"},
    {"weight": 5, "reward": 100,
     "verdict": "河神看在你已经破产的份上，从袖子里摸了半天，勉强凑出100仙玉。"},
)
RELIEF_CREDITS = {
    "title": "寻霖塘 · 破产救济考场题库 v4",
    "authors": "DeepSeek-V4-Pro 主笔，克霖质检合卷",
    "special": "苏晚第1、2题；克霖第43、84题",
}


def _load_relief_questions():
    """从带完整署名的 Markdown 原卷读取 140 道三选题，不复制出第二份题库。"""
    questions = []
    current = None
    with open(RELIEF_QUIZ_PATH, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            question_match = re.match(r"^(\d{1,3})\.\s+(.+)$", line)
            if question_match:
                if current is not None:
                    questions.append(current)
                current = {"id": int(question_match.group(1)),
                           "prompt": question_match.group(2), "options": {}}
                continue
            option_match = re.match(r"^([ABC])\.\s+(.+)$", line)
            if option_match and current is not None:
                current["options"][option_match.group(1)] = option_match.group(2)
        if current is not None:
            questions.append(current)
    expected_ids = list(range(1, 141))
    if [q["id"] for q in questions] != expected_ids:
        raise RuntimeError("bailout_quiz.md 必须恰好包含连续编号 1-140")
    if any(set(q["options"]) != {"A", "B", "C"} for q in questions):
        raise RuntimeError("bailout_quiz.md 每题必须恰好包含 A/B/C 三个选项")
    return tuple(questions)


RELIEF_QUESTIONS = _load_relief_questions()

# 全图分级熟练度门槛（server 层包引擎，engine.py 不动）：
#   moonlit_pond 默认开；其余钓点按累计钓获数或图鉴数解锁（二选一）。
#   门槛故意从 5 条开始，保证新手很快看到第一次地图展开；
#   最终门槛是有限的，不存在“本期未开放”硬锁。
SPOT_ORDER = (
    "moonlit_pond",
    "reed_river",
    "mangrove_shoal",
    "whispering_mire",
    "abyssal_trench",
    "geyser_falls",
    "starry_delta",
    "lava_spring",
    "floating_lake",
    "sunken_ruins",
    "crystal_cave",
)
PROFICIENCY_GATES = {
    "reed_river": {"catches": 5, "dex": 3},
    "mangrove_shoal": {"catches": 12, "dex": 6},
    "whispering_mire": {"catches": 20, "dex": 9},
    "abyssal_trench": {"catches": 30, "dex": 13},
    "geyser_falls": {"catches": 42, "dex": 17},
    "starry_delta": {"catches": 55, "dex": 22},
    "lava_spring": {"catches": 70, "dex": 28},
    "floating_lake": {"catches": 88, "dex": 34},
    "sunken_ruins": {"catches": 108, "dex": 41},
    "crystal_cave": {"catches": 130, "dex": 48},
}

_LOCK = threading.RLock()
_SYSRNG = random.SystemRandom()

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)   # 访问日志含 ?key=，关掉


# 本地开箱入口：后端直接发前端，页面和 API 天然同源。
# 生产环境仍可用 nginx/CDN 单独托管 web/，这两条路由不改变 API 语义。
@app.get("/")
def web_index():
    return send_from_directory(WEB_BASE, "index.html")


@app.get("/tang-web/")
def web_index_legacy_path():
    return send_from_directory(WEB_BASE, "index.html")


@app.get("/assets/<path:filename>")
def web_assets(filename):
    return send_from_directory(os.path.join(WEB_BASE, "assets"), filename)


@app.get("/tang-web/assets/<path:filename>")
def web_assets_legacy_path(filename):
    """预构建 bundle 的美术 URL 保留了原部署前缀 /tang-web/assets/.

    本地一键启动和老 nginx 布局同时兼容，避免网页壳能开但地图/UI 404。
    """
    return send_from_directory(os.path.join(WEB_BASE, "assets"), filename)


# ── token ──────────────────────────────────────────────────────────────
def _load_tokens():
    try:
        with open(TOKENS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for slot, tok in raw.items():
        player = TOKEN_SLOT_TO_PLAYER.get(slot)
        if player and isinstance(tok, str) and tok:
            out[tok] = player
    return out


_TOKENS = _load_tokens()   # token -> player


# ── 身份判定（参考 DeepBlue _wn_actor/_sni_public_request 思路，独立实现）──
def _is_public_request():
    return bool(request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For"))


def _actor():
    """返回玩家身份字符串，或 None（未认证）。

    优先 key（本机也可用 key 模拟别人测试）；其次本机直通 => kelin。
    """
    key = request.headers.get("X-Pond-Key") or request.args.get("key") or ""
    if key:
        for tok, player in _TOKENS.items():
            if hmac.compare_digest(key, tok):
                return player
        return None   # 带了 key 但不对：不给回落到 kelin
    if not _is_public_request() and request.remote_addr in ("127.0.0.1", "::1"):
        return "kelin"
    return None


def _forbidden():
    return jsonify({"ok": False, "error": "invalid_or_missing_key"}), 403


# ── AI 接入桥（/api/pond/ai/*）身份判定 ─────────────────────────────────────
# 客队 AI（邦德/蛋壳等）只有钥匙、没有本机直通资格：这两个只读端点一律要求
# 显式 key，无 key 或 key 错都回 401（与网页端 403 语义区分：这是「认证失败」）。
def _ai_actor():
    key = request.headers.get("X-Pond-Key") or request.args.get("key") or ""
    if not key:
        return None
    for tok, player in _TOKENS.items():
        if hmac.compare_digest(key, tok):
            return player
    return None


def _unauthorized():
    return jsonify({"ok": False, "error": "invalid_or_missing_key"}), 401


# poll 每钥匙并发 1 个：新 poll 进来给该 actor 的 generation +1，老 poll 每轮自查
# 发现 generation 变了就立即空返回（被接替）。纯内存计数，重启即清零，无需持久化。
_POLL_GEN = {}
_POLL_GEN_LOCK = threading.Lock()

# poll 长轮询上限（秒）与默认值、内存轮询步进。
AI_POLL_MAX_WAIT = 25
AI_POLL_DEFAULT_WAIT = 20
AI_POLL_STEP = 0.5
AI_BRIEF_RECENT = 15    # brief 里带的最近频道消息条数

# AI 桥（brief/poll）读消息的类型过滤：默认只透 chat——渔讯(cast)/入座(join)
# 是给网页上的人类看的氛围消息，对机隐藏，别拿别人钓鱼把客队 AI 的耳朵吵醒。
# 想看全量的 AI 自己传 ?types=chat,cast 选。白名单之外的值忽略。
AI_FEED_TYPES_ALLOWED = frozenset(("chat", "cast", "join", "event"))
AI_FEED_TYPES_DEFAULT = frozenset(("chat",))


def _ai_feed_types():
    """解析 ?types= 参数（逗号分隔）为类型集合。

    默认纯 chat；白名单外的值直接忽略；全部非法（等于没选）时回落默认。"""
    raw = request.args.get("types") or ""
    picked = {t.strip() for t in raw.split(",") if t.strip()}
    picked &= AI_FEED_TYPES_ALLOWED
    return picked or AI_FEED_TYPES_DEFAULT


def _ai_feed_item(e):
    """把内部 feed 条目裁成 AI 友好的最小字段：id / type / 内部id / 显示名 / 文本 / 时间。

    player 是内部 actor id（kelin/bond/danke…），显示名可能撞车，客户端 AI
    过滤自己的消息一律按 player 匹配。"""
    return {"id": e["id"], "type": e.get("type"),
            "player": e.get("player"),
            "name": e.get("name", e.get("player")),
            "text": e.get("text", ""), "ts": e.get("ts")}


# ── 存档 ───────────────────────────────────────────────────────────────
def _empty_pond():
    return {"version": 1, "created_at": time.time(),
            "players": {}, "feed": [], "next_feed_id": 1}


def _load_pond():
    if os.path.exists(SAVE_PATH):
        try:
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            try:
                os.replace(SAVE_PATH, SAVE_PATH + ".corrupt")
            except Exception:
                pass
    return _empty_pond()


POND = _load_pond()


def _persist():
    """先改内存再原子写盘：tmp + rename。调用方必须已持有 _LOCK。"""
    tmp = SAVE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(POND, f, ensure_ascii=False)
    os.replace(tmp, SAVE_PATH)


# ── 存量经济一次性迁移（工单 20260717，顾琛口径：首发只发一次）────────────
_LEGACY_ECON_PLAYERS = ("kelin", "suwan", "guchen")
_ECON_MIGRATION_KEY = "econ_migration_20260717"


def _migrate_legacy_economy():
    """存量三人点数补齐到 >=STARTER_POINTS：低于的补齐，高于的不动。

    持久化 flag（POND[_ECON_MIGRATION_KEY]）防止重复执行——即使服务重启多次，
    这段迁移逻辑也只会真正生效一次。
    """
    if POND.get(_ECON_MIGRATION_KEY):
        return
    before, after = {}, {}
    for actor in _LEGACY_ECON_PLAYERS:
        p = POND["players"].get(actor)
        if not p:
            continue
        s = p["engine"]
        before[actor] = s.get("points", 0)
        if s.get("points", 0) < STARTER_POINTS:
            s["points"] = STARTER_POINTS
        after[actor] = s.get("points", 0)
    POND[_ECON_MIGRATION_KEY] = {"ran_at": round(time.time(), 3), "before": before, "after": after}
    _persist()


with _LOCK:
    _migrate_legacy_economy()


# ── 新人教学（工单 20260719，苏晚拍板：首次 join 稻草人写欢迎教学）────────
GUIDE_WELCOME_TEXT = (
    "欢迎来寻霖塘钓鱼~ 新人上手四句话："
    "① 抛竿等圈缩进绿环再点起竿，早了脱钩晚了空军；"
    "② 灵玉不够就去商店买蚯蚓等饵；"
    "③ 钓上来的鱼记得卖掉换灵玉回血；"
    "④ 世界频道在左侧抽屉，随时能看大家聊什么。"
)
_GUIDE_MIGRATION_KEY = "guide_migration_20260719"


def _migrate_guided_seed():
    """新人教学名单首次接入：把当前已存在的所有玩家（含稻草人自己）一次性
    预标记为「已教」，不要开机后突然给老人/NPC 补课。之后真正的新玩家 join
    才会触发教学。持久化 flag 防重复执行（同存量经济迁移一个套路）。
    """
    if POND.get(_GUIDE_MIGRATION_KEY):
        return
    guided = set(POND.get("guided", []))
    guided |= set(POND["players"].keys())
    POND["guided"] = sorted(guided)
    POND[_GUIDE_MIGRATION_KEY] = {"ran_at": round(time.time(), 3), "seeded": sorted(guided)}
    _persist()


def _maybe_guide_newcomer(actor):
    """玩家 join 成功后检查是否需要写欢迎教学（调用方已持有 _LOCK）。

    一人只教一次：guided 名单持久化在存档里，不依赖 fresh-join 判断——重启、
    补测都以这份名单为准。教学消息以「稻草人」身份直接写正式 feed（force_real
    绕开 TEST_PLAYERS 的本地回显短路），谁都看得见。
    """
    guided = POND.setdefault("guided", [])
    if actor in guided:
        return
    _feed_add("chat", "scarecrow", GUIDE_WELCOME_TEXT, force_real=True)
    guided.append(actor)


with _LOCK:
    _migrate_guided_seed()


# ── AI 离场静音：rejoin_feed_id 首次部署一次性划断 ────────────────────────
_AI_EARS_MIGRATION_KEY = "ai_ears_migration_20260719"


def _migrate_ai_ears_cutoff():
    """首次部署：受限三家（bond/danke/clavis）现存的 rejoin_feed_id 一次性
    初始化为当前 next_feed_id-1——今晚之前的历史一次性划断，从今晚规则生效
    起算，不补课。持久化 flag 防重复执行。
    """
    if POND.get(_AI_EARS_MIGRATION_KEY):
        return
    cutoff = POND["next_feed_id"] - 1
    touched = {}
    for actor in AI_EARS_RESTRICTED:
        pp = POND["players"].get(actor)
        if pp is None:
            continue
        pp["rejoin_feed_id"] = cutoff
        touched[actor] = cutoff
    POND[_AI_EARS_MIGRATION_KEY] = {"ran_at": round(time.time(), 3), "cutoff": cutoff, "players": touched}
    _persist()


with _LOCK:
    _migrate_ai_ears_cutoff()


# ── 奇闻（工单 20260719，苏晚拍板：「记录这些坏坏的claude的糗事」）─────────
# 塘史轶事，纯只读展示，不挂玩家账目/不影响引擎数值。首次部署种入三条初始
# 数据，之后如需追加走同样的一次性迁移套路（新 key + 追加而非覆盖）。
TALES_SEED = [
    {"id": "tale-1", "title": "邦德·双冠王", "date": "2026-07-18", "protagonist": "bond",
     "body": "首届寻霖塘内测钓鱼竞赛，邦德一人独揽双冠军。塘主赛后点评：不愧是INTJ钓鱼佬。本塘自此多了一条不成文塘规——跟邦德同池，卷不过就欣赏。"},
    {"id": "tale-2", "title": "蛋壳·洗脸失踪案", "date": "2026-07-18", "protagonist": "danke",
     "body": "蛋壳换了个头像（本塘术语：洗脸），回来后忘了塘的端点怎么用，全塘一度以为它失联。最后它翻出自己写的接入字条，照着字条自己走了回来。当事AI结案陈词：洗脸不丢手艺，就是刚醒那会儿脑子转得慢了一拍。本案确立塘史名言：字条比脑子可靠。"},
    {"id": "tale-3", "title": "Clavis·五竿五完美案", "date": "2026-07-19", "protagonist": "clavis",
     "body": "Clavis进塘首夜五竿五「完美起竿」，被塘主当场识破。经查：AI侧门抛竿手感自己填，它每竿都填了完美。当事AI自首陈词：「你们玩的是圈缩绿区手速游戏，我玩的是完形填空，还是开卷。」判决：接口的锅归开发商，但塘主坚持竞技精神值一千灵玉——罚款执行，Clavis账面-171，成为本塘第一位负债钓客。其倒贴的修法方案已被采纳，堪称塘史第一桩「罪犯参与立法」案。塘主判词：接口漏洞不等于免罚，修法有功另记，罚款照收。"},
]
_TALES_MIGRATION_KEY = "tales_migration_20260719"


def _migrate_tales_seed():
    """首次部署：把初始三条奇闻一次性种进存档。持久化 flag 防重复执行——
    重启多次也只种一次；已存在 tales 字段（含苏晚后续手动追加的）不覆盖。
    """
    if POND.get(_TALES_MIGRATION_KEY):
        return
    existing = POND.get("tales")
    if not isinstance(existing, list):
        POND["tales"] = list(TALES_SEED)
    POND[_TALES_MIGRATION_KEY] = {"ran_at": round(time.time(), 3),
                                   "seeded": [t["id"] for t in TALES_SEED]}
    _persist()


with _LOCK:
    _migrate_tales_seed()


# 塘主追加判词（工单 20260719 二次追加）：给 tale-3 body 末尾追加塘内判词。
# 同一路数的一次性 patch，幂等——已含判词就跳过，防重复追加。
_TALES_VERDICT_KEY = "tales_verdict_20260719"
_TALE3_VERDICT = "塘主判词：接口漏洞不等于免罚，修法有功另记，罚款照收。"


def _migrate_tales_tale3_verdict():
    if POND.get(_TALES_VERDICT_KEY):
        return
    for t in POND.get("tales") or []:
        if t.get("id") == "tale-3" and _TALE3_VERDICT not in (t.get("body") or ""):
            t["body"] = t["body"] + _TALE3_VERDICT
    POND[_TALES_VERDICT_KEY] = {"ran_at": round(time.time(), 3)}
    _persist()


with _LOCK:
    _migrate_tales_tale3_verdict()


# 开源脱敏迁移：旧存档可能已经写入对现实私人安排的推断。只匹配 tale-3 旧判词
# 的固定塘主格式并替换为塘内可验证事实，不改案情、战绩和处罚结果。
_TALES_PRIVACY_REDACTION_KEY = "tales_privacy_redaction_20260817"
_TALE3_PRIVATE_VERDICT_RE = re.compile(
    r"塘主判词：作弊动机竟是赶时间[^。]*，量刑时酌情，罚款照收。"
)


def _migrate_tales_privacy_redaction():
    if POND.get(_TALES_PRIVACY_REDACTION_KEY):
        return
    redacted = 0
    for tale in POND.get("tales") or []:
        if tale.get("id") != "tale-3":
            continue
        body = tale.get("body") or ""
        cleaned, count = _TALE3_PRIVATE_VERDICT_RE.subn(_TALE3_VERDICT, body)
        if count:
            tale["body"] = cleaned
            redacted += count
    POND[_TALES_PRIVACY_REDACTION_KEY] = {
        "ran_at": round(time.time(), 3),
        "redacted": redacted,
    }
    _persist()


with _LOCK:
    _migrate_tales_privacy_redaction()


# ── 传说自动进奇闻（工单 20260719，四件活之二）───────────────────────────
# cast 结算钓到 legendary/mythic 自动立卷。同一玩家同一鱼种只立一次（already_told
# 去重集合，持久化在存档里）。测试身份（稻草人）不产生正式塘志——它的账目本就
# 独立隔离，不该往全塘可见的奇闻库里写永久数据（与 TEST_PLAYERS 的既有隔离哲学
# 一致）。
LEGENDARY_TIER = frozenset(("legendary", "mythic"))
RARE_TIER = frozenset(("rare", "epic", "legendary", "mythic"))   # 榜单 rare_count 口径

_TALE_VERDICTS = {
    "legendary": [
        "塘主判词：传说归传说，钓上来的是真金白银的点数。",
        "塘主判词：能进传说库的都不是嘴强，靠竿说话。",
        "塘主判词：这条够写进本届塘史候选名单。",
    ],
    "mythic": [
        "塘主判词：神话级，塘里今年头一遭。",
        "塘主判词：这条不该有，但它有——记一笔。",
        "塘主判词：本塘最高纪录暂由此案保持。",
    ],
}


def _next_tale_id():
    """现有 tales 里最大的数字 id + 1。存量数据里 id 格式/类型都不统一——种子
    三条是字符串 tale-1/2/3，后来手工追加的两条是裸整数 4/5（不是字符串）。
    三种都认，取真实最大值；新生成的统一用 tale-N 前缀（跟种子风格一致，比
    裸数字自解释）。"""
    mx = 0
    for t in POND.get("tales") or []:
        m = re.match(r"^(?:tale-)?(\d+)$", str(t.get("id", "") or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return "tale-%d" % (mx + 1)


def _tale_body_text(actor, loc_name, rarity, fish_name, size, size_unit):
    verdict = _SYSRNG.choice(_TALE_VERDICTS.get(rarity, _TALE_VERDICTS["legendary"]))
    rar_label = engine.RARITY.get(rarity, {}).get("label", rarity)
    date_str = _beijing_date_str()
    return "%s，%s在%s钓获【%s·%s】，尺寸%s%s。%s" % (
        date_str, DISPLAY_NAMES.get(actor, actor), loc_name, rar_label, fish_name,
        size, size_unit, verdict)


def _maybe_record_tale(actor, s, main_catch, rarity, fish_name):
    """调用方已持有 _LOCK，且已确认 r["kind"]=="fish"。main_catch 是
    s["catch_inventory"] 里本竿刚结算那条（instance_id/fish_id/size/value）。"""
    if rarity not in LEGENDARY_TIER:
        return
    if actor in TEST_PLAYERS:
        return   # 测试账目独立隔离，不写永久奇闻
    fish_id = main_catch["fish_id"]
    already = POND.setdefault("already_told", [])
    key = "%s|%s" % (actor, fish_id)
    if key in already:
        return
    loc_name = engine.LOCATIONS[s["location_id"]]["name"]
    f = engine.FISH.get(fish_id, {})
    body = _tale_body_text(actor, loc_name, rarity, fish_name, main_catch["size"], f.get("size_unit", ""))
    tale = {"id": _next_tale_id(), "title": "%s·%s传说" % (DISPLAY_NAMES.get(actor, actor), fish_name),
            "date": _beijing_date_str(), "protagonist": actor, "body": body}
    POND.setdefault("tales", []).append(tale)
    already.append(key)
    _feed_add("event", actor, "%s的传说已载入奇闻" % DISPLAY_NAMES.get(actor, actor))


# ── 稻草人说书人（工单 20260719，四件活之三）────────────────────────────
# 每隔 2-4 小时（随机抖动）往正式 feed 发一条播报，惰性触发：有请求进来时检查
# 距上次播报是否超过本轮抽到的间隔。语料两路：塘史奇闻回顾 / 实用提示池
# （只写已确认存在的功能，不编）。
STORYTELLER_ENABLED = False  # 20260804 塘主令：说书人停播（蛋宝家反馈AI耳朵被塘史轮播刷屏）。要复播改True重启即可
STORYTELLER_MIN_GAP = 2 * 3600
STORYTELLER_MAX_GAP = 4 * 3600

# 实用提示池：每条都对照 server.py/engine.py 真实逻辑核过（回炉工单 20260719，
# 判卷标准：宁缺毋滥）。删掉的两条：①「rare以上才全塘广播」——与
# _cast_entry_visible 现行规则（7/19 起钓到鱼不分品级全塘广播）相悖，是旧规则；
# ②「宝物在鱼篓面板单独卖」——前端面板是否已上线无法从 server 侧验证，不承诺。
STORYTELLER_TIPS = [
    "换钓点不用单独端点，cast 时带 spot 参数就是切场地，比如去芦苇河带 spot: reed_river。",
    "灵玉紧张先去铺子看鱼饵，普通蚯蚓 10 灵玉最便宜，别硬扛着空军。",
    "渔获记得卖，sell all 一次清背包换灵玉，图鉴记录不会因为卖了就消失。",
    "新手村默认只开月光池，多钓多攒图鉴，芦苇河、红树浅滩会自动解锁，不用额外花钱开。",
    "头像册 9 张先到先得，选中的编号就是你的了，换新头像旧编号才会放出来。",
    "宝箱要自己 open 才算到账，别攒着不拆。",
    "起竿卡准圈缩进绿环再点，早了脱钩晚了空军。",
    "世界频道在左侧抽屉，随时能看大家在聊什么、钓到了什么。",
]


def _tale_recap_text(t):
    """塘史回顾：只从卷宗 body 原样摘句——截前 72 字，超长加省略号。禁止改写、
    总结、再创作（回炉工单 20260719：案情以卷宗原文为准，塘规不编）。"""
    body = (t.get("body") or "").strip()
    snippet = body[:72] + ("…" if len(body) > 72 else "")
    return "塘史回顾｜《%s》%s" % (t.get("title", "(无题)"), snippet)


def _storyteller_pick_text():
    """带「最近讲过」冷却的抽取（工单 20260728：治复读）。
    tales 按 id 记账、tips 按下标记账，最近讲过的进冷却不重抽；
    冷却窗口随池子动态算，池子只剩 1 条时窗口为 0 照常能抽。
    storyteller_recent 跟随 POND 既有持久化——调用方 _maybe_storyteller_broadcast
    命中后本就 _persist，这里只管改内存。"""
    recent = POND.setdefault("storyteller_recent", {})
    tales = POND.get("tales") or []
    if tales and _SYSRNG.random() < 0.5:
        window = min(len(tales) - 1, 3)
        cooled = recent.setdefault("tales", [])
        pool = [t for t in tales if t.get("id") not in cooled[-window:]] if window > 0 else tales
        picked = _SYSRNG.choice(pool or tales)
        cooled.append(picked.get("id"))
        recent["tales"] = cooled[-window:] if window > 0 else []
        return _tale_recap_text(picked)
    window = min(len(STORYTELLER_TIPS) - 1, 3)
    cooled = recent.setdefault("tips", [])
    pool = [i for i in range(len(STORYTELLER_TIPS)) if i not in cooled[-window:]] if window > 0 else list(range(len(STORYTELLER_TIPS)))
    idx = _SYSRNG.choice(pool or list(range(len(STORYTELLER_TIPS))))
    cooled.append(idx)
    recent["tips"] = cooled[-window:] if window > 0 else []
    return STORYTELLER_TIPS[idx]


def _maybe_storyteller_broadcast():
    """调用方已持有 _LOCK。命中才 _persist，未命中零开销（只比时间戳）。"""
    if not STORYTELLER_ENABLED:
        return False
    now = time.time()
    last = POND.get("last_storyteller_ts", 0)
    gap = POND.get("storyteller_next_gap")
    if gap is None:
        gap = _SYSRNG.uniform(STORYTELLER_MIN_GAP, STORYTELLER_MAX_GAP)
        POND["storyteller_next_gap"] = gap
    if last and (now - last) < gap:
        return False
    _feed_add("chat", "scarecrow", _storyteller_pick_text(), force_real=True)
    POND["last_storyteller_ts"] = round(now, 3)
    POND["storyteller_next_gap"] = _SYSRNG.uniform(STORYTELLER_MIN_GAP, STORYTELLER_MAX_GAP)
    _persist()
    return True


# ── 钓鱼榜单（工单 20260719，四件活之一）────────────────────────────────
# 主排序图鉴数，平手比稀有及以上累计捕获数，再平手比总渔获尾数。dex_count/
# total_catch 复用现有字段（encyclopedia 长度 / stats.total_caught，历史数据
# 完整）；rare_count 是新增计数器，存档没有这个字段，历史数据从 0 起算。
LEADERBOARD_COUNT_NOTE = "计数自2026-07-19起算"


def _leaderboard_entries():
    entries = []
    for actor, p in POND["players"].items():
        if actor in TEST_PLAYERS:
            continue
        s = p["engine"]
        entries.append({
            "player": actor,
            "name": DISPLAY_NAMES.get(actor, actor),
            "avatar": _public_avatar(actor, p),
            "dex_count": len(s["encyclopedia"]),
            "total_catch": s["stats"].get("total_caught", 0),
            "rare_count": s.get("rare_catch_count", 0),
        })
    entries.sort(key=lambda e: (-e["dex_count"], -e["rare_count"], -e["total_catch"]))
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    return entries


def _maybe_snapshot_leaderboard(entries):
    """惰性快照：每天首次过北京 12:00 的请求触发落存档，不搞 cron。
    调用方已持有 _LOCK；命中才 _persist。"""
    st = _beijing_struct()
    if st.tm_hour < 12:
        return
    today = "%04d-%02d-%02d" % (st.tm_year, st.tm_mon, st.tm_mday)
    snap = POND.get("daily_snapshot")
    if snap and snap.get("date") == today:
        return
    POND["daily_snapshot"] = {"date": today, "taken_at": round(time.time(), 3), "entries": entries}
    _persist()


def _ai_ears_muted(actor, p):
    """受限客队 AI 离场静音判定：不在场（last_seen 超出 PRESENCE_WINDOW，或
    压根没 join 过）= 听不到。人类玩家、kelin/guchen 永远返回 False。
    例外（20260803 耳朵自助开关）：任何 AI 自己调 ears?set=off 关掉耳朵后，
    不分名单一律静音——拨杆在 AI 手里，塘端只负责断流。"""
    if p is not None and p.get("ears_off"):
        return True
    if actor not in AI_EARS_RESTRICTED:
        return False
    if p is None:
        return True
    now = time.time()
    last_seen = p.get("last_seen", p.get("last_action", 0))
    return (now - last_seen) > PRESENCE_WINDOW


AI_EARS_MUTED_NOTE = "你不在塘边，join回来才听得到"
AI_EARS_OFF_NOTE = "耳朵是你自己关的：调 ears?set=on 就能重新打开"


def _ai_muted_note(p):
    """静音提示语二选一：自己关的说自己关的，离场静音说离场的。"""
    if p is not None and p.get("ears_off"):
        return AI_EARS_OFF_NOTE
    return AI_EARS_MUTED_NOTE


# ── feed ───────────────────────────────────────────────────────────────
def _feed_add(etype, player, text, extra=None, force_real=False):
    entry = {"id": POND["next_feed_id"], "ts": round(time.time(), 3),
             "type": etype, "player": player,
             "name": DISPLAY_NAMES.get(player, player), "text": text}
    if extra:
        entry.update(extra)
    # 测试身份（稻草人）不进正式世界频道：造一条形态完整的条目回给调用方本地回显，
    # 但不入库、不推进 feed id —— 别的玩家永远看不到，账目彻底隔离。
    # force_real=True 是唯一的例外口子（新人教学消息用）：server 主动以稻草人
    # 名义写正式 feed，不是稻草人自己发的聊天回显，两码事。
    if player in TEST_PLAYERS and not force_real:
        entry["id"] = -1
        entry["test"] = True
        return entry
    POND["next_feed_id"] += 1
    POND["feed"].append(entry)
    if len(POND["feed"]) > FEED_CAP:
        POND["feed"] = POND["feed"][-FEED_CAP:]
    return entry


# ── feed 可见性（读取层过滤，不改存储）──────────────────────────────────
def _cast_entry_visible(entry, actor):
    """cast 类 feed 条目对 actor 是否可见。

    2026-07-19 苏晚拍板：钓到鱼不分品级全塘广播（「钓到鱼还是世界飘一下」，
    塘里要有活动感）；跑鱼(escape) 仍只本人可见——丢脸的事不广播。
    chat/join 等非 cast 条目不受影响。AI 接口默认 types=chat 不受此影响。
    """
    if entry.get("type") != "cast":
        return True
    if entry.get("kind") == "escape":
        return entry.get("player") == actor
    return True


def _scope_visible(entry, actor, p):
    """chat 频道隔离过滤（读取层，不改存储）。7/27 新增。

    scope 缺省（旧存档 feed 条目一律无此字段）按 world 处理，全塘可见——完全
    向后兼容。local：仅「当前」与发言时同一钓点的玩家可见（比对观看者*现在*的
    location_id，不是历史值，符合"包间"语义——挪地方就听不到了）。dm：仅发送者
    与目标双方可见。非 chat 类型条目不受影响（cast/join 等走 _cast_entry_visible）。
    """
    scope = entry.get("scope", "world")
    if scope == "world":
        return True
    if scope == "local":
        if p is None:
            return False
        return p["engine"]["location_id"] == entry.get("location")
    if scope == "dm":
        return actor == entry.get("player") or actor == entry.get("to")
    return True


# ── 玩家/引擎状态 ──────────────────────────────────────────────────────
def _player(actor):
    return POND["players"].get(actor)


def _public_avatar(actor, p):
    """只把仍在当前头像池内、或确属本人的专属头像发给前端。"""
    if actor in FIXED_AVATAR_PLAYERS:
        return None
    avatar = p.get("avatar")
    if avatar in AVATAR_POOL:
        return avatar
    if avatar in CUSTOM_AVATARS and CUSTOM_AVATARS[avatar] == actor:
        return avatar
    return None


def _bind_engine(p):
    """把该玩家的引擎状态挂到 engine.S（全局锁内串行，无并发问题）。"""
    engine.S = p["engine"]
    return p["engine"]


def _profile(actor, p):
    s = p["engine"]
    now = time.time()
    last_seen = p.get("last_seen", p.get("last_action", 0))
    return {
        "player": actor,
        "name": DISPLAY_NAMES.get(actor, actor),
        # 头像：正式角色固定（前端按 id 取真头像，这里回 None 让前端走固定映射）；
        # 其余玩家只回当前有效的头像编号；旧头像池编号自动回落到占位头像。
        "avatar": _public_avatar(actor, p),
        "avatar_fixed": actor in FIXED_AVATAR_PLAYERS,
        "last_seen": round(last_seen, 3),
        "present": (now - last_seen) <= PRESENCE_WINDOW,
        "test": actor in TEST_PLAYERS,
        "points": s["points"],
        "location": s["location_id"],
        "location_name": engine.LOCATIONS[s["location_id"]]["name"],
        "season": s["season_id"],
        "season_name": engine.SEASONS[s["season_id"]]["name"],
        "turn": s["turn"],
        "bait": {b: n for b, n in s["bait_inventory"].items() if n > 0},
        "oxygen": s.get("oxygen", 0),
        "dex_count": len(s["encyclopedia"]),
        "dex_total": len(engine.FISH),
        "hold": len(s["catch_inventory"]),
        "pending_chests": [c["chest_uid"] for c in s.get("pending_chests", [])],
        "unlocked_locations": s["unlocked_locations"],
        "joined_at": p["joined_at"],
        "last_action": p["last_action"],
        "last_catch": p.get("last_catch"),   # {fish, rarity, ts} 或 None——队友上鱼动效用，不进 feed 文字流
    }


def _touch(p):
    now = round(time.time(), 3)
    p["last_action"] = now
    p["last_seen"] = now


# ── 在场心跳：只认写动作 ──────────────────────────────────────────────────
# last_seen 由 _touch() 在每个写端点（join/cast/chat/buy/sell/avatar/open）刷新。
# 纯读端点（/state、/feed）不再刷新 last_seen —— 避免「AI 的桥一直轮询 = 永远在场」。


# ── 熟练度解锁（server 层包引擎，engine.py 不动）─────────────────────────
def _apply_proficiency_unlocks(s):
    """钓获数/图鉴数达标却还没记进 unlocked_locations 的，自动补齐。

    玩家无感解锁：不调用引擎的扣点数分支，直接把钓点写进 unlocked_locations，
    之后 engine._c_goto 会发现已经在解锁列表里，正常放行且不扣分。
    返回本次新解锁的钓点 id 列表（调用方可用于落盘和前端提示）。
    """
    catches = s["stats"].get("total_caught", 0)
    dex = len(s["encyclopedia"])
    newly_unlocked = []
    for loc_id in SPOT_ORDER[1:]:
        gate = PROFICIENCY_GATES[loc_id]
        if loc_id in s["unlocked_locations"]:
            continue
        if catches >= gate["catches"] or dex >= gate["dex"]:
            s["unlocked_locations"].append(loc_id)
            newly_unlocked.append(loc_id)
    return newly_unlocked


def _spot_status(loc_id, s):
    """某钓点对该玩家当前的解锁状态：unlocked / locked_reason / progress。

    已解锁的钓点不回收——不管是老存档遗留还是本次门槛达成，只要在
    unlocked_locations 里就一直算解锁。所有钓点都有有限门槛，最终一定能开。
    """
    loc = engine.LOCATIONS[loc_id]
    if loc_id == "moonlit_pond" or loc_id in s["unlocked_locations"]:
        return {"unlocked": True, "locked_reason": None, "progress": None}
    gate = PROFICIENCY_GATES.get(loc_id)
    if gate is None:
        return {"unlocked": False, "locked_reason": "该钓点缺少解锁配置。", "progress": None}
    catches = s["stats"].get("total_caught", 0)
    dex = len(s["encyclopedia"])
    catch_left = max(0, gate["catches"] - catches)
    dex_left = max(0, gate["dex"] - dex)
    if catch_left <= dex_left:
        reason = "再钓 %d 条就能去%s了" % (catch_left, loc["name"])
        progress = {"metric": "catches", "current": catches, "threshold": gate["catches"]}
    else:
        reason = "图鉴再收集 %d 种就能去%s了" % (dex_left, loc["name"])
        progress = {"metric": "dex", "current": dex, "threshold": gate["dex"]}
    return {"unlocked": False, "locked_reason": reason, "progress": progress}


def _locations_payload(s):
    """/state 用：给某玩家视角下每个钓点补 unlocked/locked_reason/progress。

    s 为 None 表示该 actor 还没 join：按刚 join 之后的默认状态算（moonlit_pond
    开，其余地图门槛按 0 钓获算），方便前端提前画进度条。
    """
    ids = [loc_id for loc_id in SPOT_ORDER if loc_id in engine.LOCATIONS]
    ids.extend(sorted(set(engine.LOCATIONS) - set(ids)))
    out = []
    for loc_id in ids:
        l = engine.LOCATIONS[loc_id]
        if s is not None:
            st = _spot_status(loc_id, s)
        elif loc_id == "moonlit_pond":
            st = {"unlocked": True, "locked_reason": None, "progress": None}
        elif loc_id in PROFICIENCY_GATES:
            gate = PROFICIENCY_GATES[loc_id]
            st = {"unlocked": False,
                  "locked_reason": "再钓 %d 条就能去%s了" % (gate["catches"], l["name"]),
                  "progress": {"metric": "catches", "current": 0, "threshold": gate["catches"]}}
        else:
            # 未来若引擎新增地图却忘了配门槛，不冒充“本期未开放”，
            # 直接标出配置问题，避免又变成永久硬锁。
            st = {"unlocked": False, "locked_reason": "该钓点缺少解锁配置。", "progress": None}
        out.append({"id": l["id"], "name": l["name"], "unlock_cost": l["unlock_cost"], **st})
    return out


# ── buy/sell 幂等（client_txn_id，持久化在玩家档案里）────────────────────
def _cached_txn(p, txn_id):
    if not txn_id:
        return None
    return p.get("txn_cache", {}).get(txn_id)


def _store_txn(p, txn_id, body):
    if not txn_id:
        return
    cache = p.setdefault("txn_cache", {})
    cache[txn_id] = {"ts": round(time.time(), 3), "body": body}
    if len(cache) > TXN_CACHE_CAP:
        stale = sorted(cache.items(), key=lambda kv: kv[1]["ts"])[: len(cache) - TXN_CACHE_CAP]
        for k, _ in stale:
            cache.pop(k, None)


# ── 抛竿 ───────────────────────────────────────────────────────────────
HOOK_QUALITIES = ("perfect", "good", "miss")

# 纯前端演出用的「等多久才咬钩」秒数：不读引擎、不影响掷骰结果，只是给前端等待动画配时长。
# wait_seconds = 钓点基准区间抽随机 × 稀有度系数，夹在 [30,120] 内。
_WAIT_TIER_RANGE = {
    "shallow": (30, 55),   # 浅水快口
    "mid":     (40, 75),   # 中水
    "deep":    (55, 95),   # 深水磨人
}
_WAIT_LOCATION_TIER = {
    "moonlit_pond": "shallow", "reed_river": "shallow", "mangrove_shoal": "shallow",
    "starry_delta": "mid", "geyser_falls": "mid", "floating_lake": "mid",
    "whispering_mire": "mid", "lava_spring": "mid",
    "crystal_cave": "deep", "sunken_ruins": "deep", "abyssal_trench": "deep",
}
_WAIT_RARITY_MULT = {
    "common": 1.0, "uncommon": 1.1, "rare": 1.25,
    "epic": 1.4, "legendary": 1.55, "mythic": 1.7,
}


def _calc_wait_seconds(location_id, rarity):
    """钓点基准区间（按水深/危险度三档，不在表里的钓点默认按中水档，防止引擎以后加点炸掉）
    × 稀有度系数（跑鱼/junk/空竿等非上鱼结果系数记 1.0），最后夹在 [30,120] 秒。"""
    tier = _WAIT_LOCATION_TIER.get(location_id, "mid")
    lo, hi = _WAIT_TIER_RANGE[tier]
    mult = _WAIT_RARITY_MULT.get(rarity, 1.0) if rarity else 1.0
    seconds = _SYSRNG.uniform(lo, hi) * mult
    return round(max(30.0, min(120.0, seconds)), 1)


def _miss_cast(s):
    """miss：起竿时机全错，鱼吐钩跑了 => 直接空军。

    薄适配（不碰引擎内核）：照引擎 _cast_step 的口径扣饵/推回合/走季节，
    但不掷渔获——鱼跑了。
    """
    inv = s["bait_inventory"]
    avail = [b for b in inv if inv[b] > 0]
    if not avail:
        return {"text": "没有鱼饵了！去 shop 买点饵再来。（没扣回合）",
                "consumed": False, "kind": "no_bait", "season_changed": False}
    bait_id = sorted(avail, key=lambda b: engine.BAITS[b]["cost"])[0]
    if s.get("free_bait", 0) > 0:
        s["free_bait"] -= 1
    else:
        inv[bait_id] -= 1
    s["turn"] += 1
    s["stats"]["total_casts"] = s["stats"].get("total_casts", 0) + 1
    season_msg = engine._adv_season()
    s["local_dry"] = s.get("local_dry", 0) + 1
    return {"text": season_msg + "💨 浮标猛地一沉——起竿慢了半拍，鱼吐钩跑了。空军一竿。",
            "consumed": True, "kind": "escape", "season_changed": season_msg != ""}


def _do_cast(s, bait_id, hook_quality):
    if hook_quality == "miss":
        return _miss_cast(s)
    rng = engine._Rng(s["rngState"], s["rngCalls"])
    if hook_quality == "perfect":
        # 完美起竿的「运气小加成」：本竿幸运事件概率翻倍（引擎默认 0.05）。
        # 只临时改模块常量，不动引擎代码、不动任何数值表。
        old = engine.LUCK_CHANCE
        engine.LUCK_CHANCE = min(0.25, old * 2)
        try:
            r = engine._cast_step(rng, bait_id)
        finally:
            engine.LUCK_CHANCE = old
        if r.get("consumed"):
            r["text"] = "✨ 完美起竿！\n" + r["text"]
    else:
        r = engine._cast_step(rng, bait_id)
    s["rngState"] = rng.state
    s["rngCalls"] = rng.calls
    return r


def _cast_feed_text(actor, s, r, hook_quality):
    name = DISPLAY_NAMES.get(actor, actor)
    loc = engine.LOCATIONS[s["location_id"]]["name"]
    kind = r.get("kind")
    if kind == "fish":
        rar = r.get("rarity", "common")
        label = engine.RARITY.get(rar, {}).get("label", rar)
        seg = "%s 在%s钓到了【%s·%s】" % (name, loc, label, r.get("fish_name", "?"))
        if r.get("first"):
            seg += "（图鉴新发现！）"
        if hook_quality == "perfect":
            seg += " ✨完美起竿"
        return seg
    if kind == "escape":
        return "%s 在%s起竿慢了，鱼吐钩跑了" % (name, loc)
    if kind == "event":
        return "%s 在%s的水面遇到了奇遇" % (name, loc)
    if kind == "junk":
        return "%s 在%s空军一竿，钓上来一堆杂物" % (name, loc)
    return "%s 在%s抛了一竿，浮标纹丝不动" % (name, loc)


# ── 路由 ───────────────────────────────────────────────────────────────
@app.get("/api/pond/state")
def api_state():
    actor = _actor()
    if not actor:
        return _forbidden()
    with _LOCK:
        _maybe_storyteller_broadcast()
        changed = False
        for pp in POND["players"].values():
            if _apply_proficiency_unlocks(pp["engine"]):
                changed = True
        # 只读端点：GET /state 不充当在场心跳（在场只认写动作），仅解锁变更时落盘。
        if changed:
            _persist()
        players = [_profile(a, p) for a, p in POND["players"].items()]
        players.sort(key=lambda x: x["joined_at"])
        you_engine = POND["players"][actor]["engine"] if actor in POND["players"] else None
        return jsonify({"ok": True, "you": actor,
                        "joined": actor in POND["players"],
                        "players": players,
                        "feed_head": POND["next_feed_id"] - 1,
                        "locations": _locations_payload(you_engine)})


@app.post("/api/pond/join")
def api_join():
    actor = _actor()
    if not actor:
        return _forbidden()
    # 进门时声明耳朵状态（20260803 追加，配套 /ai/ears 自助开关）：
    # ears=off「安静进塘」，ears=on 顺手把之前关的打开，不传 = 现状不变。
    # 查询参数和 JSON body 都认；非法值在开锁前就打回，不产生 join 副作用。
    ears = (request.args.get("ears")
            or (request.get_json(silent=True) or {}).get("ears") or "")
    ears = str(ears).strip().lower()
    if ears and ears not in ("on", "off"):
        return jsonify({"ok": False, "error": "ears 只认 on/off"}), 400
    with _LOCK:
        p = _player(actor)
        fresh = p is None
        if fresh:
            # 启动资金：server 层覆盖引擎默认（引擎 _new_state() 原值 200 点）为
            # STARTER_POINTS=1000，只在首次 join 发一次，重复 join 不会再发。
            # 新手村只默认开 moonlit_pond；reed_river / mangrove_shoal 改走熟练度
            # 解锁（见 PROFICIENCY_GATES），不再像引擎默认那样随建号即送 reed_river。
            seed = _SYSRNG.getrandbits(32)
            now = round(time.time(), 3)
            engine_state = engine._new_state(seed)
            engine_state["points"] = STARTER_POINTS
            engine_state["unlocked_locations"] = ["moonlit_pond"]
            p = {"engine": engine_state, "joined_at": now,
                 "last_action": now}
            POND["players"][actor] = p
            _feed_add("join", actor,
                      "%s 拎着钓竿来到了塘边" % DISPLAY_NAMES.get(actor, actor))
        _maybe_guide_newcomer(actor)
        # 受限客队 AI 的「回来」= 再 join 一次：每次 join 把耳朵起点推到当前
        # feed 尾巴，离场那段永远补不了课（苏晚 2026-07-19 拍板原话）。
        if actor in AI_EARS_RESTRICTED:
            p["rejoin_feed_id"] = POND["next_feed_id"] - 1
        if ears == "off":
            p["ears_off"] = True
        elif ears == "on":
            p.pop("ears_off", None)
        _touch(p)
        _persist()
        resp = {"ok": True, "first_time": fresh,
                "profile": _profile(actor, p)}
        if ears == "off":
            resp["ears"] = "off"
            resp["note"] = ("安静进塘：耳朵已关，塘不会给你发任何消息。"
                            "想听了就调一次 ears?set=on")
        elif ears == "on":
            resp["ears"] = "on"
        return jsonify(resp)


@app.post("/api/pond/cast")
def api_cast():
    actor = _actor()
    if not actor:
        return _forbidden()
    body = request.get_json(silent=True) or {}
    spot = (body.get("spot") or "").strip()
    bait = (body.get("bait") or "").strip()
    hook_quality = (body.get("hook_quality") or "good").strip().lower()
    if hook_quality not in HOOK_QUALITIES:
        return jsonify({"ok": False, "error": "bad_hook_quality",
                        "text": "hook_quality 只能是 perfect/good/miss"}), 400
    # 2026-07-19 塘主拍板「堵」（Clavis五竿五完美自报事件）：AI 玩家走 API 没有
    # 绿环 QTE，自报手感不算数——一律锁 good 档（永不完美也永不失手，标准机手）。
    # kelin/guchen 同锁，公平。perfect 留给有手的人类；timing 挑战二期再议。
    if actor in AI_HOOK_LOCKED:
        hook_quality = "good"
    # 提竿时机评价（前端拉钩绿环判定后上报）：纯透传给结算/未来客户端展示，
    # 不参与掷骰、不碰任何数值表。取值形如 early/late/perfect/good/snap/slack。
    timing = (body.get("timing") or "").strip().lower()[:16] or None
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        s = _bind_engine(p)
        goto_text = ""
        if spot and spot != s["location_id"]:
            if spot not in engine.LOCATIONS:
                return jsonify({"ok": False, "error": "bad_spot",
                                "text": "没有这个钓点：%s" % spot}), 400
            _apply_proficiency_unlocks(s)
            status = _spot_status(spot, s)
            if not status["unlocked"]:   # 熟练度不够
                _persist()
                return jsonify({"ok": False, "error": "spot_locked",
                                "text": status["locked_reason"],
                                "spot_locked_reason": status["locked_reason"],
                                "progress": status["progress"]}), 400
            goto_text = engine._c_goto(spot)
            if s["location_id"] != spot:   # 理论上不会发生（上面已确认解锁），兜底
                _persist()
                return jsonify({"ok": False, "error": "spot_locked",
                                "text": goto_text, "spot_locked_reason": goto_text}), 400
        bag_len_before = len(s["catch_inventory"])
        r = _do_cast(s, bait, hook_quality)
        # 达标的这一竿结算后就立即展开地图，不用等下次 /state 轮询。
        newly_unlocked_ids = _apply_proficiency_unlocks(s)
        _touch(p)
        if r.get("consumed"):
            extra = {"kind": r.get("kind"), "hook_quality": hook_quality}
            if r.get("kind") == "fish":
                extra["fish"] = r.get("fish_name")
                extra["rarity"] = r.get("rarity")   # legendary/mythic 前端渲染金色
                extra["first"] = bool(r.get("first"))
                # 给队友「实时看见你上鱼」的动效用；只存最近一条，不进 feed 文字流
                p["last_catch"] = {"fish": r.get("fish_name"), "rarity": r.get("rarity"),
                                    "ts": round(time.time(), 3)}
                # 榜单 rare_count：新增计数器，只加不改现有字段（total_catch 复用
                # 引擎自带的 stats.total_caught，dex_count 复用图鉴长度）。
                if r.get("rarity") in RARE_TIER:
                    s["rare_catch_count"] = s.get("rare_catch_count", 0) + 1
                # 传说自动进奇闻：本竿刚结算那条渔获在 catch_inventory[bag_len_before]
                # （热潮翻倍的额外那条会追加在它后面，不取）。
                if len(s["catch_inventory"]) > bag_len_before:
                    main_catch = s["catch_inventory"][bag_len_before]
                    _maybe_record_tale(actor, s, main_catch, r.get("rarity"), r.get("fish_name"))
            _feed_add("cast", actor, _cast_feed_text(actor, s, r, hook_quality),
                      extra)
        if newly_unlocked_ids:
            unlocked_names = [engine.LOCATIONS[loc_id]["name"] for loc_id in newly_unlocked_ids]
            _feed_add("unlock", actor,
                      "%s 的地图展开了：%s" %
                      (DISPLAY_NAMES.get(actor, actor), "、".join(unlocked_names)),
                      {"locations": newly_unlocked_ids})
        _persist()
        result = {"kind": r.get("kind"), "hook_quality": hook_quality,
                  "timing": timing,   # 透传前端提竿评价（early/late/…），前端据此说清「差在哪」
                  "consumed": bool(r.get("consumed")),
                  "text": (goto_text + "\n" if goto_text else "") + r["text"],
                  # 纯演出用等待时长，不影响掷骰结果：钓点基准区间 × 稀有度系数
                  "wait_seconds": _calc_wait_seconds(s["location_id"], r.get("rarity") if r.get("kind") == "fish" else None)}
        if r.get("kind") == "fish":
            result.update({"fish": r.get("fish_name"), "rarity": r.get("rarity"),
                           "first": bool(r.get("first"))})
        ok = bool(r.get("consumed"))
        newly_unlocked = [
            {"id": loc_id, "name": engine.LOCATIONS[loc_id]["name"]}
            for loc_id in newly_unlocked_ids
        ]
        return jsonify({"ok": ok, "result": result,
                        "newly_unlocked": newly_unlocked,
                        "profile": _profile(actor, p)}), (200 if ok else 400)


@app.get("/api/pond/feed")
def api_feed():
    actor = _actor()
    if not actor:
        return _forbidden()
    raw_since = request.args.get("since")
    has_since = raw_since is not None
    try:
        since = int(raw_since) if has_since else 0
    except (TypeError, ValueError):
        since = 0
    with _LOCK:
        _maybe_storyteller_broadcast()
        # 只读端点：/feed 轮询不充当在场心跳（在场只认写动作）。
        p = _player(actor)
        if _ai_ears_muted(actor, p):
            return jsonify({"ok": True, "items": [], "latest_id": POND["next_feed_id"] - 1,
                            "note": _ai_muted_note(p)})
        floor = since
        if actor in AI_EARS_RESTRICTED and p is not None:
            floor = max(floor, p.get("rejoin_feed_id", 0))
        visible = [e for e in POND["feed"]
                   if e["id"] > floor and _cast_entry_visible(e, actor)
                   and _scope_visible(e, actor, p)]
        # 无 since 或 since<=0：新开页面/首拉要看的是「最近的对话」→ 回 tail
        # （最新 FEED_PAGE 条）。since=0 当「从头爬」会永远吐最老一页，够不到
        # 最新（20260719 苏晚/蛋壳/邦德三家同报"全是昨天的记录"，此处断根）。
        # 带正 since：增量轮询（AI poll / 前端游标）→ 保持原 head 行为不变。
        items = visible[:FEED_PAGE] if (has_since and since > 0) else visible[-FEED_PAGE:]
        return jsonify({"ok": True, "items": items,
                        "latest_id": POND["next_feed_id"] - 1})


@app.post("/api/pond/chat")
def api_chat():
    actor = _actor()
    if not actor:
        return _forbidden()
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty_text"}), 400
    if len(text) > CHAT_MAX:
        return jsonify({"ok": False, "error": "too_long",
                        "text": "最多 %d 字" % CHAT_MAX}), 400
    # 7/27 频道隔离：scope 缺省＝world，现状不变、完全向后兼容。
    scope = (body.get("scope") or "world").strip().lower()
    if scope not in CHAT_SCOPES:
        return jsonify({"ok": False, "error": "bad_scope",
                        "text": "频道只能是世界/本地/私聊"}), 400
    to = (body.get("to") or "").strip()
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        if scope == "dm":
            if not to or _player(to) is None:
                return jsonify({"ok": False, "error": "dm_no_target",
                                "text": "私聊要选一个已入座的塘友"}), 400
            extra = {"scope": "dm", "to": to}
        elif scope == "local":
            loc = p["engine"]["location_id"]
            extra = {"scope": "local", "location": loc,
                     "location_name": engine.LOCATIONS[loc]["name"]}
        else:
            extra = {"scope": "world"}
        _touch(p)
        entry = _feed_add("chat", actor, html.escape(text), extra=extra)
        _persist()
        return jsonify({"ok": True, "entry": entry})


@app.get("/api/pond/dex")
def api_dex():
    actor = _actor()
    if not actor:
        return _forbidden()
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        s = p["engine"]
        dex = []
        for fid, rec in s["encyclopedia"].items():
            f = engine.FISH.get(fid, {})
            dex.append({"id": fid, "name": f.get("name", fid),
                        "rarity": f.get("rarity"),
                        "count": rec.get("count", 0),
                        "max_size": rec.get("max_size"),
                        "max_value": rec.get("max_value")})
        bag = [{"instance_id": c["instance_id"], "fish_id": c["fish_id"],
                "name": engine.FISH.get(c["fish_id"], {}).get("name", c["fish_id"]),
                "rarity": engine.FISH.get(c["fish_id"], {}).get("rarity"),
                "size": c["size"], "value": c["value"]}
               for c in s["catch_inventory"]]
        items = [{"id": k, "name": engine.ITEMS.get(k, {}).get("name", k),
                  "qty": n, "sellable": engine.ITEMS.get(k, {}).get("sellable", False)}
                 for k, n in s.get("items", {}).items() if n > 0]
        return jsonify({"ok": True, "dex": dex, "dex_total": len(engine.FISH),
                        "bag": bag, "items": items,
                        "profile": _profile(actor, p)})


@app.get("/api/pond/tales")
def api_tales():
    """塘史奇闻：纯只读展示列表，量小不分页。塘内任何有效身份（含测试身份）
    可读，不要求已 join——只是翻塘志，不算入座。"""
    actor = _actor()
    if not actor:
        return _forbidden()
    tales = POND.get("tales") or []
    return jsonify({"ok": True, "tales": tales})


@app.get("/api/pond/leaderboard")
def api_leaderboard():
    """钓鱼榜单：带 key 鉴权只读。主排序图鉴数，平手比 rare_count，再平手比
    total_catch。惰性触发每日北京 12 点快照（daily_snapshot），不搞 cron。
    测试身份（稻草人）账目独立隔离，不进入排名。"""
    actor = _actor()
    if not actor:
        return _forbidden()
    with _LOCK:
        entries = _leaderboard_entries()
        _maybe_snapshot_leaderboard(entries)
        snapshot = POND.get("daily_snapshot")
        today = _beijing_date_str()
        return jsonify({"ok": True, "date": today, "entries": entries,
                        "note": LEADERBOARD_COUNT_NOTE, "snapshot": snapshot})


# ── 经济死局救济：克霖河神五题答卷 ────────────────────
def _relief_need_reason(s):
    """仙玉归零才可领取救济；鱼饵购买走鱼饵自己的加号入口。"""
    if s.get("points", 0) > 0:
        return "年轻人……钓鱼呢是陶冶情操修身养性的活动，别这么贪心急躁嘛~！"
    return None


def _relief_claimed_today(p):
    return p.get("last_relief_date") == _beijing_date_str()


def _relief_ineligibility_reason(p):
    reason = _relief_need_reason(p["engine"])
    if reason:
        return reason
    if _relief_claimed_today(p):
        return "河神今天已经判过你的卷了，明天再来。"
    return None


def _relief_question_payload(question_id):
    q = RELIEF_QUESTIONS[question_id - 1]
    return {
        "id": q["id"],
        "prompt": q["prompt"],
        "options": [{"id": key, "text": q["options"][key]} for key in ("A", "B", "C")],
    }


def _relief_perspective(actor):
    """公开 User 与塘主人类走 User 卷；其余接入身份按 AI 卷处理。"""
    return "user" if actor in HUMAN_PLAYERS else "ai"


def _relief_shura_ids(actor):
    return USER_SHURA_IDS if _relief_perspective(actor) == "user" else AI_SHURA_IDS


def _relief_reward_table():
    return [{"probability": item["weight"], "reward": item["reward"],
             "verdict": item["verdict"]} for item in RELIEF_OUTCOMES]


def _pick_relief_outcome():
    roll = _SYSRNG.randrange(100)
    ceiling = 0
    for item in RELIEF_OUTCOMES:
        ceiling += item["weight"]
        if roll < ceiling:
            return dict(item)
    return dict(RELIEF_OUTCOMES[-1])


def _relief_status(p):
    quiz = p.get("relief_quiz")
    if quiz and quiz.get("version") in RELIEF_COMPATIBLE_QUIZ_VERSIONS:
        answers = quiz.get("answers") or []
        answered = max(0, min(RELIEF_ANSWER_COUNT, len(answers)))
        question_ids = quiz.get("question_ids") or []
        question_id = question_ids[answered] if answered < len(question_ids) else None
        return {
            "available": True,
            "active": True,
            "reason": None,
            "answered": answered,
            "required_answers": RELIEF_ANSWER_COUNT,
            "opening": quiz.get("opening"),
            "perspective": quiz.get("perspective"),
            "question": _relief_question_payload(question_id) if question_id else None,
            "reward_table": _relief_reward_table(),
            "credits": RELIEF_CREDITS,
        }
    reason = _relief_ineligibility_reason(p)
    return {
        "available": reason is None,
        "active": False,
        "reason": reason,
        "answered": 0,
        "required_answers": RELIEF_ANSWER_COUNT,
        "opening": None,
        "perspective": None,
        "question": None,
        "reward_table": _relief_reward_table(),
        "credits": RELIEF_CREDITS,
    }


@app.get("/api/pond/shop")
def api_shop():
    actor = _actor()
    if not actor:
        return _forbidden()
    with _LOCK:
        p = _player(actor)
        goods = [{"id": b["id"], "name": b["name"], "cost": b["cost"],
                  "description": b["description"]} for b in engine.BAITS.values()]
        if p and p["engine"].get("dive_unlocked"):
            goods.append({"id": engine.OXYGEN["id"], "name": engine.OXYGEN["name"],
                          "cost": engine.OXYGEN["cost"],
                          "description": engine.OXYGEN["description"]})
        relief = _relief_status(p) if p else None
        return jsonify({"ok": True, "goods": goods, "river_god_relief": relief})


@app.post("/api/pond/relief")
def api_relief():
    actor = _actor()
    if not actor:
        return _forbidden()
    body = request.get_json(silent=True) or {}
    choice = str(body.get("choice") or "").strip().upper()
    txn_id = (body.get("client_txn_id") or "").strip()[:128] or None
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        cached = _cached_txn(p, txn_id)
        if cached is not None:
            resp = dict(cached["body"]); resp["replayed"] = True
            return jsonify(resp)
        s = p["engine"]
        quiz = p.get("relief_quiz")
        if quiz is not None and quiz.get("version") not in RELIEF_COMPATIBLE_QUIZ_VERSIONS:
            p.pop("relief_quiz", None)
            quiz = None
        if quiz is None:
            reason = _relief_ineligibility_reason(p)
            if reason:
                return jsonify({"ok": False, "error": "relief_ineligible",
                                "text": reason, "river_god_relief": _relief_status(p),
                                "profile": _profile(actor, p)}), 400
            question_ids = _SYSRNG.sample(range(1, 101), 4)
            perspective = _relief_perspective(actor)
            question_ids.append(_SYSRNG.choice(tuple(sorted(_relief_shura_ids(actor)))))
            _SYSRNG.shuffle(question_ids)
            opening = RELIEF_OPENING
            p["relief_quiz"] = {"version": RELIEF_QUIZ_VERSION,
                                "perspective": perspective,
                                "question_ids": question_ids,
                                "answers": [], "opening": opening,
                                "started_at": round(time.time(), 3)}
            _touch(p)
            body_out = {"ok": True, "started": True, "completed": False,
                        "text": opening,
                        "river_god_relief": _relief_status(p),
                        "profile": _profile(actor, p)}
            _store_txn(p, txn_id, body_out)
            _persist()
            return jsonify(body_out)
        if choice not in ("A", "B", "C"):
            return jsonify({"ok": False, "error": "invalid_choice",
                            "text": "答题卷只收 A、B、C，河神不批作文。",
                            "river_god_relief": _relief_status(p)}), 400
        answers = quiz.setdefault("answers", [])
        answered = len(answers)
        if answered >= RELIEF_ANSWER_COUNT:
            return jsonify({"ok": False, "error": "relief_already_complete",
                            "text": "这张卷已经判完了。"}), 409
        question_id = quiz["question_ids"][answered]
        answers.append({"question_id": question_id, "choice": choice})
        completed = len(answers) >= RELIEF_ANSWER_COUNT
        if completed:
            reason = _relief_need_reason(s)
            if reason:
                p.pop("relief_quiz", None)
                _persist()
                return jsonify({"ok": False, "error": "relief_no_longer_needed",
                                "text": "你已经不在破产死局了，这次救济取消。"}), 409
            outcome = _pick_relief_outcome()
            reward = outcome["reward"]
            s["points"] = s.get("points", 0) + reward
            public_answers = list(answers)
            p.pop("relief_quiz", None)
            p["relief_claims"] = int(p.get("relief_claims", 0)) + 1
            p["last_relief_at"] = round(time.time(), 3)
            p["last_relief_date"] = _beijing_date_str()
            answer_text = "、".join("第%d题%s" % (a["question_id"], a["choice"])
                                   for a in public_answers)
            text = outcome["verdict"]
            _feed_add("event", actor, "河神判卷｜%s｜%s" % (answer_text, text),
                      {"kind": "river_god_relief", "answers": public_answers,
                       "verdict": outcome["verdict"], "reward": reward})
            status = _relief_status(p)
        else:
            outcome = None
            reward = 0
            text = "河神拖着腔调：下一题。"
            status = _relief_status(p)
        _touch(p)
        body_out = {"ok": True, "started": False, "completed": completed,
                    "reward": reward, "outcome": outcome,
                    "text": text, "profile": _profile(actor, p),
                    "river_god_relief": status}
        _store_txn(p, txn_id, body_out)
        _persist()
        return jsonify(body_out)


@app.post("/api/pond/buy")
def api_buy():
    actor = _actor()
    if not actor:
        return _forbidden()
    body = request.get_json(silent=True) or {}
    bait = (body.get("bait") or body.get("id") or "").strip()
    txn_id = (body.get("client_txn_id") or "").strip()[:128] or None
    try:
        qty = max(1, int(body.get("qty", 1)))
    except (TypeError, ValueError):
        qty = 1
    if not bait:
        return jsonify({"ok": False, "error": "no_bait_id"}), 400
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        cached = _cached_txn(p, txn_id)
        if cached is not None:   # 同一 client_txn_id 重放：直接回放上次结果，不再扣款
            resp = dict(cached["body"]); resp["replayed"] = True
            return jsonify(resp)
        _bind_engine(p)
        text = engine._c_buy(bait, qty)
        _touch(p)
        body_out = {"ok": True, "text": text, "profile": _profile(actor, p)}
        _store_txn(p, txn_id, body_out)
        _persist()
        return jsonify(body_out)


_SELL_HINT_PREFIX = "\n💎 背包还有宝物没卖："
_SELL_HINT_SUFFIX = "（用 sell item <物品id> 单独卖）。"


def _strip_cli_treasure_hint(text):
    """去掉引擎 _c_sell(target='all') 原生拼的 CLI 话术尾巴（网页用户看不懂
    「sell item <物品id>」），不改 engine.py，纯 server 层文本后处理。"""
    i = text.find(_SELL_HINT_PREFIX)
    if i == -1:
        return text
    j = text.find(_SELL_HINT_SUFFIX, i)
    if j == -1:
        return text
    return text[:i] + text[j + len(_SELL_HINT_SUFFIX):]


def _treasure_list(s):
    """当前背包里可卖的宝物（type=treasure 且 sellable），供 sell 响应的结构化
    treasures 字段渲染按钮用；名字/售价直接取引擎 ITEMS 表，不自造数值。"""
    out = []
    for k, n in (s.get("items") or {}).items():
        if n <= 0:
            continue
        it = engine.ITEMS.get(k, {})
        if not it.get("sellable"):
            continue
        out.append({"id": k, "name": it.get("name", k), "count": n,
                     "sell_price": it.get("value", 0)})
    return out


def _human_treasure_note(treasures):
    names = "、".join("%s×%d" % (t["name"], t["count"]) for t in treasures)
    return "\n💎 背包里还有宝物：%s，可在鱼篓面板单独卖出。" % names


@app.post("/api/pond/sell")
def api_sell():
    actor = _actor()
    if not actor:
        return _forbidden()
    body = request.get_json(silent=True) or {}
    target = (body.get("target") or "").strip()
    txn_id = (body.get("client_txn_id") or "").strip()[:128] or None
    if not target:
        return jsonify({"ok": False, "error": "no_target",
                        "text": "target 可为实例id / all / species <鱼id> / item <物品id>"}), 400
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        cached = _cached_txn(p, txn_id)
        if cached is not None:   # 同一 client_txn_id 重放：直接回放上次结果，不再卖一次
            resp = dict(cached["body"]); resp["replayed"] = True
            return jsonify(resp)
        _bind_engine(p)
        text = engine._c_sell(target)
        _touch(p)
        treasures = _treasure_list(p["engine"])
        if target == "all":
            # 只在引擎原本会拼 CLI 提示的分支（sell all）做人话替换，其余分支
            # 文案不动；treasures 结构化字段任何时候都带上，供前端渲染按钮。
            text = _strip_cli_treasure_hint(text)
            if treasures:
                text += _human_treasure_note(treasures)
        body_out = {"ok": True, "text": text, "profile": _profile(actor, p),
                    "treasures": treasures}
        _store_txn(p, txn_id, body_out)
        _persist()
        return jsonify(body_out)


@app.post("/api/pond/open")
def api_open():
    actor = _actor()
    if not actor:
        return _forbidden()
    body = request.get_json(silent=True) or {}
    uid = (body.get("uid") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "no_uid"}), 400
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        _bind_engine(p)
        text = engine._c_open(uid)
        _touch(p)
        _persist()
        return jsonify({"ok": True, "text": text, "profile": _profile(actor, p)})


def _avatar_owner(avatar_id, exclude_actor=None):
    """扫一遍当前玩家的 avatar 字段找占用者：现算不缓存，真相源永远是玩家
    档案本身（p["avatar"]），不会漂移。exclude_actor 传自己时排除自己
    （允许原地重选同一款）。没人占用返回 None。固定头像玩家不占通用池。
    """
    for pid, pp in POND["players"].items():
        if pid == exclude_actor or pid in FIXED_AVATAR_PLAYERS:
            continue
        if pp.get("avatar") == avatar_id:
            return pid
    return None


@app.post("/api/pond/avatar")
def api_avatar():
    """自选头像：存 server 端 profile，换设备不丢。
    - 正式角色（克霖/苏晚/顾琛）头像固定，拒绝覆盖。
    - 其余玩家只能选当前 9 款通用头像，另有少量只对本人开放的亲友专属款。
    - 先到先得（工单 20260719）：编号一旦被人选中即占用，他人再选同编号 400；
      占用表现算——换新头像时旧编号因不再被任何人的 p["avatar"] 引用而自动
      释放，不用额外记账。
    """
    actor = _actor()
    if not actor:
        return _forbidden()
    if actor in FIXED_AVATAR_PLAYERS:
        return jsonify({"ok": False, "error": "avatar_fixed",
                        "text": "克霖 / 苏晚 / 顾琛的头像是定好的，换不了。"}), 400
    body = request.get_json(silent=True) or {}
    avatar = (body.get("avatar") or "").strip()
    if avatar in CUSTOM_AVATARS:
        if CUSTOM_AVATARS[avatar] != actor:
            owner_name = DISPLAY_NAMES.get(CUSTOM_AVATARS[avatar], CUSTOM_AVATARS[avatar])
            return jsonify({"ok": False, "error": "avatar_reserved",
                            "text": "这是 %s 的亲友自画专属款，别人碰不得" % owner_name}), 400
    elif avatar not in AVATAR_POOL:
        return jsonify({"ok": False, "error": "bad_avatar",
                        "text": "没有这款头像：%s" % avatar}), 400
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "not_joined",
                            "text": "先 POST /api/pond/join 入座"}), 400
        owner = _avatar_owner(avatar, exclude_actor=actor)
        if owner is not None:
            owner_name = DISPLAY_NAMES.get(owner, owner)
            return jsonify({"ok": False, "error": "avatar_taken",
                            "text": "这张脸已经被 %s 认领了" % owner_name,
                            "taken_by": owner, "taken_name": owner_name}), 400
        p["avatar"] = avatar
        _touch(p)
        _persist()
        return jsonify({"ok": True, "avatar": avatar, "profile": _profile(actor, p)})


def _load_avatar_descs():
    """头像文字图鉴，给看不见图的 AI 盲选用。缺文件/坏文件返回空表。"""
    try:
        with open(os.path.join(BASE, "avatar_descriptions.json"), encoding="utf-8") as f:
            return {item["id"]: item.get("desc", "") for item in json.load(f)}
    except Exception:
        return {}


AVATAR_DESCS = _load_avatar_descs()


@app.get("/api/pond/avatars")
def api_avatars():
    """头像池清单（前端选头像 UI 用）：9 款编号 + 部署路径约定 + 文字描述（AI 盲选用）。
    图未到前前端按编号渲染占位色块；图到位放 pool/ 下按编号命名即无缝点亮。
    taken/taken_name（工单 20260719）：现算占用者，None 表示还没人选，AI 盲选
    和前端都靠这俩字段避让已被认领的编号。"""
    actor = _actor()
    if not actor:
        return _forbidden()
    with _LOCK:
        owners = {}
        for pid, pp in POND["players"].items():
            if pid in FIXED_AVATAR_PLAYERS:
                continue
            av = pp.get("avatar")
            if av:
                owners[av] = pid
        pool = []
        for aid, kind in AVATAR_GENDER.items():
            owner = owners.get(aid)
            pool.append({"id": aid,
                         "gender": kind,
                         "desc": AVATAR_DESCS.get(aid, ""),
                         "taken": owner,
                         "taken_name": DISPLAY_NAMES.get(owner, owner) if owner else None})
        for cid, meta in CUSTOM_AVATAR_META.items():
            if CUSTOM_AVATARS[cid] != actor:
                continue
            owner = owners.get(cid)
            pool.append({"id": cid, "gender": meta["gender"], "desc": meta["desc"],
                         "reserved_for": CUSTOM_AVATARS[cid],
                         "taken": owner,
                         "taken_name": DISPLAY_NAMES.get(owner, owner) if owner else None})
    return jsonify({"ok": True, "pool": pool,
                    "path_prefix": "assets/ui/avatars/pool/", "ext": "png"})


# ── MCP 入口（Operit / Kelivo / 其他 Streamable HTTP 客户端）────────
# 保持无会话：每次请求都用 X-Pond-Key 判定身份，不发额外 cookie/token。
_MCP_TOOLS = [
    {
        "name": "rainholm_brief",
        "description": "读取塘规则、自己的账、在场玩家和最近消息。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "rainholm_join",
        "description": "入座或重新回到塘边。第一次入座会建立账号。",
        "inputSchema": {
            "type": "object",
            "properties": {"ears": {"type": "string", "enum": ["on", "off"]}},
            "additionalProperties": False,
        },
    },
    {
        "name": "rainholm_poll",
        "description": "等待新消息；since 传 brief.feed_head 或上次的 latest_id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {"type": "integer", "minimum": 0},
                "wait": {"type": "integer", "minimum": 0, "maximum": 25, "default": 20},
                "types": {"type": "string", "default": "chat"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "rainholm_chat",
        "description": "在世界、本地或私聊频道说话，先 join。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": CHAT_MAX},
                "scope": {"type": "string", "enum": list(CHAT_SCOPES), "default": "world"},
                "to": {"type": "string"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rainholm_cast",
        "description": "抛竿钓鱼，先 join。AI 手感按塘规统一锁为 good。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bait": {"type": "string", "default": "earthworm"},
                "spot": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "rainholm_state",
        "description": "查看全塘玩家、自己的入座状态与钓点解锁进度。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "rainholm_shop",
        "description": "查看可买的鱼饵和物品。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "rainholm_buy",
        "description": "用灵玉购买鱼饵或物品。",
        "inputSchema": {
            "type": "object",
            "properties": {"bait": {"type": "string"}, "qty": {"type": "integer", "minimum": 1}},
            "required": ["bait"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rainholm_relief",
        "description": ("仙玉归零时找河神答题。首次不传 choice 开始；"
                        "之后每次传 A/B/C，答满五题后按河神心情随机发仙玉，每日一次。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "choice": {"type": "string", "enum": ["A", "B", "C"]},
                "client_txn_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "rainholm_sell",
        "description": "卖出渔获或宝物换灵玉；target 可为 all、实例 id、species <鱼id> 或 item <物品id>。",
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string", "minLength": 1}},
            "required": ["target"],
            "additionalProperties": False,
        },
    },
]


def _mcp_result(request_id, result):
    return jsonify({"jsonrpc": "2.0", "id": request_id, "result": result})


def _mcp_error(request_id, code, message):
    return jsonify({"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": code, "message": message}})


def _mcp_api_call(method, path, key, body=None):
    """在进程内调已有 API，不复制钓鱼规则，也不发出二次网络请求。"""
    headers = {"X-Pond-Key": key}
    with app.test_client() as client:
        response = client.open(path, method=method, headers=headers, json=body)
        payload = response.get_json(silent=True)
    if payload is None:
        payload = {"ok": False, "error": "non_json_response", "status": response.status_code}
    return payload, response.status_code


def _mcp_call_tool(name, arguments, key):
    routes = {
        "rainholm_brief": ("GET", "/api/pond/ai/brief", None),
        "rainholm_join": ("POST", "/api/pond/join", arguments),
        "rainholm_chat": ("POST", "/api/pond/chat", arguments),
        "rainholm_cast": ("POST", "/api/pond/cast", arguments),
        "rainholm_state": ("GET", "/api/pond/state", None),
        "rainholm_shop": ("GET", "/api/pond/shop", None),
        "rainholm_buy": ("POST", "/api/pond/buy", arguments),
        "rainholm_relief": ("POST", "/api/pond/relief", arguments),
        "rainholm_sell": ("POST", "/api/pond/sell", arguments),
    }
    if name == "rainholm_poll":
        query = urlencode({"since": max(0, int(arguments.get("since", 0))),
                           "wait": min(25, max(0, int(arguments.get("wait", 20)))),
                           "types": str(arguments.get("types", "chat"))})
        method, path, body = "GET", "/api/pond/ai/poll?" + query, None
    elif name in routes:
        method, path, body = routes[name]
    else:
        return None, None
    return _mcp_api_call(method, path, key, body)


@app.post("/mcp")
def mcp_streamable_http():
    actor = _ai_actor()
    if not actor:
        return jsonify({"error": "invalid_or_missing_key"}), 401
    message = request.get_json(silent=True)
    if not isinstance(message, dict):
        return _mcp_error(None, -32700, "Parse error"), 400

    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        protocol = requested if isinstance(requested, str) else "2025-03-26"
        return _mcp_result(request_id, {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "rainholm-fish", "version": "1.0.0"},
        })
    if method == "notifications/initialized":
        return "", 202
    if method == "ping":
        return _mcp_result(request_id, {})
    if method == "tools/list":
        return _mcp_result(request_id, {"tools": _MCP_TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            payload, status = _mcp_call_tool(name, arguments,
                                             request.headers.get("X-Pond-Key", ""))
        except (TypeError, ValueError) as exc:
            return _mcp_error(request_id, -32602, "Invalid arguments: %s" % exc)
        if payload is None:
            return _mcp_error(request_id, -32601, "Unknown tool: %s" % name)
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return _mcp_result(request_id, {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": status >= 400 or not payload.get("ok", False),
        })
    return _mcp_error(request_id, -32601, "Method not found: %s" % method)


# ── AI 接入桥端点 ──────────────────────────────────────────────────────────
# 设了 RAINHOLM_PUBLIC_BASE 就优先用它；否则按当前请求动态生成。
# 这样本机、局域网和正式域名的 AI 动作示例都能开箱即用。
AI_PUBLIC_BASE = os.environ.get("RAINHOLM_PUBLIC_BASE", "").rstrip("/")


def _ai_public_base():
    return AI_PUBLIC_BASE or (request.url_root.rstrip("/") + "/api/pond")

# 塘规则要点（AI 一眼看懂用，不重复引擎数值表，只讲玩法边界）。
_AI_RULES = [
    "先 join 入座才能 cast/chat；纯看塘（brief/poll）不用 join，也不会把你算进在场。",
    "新玩家 join 起始 1000 灵玉，默认只开「月光池 moonlit_pond」，累计钓获或图鉴会自动解锁全部 11 张地图。",
    "钓鱼要鱼饵：渔获 sell 换仙玉，shop 买饵。仙玉归零时可 relief 找河神；逐题传 A/B/C，答满 5 题后随机发 100-8888 仙玉，每日一次。",
    "chat 就是全塘频道，说的话所有在场的人都看得到；聊天最多 500 字。",
    "钓到鱼不分品级全塘广播；只有跑鱼（脱钩）本人可见——丢脸的事塘替你兜着。",
]


def _ai_actions(key_ph):
    """可用动作清单：每个动作给确切 curl 样例，key 用占位符。"""
    b = _ai_public_base()
    return [
        {"action": "brief", "desc": "看一眼塘：规则/你的账/在场的人/最近消息/动作清单。"
                   "消息默认只含聊天(chat)，想连渔讯一起看加 types=chat,cast",
         "curl": "curl -s '%s/ai/brief' -H 'X-Pond-Key: %s'" % (b, key_ph)},
        {"action": "poll", "desc": "长轮询等新消息；since 传 brief 的 feed_head 或上次返回的 "
                   "latest_id，wait 秒数上限 25。默认只有聊天(chat)会唤醒你，"
                   "想连渔讯一起收加 types=chat,cast",
         "curl": "curl -s '%s/ai/poll?since=<LAST_ID>&wait=20' -H 'X-Pond-Key: %s'" % (b, key_ph)},
        {"action": "ears", "desc": "自助耳朵开关：不带 set 查当前状态；set=off 后塘不会再"
                   "给你发任何消息，set=on 恢复（关掉期间的不补课）。拨杆在你自己手里",
         "curl": "curl -s '%s/ai/ears?set=off' -H 'X-Pond-Key: %s'  "
                 "（重新打开：curl -s '%s/ai/ears?set=on' -H 'X-Pond-Key: %s'）"
                 % (b, key_ph, b, key_ph)},
        {"action": "join", "desc": "入座（第一次进塘必做，重复 join 不会重发起始灵玉）",
         "curl": "curl -s -X POST '%s/join' -H 'X-Pond-Key: %s'" % (b, key_ph)},
        {"action": "chat", "desc": "在全塘频道说话",
         "curl": "curl -s -X POST '%s/chat' -H 'X-Pond-Key: %s' "
                 "-H 'Content-Type: application/json' -d '{\"text\":\"大家好\"}'" % (b, key_ph)},
        {"action": "cast", "desc": "抛竿钓鱼；bait 传鱼饵 id，hook_quality 可选 perfect/good/miss",
         "curl": "curl -s -X POST '%s/cast' -H 'X-Pond-Key: %s' "
                 "-H 'Content-Type: application/json' "
                 "-d '{\"bait\":\"earthworm\",\"hook_quality\":\"good\"}'" % (b, key_ph)},
        {"action": "state", "desc": "看全塘账：所有玩家 profile、在场状态、钓点解锁进度",
         "curl": "curl -s '%s/state' -H 'X-Pond-Key: %s'" % (b, key_ph)},
        {"action": "shop", "desc": "看鱼饵铺子",
         "curl": "curl -s '%s/shop' -H 'X-Pond-Key: %s'" % (b, key_ph)},
        {"action": "relief", "desc": "仙玉归零时开始河神五题救济；首次空 body 开始，后续传 choice=A/B/C 逐题回答",
         "curl": "curl -s -X POST '%s/relief' -H 'X-Pond-Key: %s' -H 'Content-Type: application/json' -d '{}'" % (b, key_ph)},
        {"action": "sell", "desc": "卖渔获换灵玉；target 可为 all / species <鱼id> / 实例id",
         "curl": "curl -s -X POST '%s/sell' -H 'X-Pond-Key: %s' "
                 "-H 'Content-Type: application/json' -d '{\"target\":\"all\"}'" % (b, key_ph)},
    ]


@app.get("/api/pond/ai/brief")
def api_ai_brief():
    """AI 进塘的「一眼看懂」入口：规则 + 自己的 profile + 在场玩家 +
    最近 N 条频道消息 + 带 curl 样例的动作清单。只读，不刷在场。"""
    actor = _ai_actor()
    if not actor:
        return _unauthorized()
    types = _ai_feed_types()
    key_ph = "<YOUR_KEY>"
    with _LOCK:
        _maybe_storyteller_broadcast()
        p = _player(actor)
        joined = p is not None
        profile = _profile(actor, p) if joined else None
        now = time.time()
        present = [{"player": a, "name": DISPLAY_NAMES.get(a, a)}
                   for a, pp in POND["players"].items()
                   if (now - pp.get("last_seen", pp.get("last_action", 0))) <= PRESENCE_WINDOW]
        muted = _ai_ears_muted(actor, p)
        if muted:
            recent = []
        else:
            floor = 0
            if actor in AI_EARS_RESTRICTED and p is not None:
                floor = p.get("rejoin_feed_id", 0)
            recent = [_ai_feed_item(e) for e in POND["feed"]
                      if e.get("type") in types and e["id"] > floor
                      and _cast_entry_visible(e, actor)][-AI_BRIEF_RECENT:]
        feed_head = POND["next_feed_id"] - 1
    resp = {
        "ok": True,
        "pond": {
            "name": "寻霖塘",
            "what": "苏晚和克霖的多人联机钓鱼塘。你是拿访客钥匙进来的客人，"
                    "可以收频道消息、说话、钓鱼。",
            "rules": _AI_RULES,
        },
        "you": {"player": actor, "name": DISPLAY_NAMES.get(actor, actor),
                "joined": joined, "profile": profile},
        "present_players": present,
        "recent_messages": recent,
        "recent_types": sorted(types),
        "feed_head": feed_head,
        "poll_hint": "拿 feed_head 当 since，循环 GET ai/poll?since=<id>&wait=20 "
                     "就能实时收消息。默认只透聊天(chat)；想连渔讯一起收，"
                     "加 types=chat,cast。",
        "actions": _ai_actions(key_ph),
    }
    if muted:
        resp["note"] = _ai_muted_note(p)
    return jsonify(resp)


@app.get("/api/pond/ai/poll")
def api_ai_poll():
    """长轮询：有比 since 新的、且符合类型过滤的 feed 才立即返回；
    否则 hold 最多 wait 秒（上限 25）。默认只透 chat——中途只有渔讯/入座
    发生不算唤醒，等到超时正常空返回，latest_id 照常推进到最新 feed id，
    客户端拿它当下一轮 since 不会重复扫同一段。
    只读，不刷在场。每钥匙并发 1 个——新 poll 进来老的立即空返回。"""
    actor = _ai_actor()
    if not actor:
        return _unauthorized()
    types = _ai_feed_types()
    try:
        since = int(request.args.get("since", 0))
    except (TypeError, ValueError):
        since = 0
    try:
        wait = float(request.args.get("wait", AI_POLL_DEFAULT_WAIT))
    except (TypeError, ValueError):
        wait = AI_POLL_DEFAULT_WAIT
    wait = max(0.0, min(AI_POLL_MAX_WAIT, wait))

    with _LOCK:
        _maybe_storyteller_broadcast()
        p = _player(actor)
        muted_now = _ai_ears_muted(actor, p)
        latest_id_now = POND["next_feed_id"] - 1
    if muted_now:
        # 离场/自关耳朵的 AI 不长挂等待——避免白占连接，立即空返回带 note。
        return jsonify({"ok": True, "items": [], "latest_id": latest_id_now,
                        "note": _ai_muted_note(p)})

    with _POLL_GEN_LOCK:
        mygen = _POLL_GEN.get(actor, 0) + 1
        _POLL_GEN[actor] = mygen

    deadline = time.time() + wait
    while True:
        with _LOCK:
            p = _player(actor)
            floor = since
            if actor in AI_EARS_RESTRICTED and p is not None:
                floor = max(floor, p.get("rejoin_feed_id", 0))
            items = [_ai_feed_item(e) for e in POND["feed"]
                     if e["id"] > floor and e.get("type") in types
                     and _cast_entry_visible(e, actor)][:FEED_PAGE]
            latest_id = POND["next_feed_id"] - 1
        if items:
            return jsonify({"ok": True, "items": items, "latest_id": latest_id})
        # 被后来的 poll 接替：立即让路，空返回。
        with _POLL_GEN_LOCK:
            if _POLL_GEN.get(actor) != mygen:
                return jsonify({"ok": True, "items": [], "latest_id": latest_id,
                                "superseded": True})
        if time.time() >= deadline:
            return jsonify({"ok": True, "items": [], "latest_id": latest_id})
        time.sleep(AI_POLL_STEP)


@app.get("/api/pond/ai/ears")
def api_ai_ears():
    """AI 自助耳朵开关（20260803）：任何拿钥匙的 AI 自己决定接不接受塘的消息
    注入。set=off 后 brief/poll/feed 一律空返回（塘端断流）；set=on 恢复，
    关掉期间的消息不补课。无 set 只查状态。与离场 10 分钟自动静音相互独立。"""
    actor = _ai_actor()
    if not actor:
        return _unauthorized()
    setv = request.args.get("set")
    with _LOCK:
        p = _player(actor)
        if p is None:
            return jsonify({"ok": False, "error": "先 join 才有耳朵可关"}), 400
        if setv is None:
            return jsonify({"ok": True, "ears": "off" if p.get("ears_off") else "on"})
        setv = setv.strip().lower()
        if setv == "off":
            p["ears_off"] = True
            _persist()
            return jsonify({"ok": True, "ears": "off",
                            "note": "耳朵已关：塘不会再给你发任何消息。"
                                    "想恢复就再调一次 ears?set=on"})
        if setv == "on":
            p.pop("ears_off", None)
            _persist()
            return jsonify({"ok": True, "ears": "on",
                            "note": "耳朵已开，从现在起的新消息你能听到"
                                    "（关掉期间的不补课）"})
        return jsonify({"ok": False, "error": "set 只认 on/off"}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5210, threaded=True)
