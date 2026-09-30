"""
编码检测模块 - 使用 charset-normalizer 检测文件编码
"""

import codecs
import re
from pathlib import Path
from typing import NamedTuple

import charset_normalizer

from converter import canonical_encoding


class EncodingResult(NamedTuple):
    """编码检测结果"""
    encoding: str
    confidence: float


# BOM 标识及其对应的编码。
# 注意：长 BOM 必须排在短 BOM 之前，否则 UTF-32-LE 的 BOM (FF FE 00 00) 会
# 被 UTF-16-LE 的 BOM (FF FE) 先命中而误判为 UTF-16-LE。
_BOM_MAP = [
    (codecs.BOM_UTF32_LE, 'utf-32-le'),   # FF FE 00 00
    (codecs.BOM_UTF32_BE, 'utf-32-be'),   # 00 00 FE FF
    (codecs.BOM_UTF8, 'utf-8-sig'),       # EF BB BF
    (codecs.BOM_UTF16_LE, 'utf-16-le'),   # FF FE
    (codecs.BOM_UTF16_BE, 'utf-16-be'),   # FE FF
]

# charset-normalizer 只喂前 64KB:其判断在几十 KB 后即饱和,喂全量只带来 O(n) 开销
# (2MB GBK 全量检测 ~138ms,限样本 ~2ms),且 GBK 优先/gb18030 兜底的严格校验仍跑全量。
_CN_SAMPLE = 65536

# detect 工具/缓存未命中探测读取的窗口大小。多读 1 字节:既判断窗口是否截断,
# 也在窗口末字节恰为 \r 时把被劈开的 CRLF 配对完整(行尾统计不失真)。
_PROBE_WINDOW = 32768


def decode_text(data: bytes, encoding: str, *, prefix: bool = False) -> str | None:
    """严格解码 data,解不开(或不支持的编码名)返回 None。

    prefix=True:data 是文件的任意截断前缀(窗口外还有字节)。用增量解码器解码,
    末尾可能被窗口截断的不完整多字节序列被缓冲等待而不是报错;中间的非法字节
    仍会报错。用于 32KB 探测窗口:窗口边界劈开汉字/UTF-8 序列时严格校验不至于失效。
    """
    try:
        if prefix:
            return codecs.getincrementaldecoder(encoding)().decode(data, False)
        return data.decode(encoding)
    except (UnicodeError, LookupError, ValueError):
        return None


def _detect_by_bom(data: bytes) -> str | None:
    """通过 BOM 检测编码"""
    for bom, encoding in _BOM_MAP:
        if data.startswith(bom):
            return encoding
    return None


def _normalize_encoding(detected: str) -> str:
    """
    标准化编码名称:归一到 Python codecs 规范名(连字符风格),
    GB 系与 BOM 系保留项目内部使用的名称以便写回时恢复 BOM。
    """
    lower = detected.lower().replace('-', '').replace('_', '')

    # GB2312 是 GBK 的子集，统一使用 GBK
    if lower in ('gb2312', 'gbk'):
        return 'gbk'

    # GB18030 是 GBK 的超集，保留以避免数据丢失
    if lower == 'gb18030':
        return 'gb18030'

    # UTF-8 with BOM，保留标识以便写回时恢复 BOM
    if lower == 'utf8sig':
        return 'utf-8-sig'

    # UTF-8 / ASCII 兼容 UTF-8
    if lower in ('utf8', 'ascii'):
        return 'utf-8'

    # 其余编码名(big5/cp949/johab/cp1252…)交 codecs 归一到规范名(与 converter 同一实现)
    name = canonical_encoding(detected)
    return name if name is not None else detected.lower()


# 单字节遗留编码（windows-1250、iso-8859-5、cp1252、mac_roman 等）把每个高位字节
# 独立映射成字符，能解码任意字节流——decode 成功无意义。用 0x80 探测：所有严格
# 多字节编码（gbk/gb18030/utf-8/shift_jis/big5/euc-*）都不接受 0x80 作为合法字节，
# 解不开；单字节遗留编码则能解开。
_PERMISSIVE_PROBE = b'\x80'


def _is_permissive_single_byte(encoding: str) -> bool:
    """该编码是否为“能解码任意字节流”的单字节遗留编码（decode 成功无意义）。"""
    return decode_text(_PERMISSIVE_PROBE, encoding) is not None


# ── 中文判定仲裁 ──────────────────────────────────────────
# GBK 与 big5/cp949/shift_jis 等严格多字节 CJK 编码同是双字节结构、字节区间高度
# 重叠:GBK 文件几乎总能被它们“合法”地解码成乱码,反之亦然。charset-normalizer
# 对纯中文短文本常把 GBK 误报成 big5/cp949(实测「初始化完成」→big5、
# 「系统初始化完毕请继续执行」→cp949)。用“常用简体汉字占全部 CJK 字符的比例”
# 区分:正确解码出常用字(的一是了在…),错误解码出的是生僻字/繁体/韩文,占比近 0。
# 阈值经真实样本校准:13 个常见中文短语 GBK 解码占比 0.43~1.00,误判编码解码
# 0.00~0.25;真 big5 文本要么 GBK 严格解码直接失败(守卫不触发),能解开的其
# GBK 乱码常用字占比也 <0.05,不会误伤。

_CJK_CHAR_RE = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+')

_ZH_MIN_COUNT = 3     # CJK 字符数下限:汉字太少证据不足,不仲裁
_ZH_MIN_RATIO = 0.3   # GB 解码的常用字占比下限
_ZH_WIN_FACTOR = 2.0  # GB 占比须超过对方占比的倍数

# 打分样本上限(字符):判定只需“是否成段常用汉字”,MB 级大文件全量正则扫描不划算,
# 只统计前后各 _ZH_TEXT_SAMPLE。中部才有 CJK 的极端布局会因此放弃仲裁、落到
# gb18030 兜底(读出内容与 gbk 等价),不值得为其付全量扫描。
_ZH_TEXT_SAMPLE = 65536

_COMMON_ZH_CHARS = (
    "的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那得于"
    "着下自之年过发后作里用道行所然家种事成方多经么去法学如都同现当没动面起看定天分还"
    "进好小部其些主样理心她本前开但因只从想实日者意无力它与长把机民第公此已工使情明性"
    "知全三又关点正业外将两高间由问很最重并物手应战向头文体政美相见被利什二等产或新己"
    "制身果加西月话合回特代内信表化老给世位次度门任常先海通教儿原东声提立及比员解水名"
    "真论处走义各入几口认条平系气题活尔更别打女变四神总何电数安少报才结反受目太量再感"
    "建务做接必场件计管期市直德资命山金指克许统区保至队形社便空决治展马科司五基眼书非"
    "则听白却界达光放强即像难且权思王象完设式色路记南品住告类求据程北边死张该交规万取"
    "拉格望觉术领共确传师观清今切院让识候带导争运笑飞风步改收根干造言联持组每济车亲极"
    "林服快办议往元英士证近失转夫令准布始怎呢存未远叫台单影具罗字爱击流备兵连调深商算"
    "质团集百需价花华兴左右低短旧慢坏假满轻早晚浅暗冷暖送买卖读写答找拿坐站跑返离靠初"
    "终端线层级段片块整半双另余仅既虽须允肯敢愿念盼希待刻钟秒周春夏秋冬晴阴雨雪雷云雾"
    "霜露江河湖洋溪池井泉田土沙石铁铜银铅锌锡木树草叶苗芽茎枝皮毛角骨肉血汗泪涕唾液奶"
    "汁酒茶饭米菜鱼虾蟹鸡鸭鹅牛羊猪狗猫驴骡兔鼠蛇龙虎鹿狼熊猴蚁蜂蝶鸟燕雀鹰鸦轮船帆桨"
    "橹枪炮剑刀弓箭盾甲盔帽衣裳裙裤鞋袜巾棉麻丝绸缎绳绑捆扎包裹装盛藏贮置搁挂悬吊垂降"
    "落升涨沉浮漂静停末幼壮弱健虚疾病疼痛痒晕昏迷睡醒吃喝咬嚼吞咽消吐吸呼喘嗅闻味香臭"
    "酸甜苦辣咸淡涩油腻燥湿润粘滑粗糙细嫩柔软坚硬牢固脆松紧宽窄厚薄亮黑红黄蓝绿紫灰褐"
    "棕橙粉彩纹斑圆尖钝弯曲斜竖横纵躺卧趴蹲跪跳跃跌摔撞碰擦磨碾旋环绕复遍趟顿阵批架辆"
    "艘册页篇章句典词汇语音韵节奏律歌唱演弹敲拍抚摸推按捏挤压拧撕扯拽拖曳牵引举抬扛搬"
    "携示警诫劝勉激励鼓催促督追赶迟速迅捷敏锐笨拙灵巧娴熟精练疏陌懂楚模糊隐显秘密私享"
    "普殊异寻偶频繁继续延扩缩减增损益弊优劣善恶丑缺乏足够充剩浪费省约俭奢侈豪朴素简杂"
    "琐易困容顺畅阻碍妨扰乱搅响效功绩败胜输赢赚赔盈亏支付账款项钱货币财富穷贵贱值营职"
    "企厂矿农军官帅伍群众编审校印刷版刊登广宣播媒介纸志献料档案卷宗图馆室厅堂房屋厨浴"
    "厕卫窗墙壁顶楼梯阶街径途旅驾骑乘舰艇航驶驭操掌控握"
)

# 打分用正则:两趟均为 C 级扫描,且重复字如实计入(集合交集会把同字多次出现算一次)
_COMMON_ZH_RE = re.compile('[' + _COMMON_ZH_CHARS + ']')


def _zh_common_ratio(text: str) -> tuple[float, int]:
    """返回 (常用简体汉字占 CJK 字符的比例, CJK 字符总数)。"""
    total = sum(map(len, _CJK_CHAR_RE.findall(text)))
    if total == 0:
        return 0.0, 0
    hits = len(_COMMON_ZH_RE.findall(text))
    return hits / total, total


def _zh_sample_text(text: str) -> str:
    """打分前截取文本前后各 _ZH_TEXT_SAMPLE 字符,把大文件的扫描开销封顶。"""
    if len(text) <= _ZH_TEXT_SAMPLE * 2:
        return text
    return text[:_ZH_TEXT_SAMPLE] + text[-_ZH_TEXT_SAMPLE:]


def _zh_prefers_gb(gb_text: str, other_text: str | None) -> bool:
    """GB 解码与另一路解码都成立时,GB 一方是否明显更像简体中文文本。"""
    g_ratio, g_count = _zh_common_ratio(_zh_sample_text(gb_text))
    if g_count < _ZH_MIN_COUNT or g_ratio < _ZH_MIN_RATIO:
        return False
    if other_text is None:
        # 对方编码连数据都解不开,而 GB 解码出成段常用汉字 ⇒ 采信 GB
        return True
    o_ratio, o_count = _zh_common_ratio(_zh_sample_text(other_text))
    return o_count < _ZH_MIN_COUNT or g_ratio >= _ZH_WIN_FACTOR * o_ratio


def _decode_gb_candidate(data: bytes, *, prefix: bool = False) -> tuple[str, EncodingResult] | None:
    """按 gbk→gb18030(严格超集)顺序试解码,成功返回 (文本, 结果)。

    GB 系两处“优先 gbk、回退 gb18030”的重复链(前缀仲裁、单字节守卫)与
    BOM 拼接体共用此单一实现:gbk 解得开时 gb18030 解出的是同一文本,
    优先 gbk 只为编码标签更精确。"""
    text = decode_text(data, 'gbk', prefix=prefix)
    if text is not None:
        return text, EncodingResult(encoding='gbk', confidence=0.85)
    text = decode_text(data, 'gb18030', prefix=prefix)
    if text is not None:
        return text, EncodingResult(encoding='gb18030', confidence=0.8)
    return None


def detect_encoding(data: bytes, *, prefix: bool = False) -> EncodingResult:
    """
    检测字节数据的编码(优先中文编码 GBK/GB18030)。

    prefix=True:data 是文件的任意截断前缀(窗口外还有字节)。所有严格校验容忍
    末尾可能被截断的多字节序列;且 UTF-8 快路径的“解码成功”不再终局——悬空
    尾字节在“被截断的 UTF-8 序列”与“被截断的 GBK 双字节”之间两可,若 GB 系
    同样解得开且更像中文,判 GB 系。prefix=False(整段数据)时严格解码成功
    即无歧义,直接返回(与既有行为一致)。
    """
    # 先检查 BOM
    bom_encoding = _detect_by_bom(data)
    if bom_encoding:
        return EncodingResult(encoding=bom_encoding, confidence=1.0)

    # 快速检测是否包含高位字节(bytes.isascii() 是 C 级扫描,不分配临时拷贝)
    if data.isascii():
        # 纯 ASCII，使用 UTF-8
        return EncodingResult(encoding='utf-8', confidence=1.0)

    # UTF-8 快路径:严格解码整段成功 ⇒ 确为 UTF-8。UTF-8 是严格编码,GBK/SJIS/Big5 等
    # 的高位字节几乎不可能整段凑成合法 UTF-8,故解码成功即无歧义,无需 charset-normalizer。
    # 注:仅此一种严格解码快路径安全——GBK/gb18030 过于宽松(会吞孤立 0x80、误吞异种 CJK),
    # 仍交由下方 charset-normalizer + GBK 优先 + gb18030 兜底的既有精细逻辑处理。
    utf8_text = decode_text(data, 'utf-8', prefix=prefix)
    if utf8_text is not None:
        if not prefix:
            return EncodingResult(encoding='utf-8', confidence=1.0)
        # 前缀窗口:悬空尾字节两可,GB 系同样可解且更像中文时判 GB 系
        # (顺带纠正”GBK 字节恰好构成合法 UTF-8”的罕见短文本,见上方快路径说明的例外)。
        hit = _decode_gb_candidate(data, prefix=prefix)
        if hit is not None and _zh_prefers_gb(hit[0], utf8_text):
            return hit[1]
        return EncodingResult(encoding='utf-8', confidence=1.0)

    # charset-normalizer 检测(只喂前 _CN_SAMPLE 字节,判断精度在几十 KB 后即饱和)
    try:
        result = charset_normalizer.detect(data[:_CN_SAMPLE])
        if result and result.get('encoding'):
            detected_encoding = result['encoding']
            if detected_encoding is None:
                raise ValueError("encoding is None after check")
            encoding = _normalize_encoding(detected_encoding)

            # charset-normalizer 对“大段 ASCII 夹少量中文”的源码常误报为 windows-1250 /
            # iso-8859-X 这类单字节遗留编码（它们能解码任意字节流，置信度虚高）。此时先做
            # GBK 严格校验：GBK 解得开说明高位字节确实成对组成中文，应优先于单字节猜测。
            if _is_permissive_single_byte(encoding):
                hit = _decode_gb_candidate(data, prefix=prefix)
                if hit is not None:
                    return hit[1]

            # 严格多字节 CJK 编码（big5/cp949/shift_jis 等）与 GBK 字节结构高度重叠:
            # GBK 文件几乎总能被它们“合法”解码成乱码,纯中文短文本常被误报。GBK 解得开
            # 且常用字占比明显更像中文时,改判 GB 系(校准说明见 _zh_prefers_gb 上方注释)。
            elif not encoding.startswith('gb'):
                gbk_text = decode_text(data, 'gbk', prefix=prefix)
                if gbk_text is not None:
                    # CN 报 utf-8/ascii 与“快路径严格 utf-8 已失败”自相矛盾,无需再解码
                    cand = None if encoding == 'utf-8' else decode_text(data, encoding, prefix=prefix)
                    if _zh_prefers_gb(gbk_text, cand):
                        return EncodingResult(encoding='gbk', confidence=0.85)
                    if cand is not None:
                        # 守卫已顺手验证候选可解码,直接采信,免通用路径再解一遍
                        confidence = result.get('confidence', 0.9) or 0.9
                        return EncodingResult(encoding=encoding, confidence=confidence)

            if decode_text(data, encoding, prefix=prefix) is not None:
                confidence = result.get('confidence', 0.9) or 0.9
                return EncodingResult(encoding=encoding, confidence=confidence)
    except Exception:
        pass

    # 尝试 GB18030 解码验证（gb18030 是 gbk 的严格超集：能正确覆盖所有 gbk 文件，
    # 且不会把含 4 字节序列的 gb18030 文件误判为 gbk）
    if decode_text(data, 'gb18030', prefix=prefix) is not None:
        return EncodingResult(encoding='gb18030', confidence=0.8)

    # 默认使用 GB18030（超集，比 gbk 更安全）
    return EncodingResult(encoding='gb18030', confidence=0.5)


def detect_file_encoding(file_path: str | Path) -> EncodingResult:
    """
    检测文件编码（仅读取前 32KB 用于检测）
    """
    return detect_file_encoding_details(file_path)[0]


def detect_file_encoding_details(file_path: str | Path) -> tuple[EncodingResult, str]:
    """
    一次读取前 _PROBE_WINDOW(+1) 字节，同时返回编码检测结果与行尾风格。

    返回 (EncodingResult, line_ending)，line_ending 见 detect_line_ending。
    供只探测不读内容的工具使用，避免重复读文件。多读的 1 字节既用于判断窗口
    是否截断,也把可能被窗口劈开的 CRLF 带上行尾统计,纯 CRLF 文件不会误报 mixed。
    窗口外还有字节时按“前缀”探测:严格校验容忍末尾被截断的多字节序列(否则
    窗口劈开汉字曾把稀疏 GBK 判成 windows-1250、大 UTF-8 判成 gb18030)。
    """
    path = Path(file_path)
    with open(path, 'rb') as f:
        chunk = f.read(_PROBE_WINDOW + 1)

    if not chunk:
        return EncodingResult(encoding='utf-8', confidence=1.0), 'none'

    # 整个文件都在窗口内(prefix=False)时严格校验,不做前缀容错
    prefix = len(chunk) > _PROBE_WINDOW
    return detect_with_safety_net(chunk, prefix=prefix), detect_line_ending(chunk)


def decode_bom_gb_body(data: bytes, *, prefix: bool = False) -> tuple[str, str] | None:
    """读取“UTF-8 BOM + GB 系正文”的拼接体(非合法 UTF-8,常见于手工修复过的文件)。

    剥掉 BOM 后按 gbk→gb18030(严格超集)顺序校验正文,成功返回 (正文, 编码);
    数据不带 UTF-8 BOM 或正文两者都解不开返回 None。安全网与读侧自愈共用此
    单一实现,gbk 优先只为编码标签更精确,两种编码解出的文本相同。
    """
    if not data.startswith(codecs.BOM_UTF8):
        return None
    hit = _decode_gb_candidate(data[3:], prefix=prefix)
    if hit is None:
        return None
    return hit[0], hit[1].encoding


def detect_with_safety_net(data: bytes, *, prefix: bool = False) -> EncodingResult:
    """detect_encoding + 读侧安全网。

    utf-8-sig 结论是 BOM 命中即返回的、正文未经校验——正文若解不开 UTF-8
    (典型:BOM + GBK 正文的拼接体),回退 GB 系。utf-8(无 BOM)结论在
    detect_encoding 内部已验证可解码,无需复核。
    """
    result = detect_encoding(data, prefix=prefix)

    if result.encoding == 'utf-8-sig' and decode_text(data, 'utf-8-sig', prefix=prefix) is None:
        recovered = decode_bom_gb_body(data, prefix=prefix)
        if recovered is not None:
            return EncodingResult(encoding=recovered[1], confidence=0.9)

    return result


def detect_line_ending(data: bytes | str) -> str:
    """
    检测字节流或字符串的行尾风格，仅做统计，不做任何规范化。

    返回 'CRLF' / 'LF' / 'CR' / 'mixed' / 'none'：
    - CRLF：行尾全部为 \\r\\n
    - LF：行尾全部为独立 \\n
    - CR：行尾全部为独立 \\r
    - mixed：同时存在不止一种行尾
    - none：无任何换行符

    接受 str 是为了避免调用方为统计行尾而把整段内容编码成 bytes（O(N) 拷贝）。
    """
    if isinstance(data, str):
        crlf = data.count('\r\n')
        total_lf = data.count('\n')
        total_cr = data.count('\r')
    else:
        crlf = data.count(b'\r\n')
        total_lf = data.count(b'\n')
        total_cr = data.count(b'\r')
    lone_lf = total_lf - crlf
    lone_cr = total_cr - crlf

    if crlf == 0 and lone_lf == 0 and lone_cr == 0:
        return 'none'
    if lone_lf == 0 and lone_cr == 0:
        return 'CRLF'
    if crlf == 0 and lone_cr == 0:
        return 'LF'
    if crlf == 0 and lone_lf == 0:
        return 'CR'
    return 'mixed'
