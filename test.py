#!/usr/bin/env python3
import streamlit as st
import freetype
import math
import tempfile
import os
import struct
import base64
from collections import namedtuple
from io import BytesIO
from PIL import Image, ImageOps
import streamlit.components.v1 as components

st.markdown("""
<style>
/* ====================== 全局基础 ====================== */
/* 整体背景：宣纸色/墨水屏底色 */
.stApp {
    background-color: #f7f6f2 !important;
}
/* 全局文字：水墨黑 */
* {
    color: #222222 !important;
}

/* ====================== 按钮 ====================== */
/* 主按钮（生成字体）：水墨黑底白字 */
.stButton > button[kind="primary"] {
    background-color: #eeeeec !important;
    color: #222222 !important;
    border: 1px solid #cccccc !important;
    border-radius: 4px !important;
}
/* 次按钮（下载字体）：灰底黑字 */
.stButton > button:not([kind="primary"]) {
    background-color: #eeeeec !important;
    color: #222222 !important;
    border: 1px solid #cccccc !important;
    border-radius: 4px !important;
}

/* ====================== 输入框/数字框 ====================== */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background-color: #eeeeec !important;
    border: 1px solid #cccccc !important;
    border-radius: 4px !important;
}

/* ====================== 复选框（2-bit灰度） ====================== */
/* 复选框框体：灰色 */
.stCheckbox [data-baseweb="checkbox"] div[role="checkbox"] {
    background-color: #aaaaaa !important;
    border-color: #666666 !important;
}
/* 复选框对勾：黑色 */
.stCheckbox [data-baseweb="checkbox"] svg {
    stroke: #222222 !important;
    fill: #222222 !important;
}

/* ====================== 进度条 ====================== */
/* 进度条填充：水墨黑 */
.stProgress > div > div > div > div {
    background-color: #444444 !important;
}
/* 进度条背景：淡墨灰 */
.stProgress > div > div > div {
    background-color: #d8d8d8 !important;
}

/* ====================== 提示框（生成完成/错误） ====================== */
/* 成功图标（原绿色对勾）：灰色 */
div[data-testid="stAlert"] svg[aria-hidden="true"] {
    fill: #666666 !important;
}
/* 错误提示（原红色）：统一灰底 */
div[data-testid="stAlert"][kind="error"] [data-testid="stMarkdownContainer"] {
    background-color: #e0e0e0 !important;
    color: #222222 !important;
    border: 1px solid #cccccc !important;
}
div[data-testid="stAlert"][kind="error"] svg[aria-hidden="true"] {
    fill: #666666 !important;
}

/* ====================== 上传组件 ====================== */
.stFileUploader > div > div {
    background-color: #eeeeec !important;
    border: 1px solid #cccccc !important;
    border-radius: 4px !important;
}

/* ====================== 预览图 ====================== */
img {
    border: 2px solid #cccccc !important;
    border-radius: 4px !important;
}

/* ====================== 标题 ====================== */
h1 {
    color: #222222 !important;
    font-family: "SimSun", "Microsoft YaHei", sans-serif !important;
}
</style>
""", unsafe_allow_html=True)


GlyphProps = namedtuple("GlyphProps", ["width", "height", "advance_x", "left", "top", "data_length", "data_offset", "code_point"])

# ------------------------------------------------------------------------------
# 读取汉字表
# ------------------------------------------------------------------------------
def load_common_chars_from_txt(filename):
    encodings = ["utf-8-sig", "utf-8", "gb18030"]
    for enc in encodings:
        try:
            with open(filename, "r", encoding=enc) as f:
                return f.read().replace("\n", "").replace(" ", "").strip()
        except:
            continue
    return None

# ------------------------------------------------------------------------------
# 基础函数
# ------------------------------------------------------------------------------
def norm_floor(val): return int(math.floor(val / 64))
def norm_ceil(val): return int(math.ceil(val / 64))

def load_glyph(code_point, font_stack):
    for face in font_stack:
        if face.get_char_index(code_point):
            face.load_char(code_point, freetype.FT_LOAD_RENDER)
            return face
    return None

def single_enclosing_interval(sorted_codes):
    if not sorted_codes:
        return []
    return [(sorted_codes[0], sorted_codes[-1])]

def rebuild_glyphs_for_intervals(glyphs, intervals):
    glyph_props_by_code = {}
    glyph_data_by_code = {}
    for g, d in glyphs:
        glyph_props_by_code[g.code_point] = g
        glyph_data_by_code[g.code_point] = d

    new_gp = []
    data_chunks = []
    data_offset = 0

    for s, e in intervals:
        for code in range(s, e + 1):
            if code in glyph_props_by_code:
                old = glyph_props_by_code[code]
                d = glyph_data_by_code[code]
                g = GlyphProps(
                    old.width,
                    old.height,
                    old.advance_x,
                    old.left,
                    old.top,
                    len(d),
                    data_offset,
                    code,
                )
                data_chunks.append(d)
                data_offset += len(d)
            else:
                # Occupy code points inside merged gaps so interval -> glyph index remains valid.
                g = GlyphProps(0, 0, 0, 0, 0, 0, 0, code)
            new_gp.append(g)

    return new_gp, b"".join(data_chunks)

# ------------------------------------------------------------------------------
# 读取字体内部名称
# ------------------------------------------------------------------------------
def get_font_family_name(face):
    try:
        return face.family_name.decode("utf-8", errors="ignore").replace(" ", "_")
    except:
        return "font"

# ------------------------------------------------------------------------------
# 超轻量预览（永不卡死）
# ------------------------------------------------------------------------------
def render_fast_preview(face, size, letter_spacing, is2bit, text, WIDTH=480, HEIGHT=800):
    face.set_char_size(size * 64, size * 64, 150, 150)
    img = Image.new('L', (WIDTH, HEIGHT), 255)
    pixels = img.load()

    x, y = 16, 40
    line_h = norm_ceil(face.size.height) + 6
    threshold = 6 if is2bit else 120

    for ch in text:
        if y >= HEIGHT - line_h: break
        if ch == "\n":
            x = 16
            y += line_h
            continue

        try:
            face.load_char(ord(ch), freetype.FT_LOAD_ADVANCE_ONLY)
            adv = norm_floor(face.glyph.advance.x) + letter_spacing
            if x + adv > WIDTH - 16:
                x = 16
                y += line_h
                if y >= HEIGHT - line_h: break

            face.load_char(ord(ch), freetype.FT_LOAD_RENDER)
            bmp = face.glyph.bitmap
            px = x + face.glyph.bitmap_left
            py = y + face.glyph.bitmap_top - bmp.rows

            w, h = bmp.width, bmp.rows
            buf = bmp.buffer
            for iy in range(h):
                for ix in range(w):
                    if buf[iy*w + ix] > threshold:
                        if 0 <= px+ix < WIDTH and 0 <= py+iy < HEIGHT:
                            pixels[px+ix, py+iy] = 0

            x += adv
        except:
            continue

    return ImageOps.expand(img, 2, 0)

# ------------------------------------------------------------------------------
# UI
# ------------------------------------------------------------------------------
st.set_page_config(page_title="字体工具", layout="wide")
st.title("Crosspoint 字体转换工具")

@st.cache_resource
def load_font(path):
    return freetype.Face(path)

uploaded = st.file_uploader("上传 TTF", type=["ttf"])
tmp_path = None
font_filename_base = "font"

# ==========================
# 读取上传文件名（自动去后缀 .ttf .otf）
# ==========================
if uploaded:
    # 获取上传文件名，例如 "NotoSans.ttf"
    font_filename_base = os.path.splitext(uploaded.name)[0]

    with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as f:
        f.write(uploaded.getvalue())
        tmp_path = f.name
char_mode = st.radio(
    "字符集选择",
    ["常用5000字（推荐）", "常用7000字","所有字体（不推荐）"],
    horizontal=True
)

is_mode_5000 = "5000" in char_mode
is_mode_7000 = "7000" in char_mode
is_mode_common = is_mode_5000 or is_mode_7000
# 布局
col1, col2, col3, col4 = st.columns(4)
with col1:
    # 这里只是显示，不影响最终文件名
    st.text_input("字体名称（自动读取）", value=font_filename_base, disabled=False)
with col2:
    size = st.number_input("字号", 8, 128, 24)
with col3:
    letter_spacing = st.number_input("字距", -10, 20, 0)
with col4:
    is2Bit = st.checkbox("2-bit 灰度", True)

# 预览文本
preview_text = """曈海客谈瀛洲 烟涛微茫信难求
越人语天姥 云霞明灭或可睹
天姥连天向天横 势拔五岳掩赤城
天台四万八千丈 对此欲倒东南倾
我欲因之梦吴越 一夜飞度镜湖月
湖月照我影 送我至剡溪
谢公宿处今尚在 渌水荡漾清猿啼"""

# 实时预览
if uploaded and tmp_path:
    face = load_font(tmp_path)
    img = render_fast_preview(face, size, letter_spacing, is2Bit, preview_text)
    st.image(img, caption="480×800 预览", width=480)

# --------------------------
# 生成逻辑（原版1:1）
if is_mode_5000:
    loadtxt="常用五千.txt"
elif is_mode_7000:
    loadtxt="常用七千.txt"
# --------------------------
if st.button("生成字体", type="primary", use_container_width=True) and uploaded:
    font_stack = [freetype.Face(tmp_path)]
    if is_mode_common:

        common = load_common_chars_from_txt(loadtxt)

        # ==============================================

        # 1. 基础区间（空格、英文、标点，必须保留，否则无法显示）
        intervals = [
            (0x20, 0x7F),
            (0x2000, 0x206F),
            (0x3000, 0x303F),
            (0xFF00, 0xFFEF)
        ]

        # 2. 【关键】从 TXT 汉字 自动生成每个字的 interval
        if common:
            for char in common:
                code_point = ord(char)       # 汉字 → 自动转码点
                intervals.append( (code_point, code_point) )

        # 3. 区间必须排序（字体格式要求）
        intervals = sorted(intervals)

        # 4. 按区间生成字形列表（严格保序、不去乱改）
        ordered = []
        seen = set()

        def add(c):
            if c not in seen:
                seen.add(c)
                ordered.append(c)

        for s, e in intervals:
            for c in range(s, e + 1):
                if load_glyph(c, font_stack):
                    add(c)

        # 5. 最终生成 interval（给设备查找用，自动从所有字生成）
        sorted_c = sorted(ordered)
        out_intervals = []
        if sorted_c:
            s = e = sorted_c[0]
            for c in sorted_c[1:]:
                if c == e + 1:
                    e = c
                else:
                    out_intervals.append( (s, e) )
                    s = e = c
            out_intervals.append( (s, e) )
    else:
        out_intervals = [
            (0x0000, 0x007F), (0x0080, 0x00FF), (0x0100, 0x017F),
            (0x2000, 0x206F), (0x2010, 0x203A), (0x2040, 0x205F),
            (0x20A0, 0x20CF), (0x0300, 0x036F), (0x0370, 0x03FF),
            (0x0400, 0x04FF), (0x2070, 0x209F), (0x2200, 0x22FF),
            (0x2190, 0x21FF), (0x4E00, 0x9FFF), (0x3400, 0x4DBF),
            (0x20000, 0x2A6DF), (0x2A700, 0x2EBEF), (0x30000, 0x3134F),
            (0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF),
            (0xFF60, 0xFF9F), (0xAC00, 0xD7AF), (0x1100, 0x11FF),
            (0x3130, 0x318F), (0xA960, 0xA97F), (0xD7B0, 0xD7FF),
            (0x2E80, 0x2EFF), (0x2F00, 0x2FDF), (0x3000, 0x303F),
            (0xFE30, 0xFE4F), (0xF900, 0xFAFF), (0xFFFD, 0xFFFD),
            (0xFF00, 0xFFEF),  # 全角标点
            # ========== 新增：中文阅读核心补充 ==========
            (0x2018, 0x201D),  # 中文弯引号（单/双）
            (0x2026, 0x2026),  # 省略号
            (0x200B, 0x200B),  # 零宽空格
            (0xFE10, 0xFE1F),  # 竖排标点
            (0x2F800, 0x2FA1F),# 古籍/繁体生僻字
        ]
        ordered = []
        seen = set()

        def add(c):
            if c not in seen:
                seen.add(c)
                ordered.append(c)

        for s, e in out_intervals:
            for c in range(s, e + 1):
                if load_glyph(c, font_stack):
                    add(c)

    for f in font_stack:
        f.set_char_size(size * 64, size * 64, 150, 150)

    total = len(ordered)
    prog = st.progress(0)
    status = st.empty()
    glyphs = []
    offset = 0

    for i, c in enumerate(ordered):
        f = load_glyph(c, font_stack)
        if not f: continue
        bmp = f.glyph.bitmap

        p4 = []
        px = 0
        for j, v in enumerate(bmp.buffer):
            xj = j % bmp.width
            if xj % 2 == 0:
                px = v >> 4
            else:
                px |= v & 0xF0
                p4.append(px)
                px = 0
            if xj == bmp.width - 1 and bmp.width % 2 == 1:
                p4.append(px)

        if is2Bit:
            res, px = [], 0
            pitch = (bmp.width // 2) + (bmp.width % 2)
            for yb in range(bmp.rows):
                for xb in range(bmp.width):
                    px <<= 2
                    v = p4[yb * pitch + (xb // 2)] >> ((xb % 2) * 4) & 0xF
                    if v >= 12: px +=3
                    elif v >=8: px +=2
                    elif v >=4: px +=1
                    if (yb * bmp.width + xb) %4 ==3:
                        res.append(px)
                        px=0
            if (bmp.width * bmp.rows) %4 !=0:
                px <<= (4 - (bmp.width*bmp.rows)%4)*2
                res.append(px)
        else:
            res, px = [],0
            pitch = (bmp.width//2)+(bmp.width%2)
            for yb in range(bmp.rows):
                for xb in range(bmp.width):
                    px <<=1
                    v = p4[yb * pitch + xb//2]
                    if ((xb%2==0 and (v&0xE)) or (xb%2==1 and (v&0xE0))):
                        px +=1
                    if (yb*bmp.width + xb)%8 ==7:
                        res.append(px)
                        px=0
            if (bmp.width*bmp.rows)%8 !=0:
                px <<= 8-(bmp.width*bmp.rows)%8
                res.append(px)

        data = bytes(res)
        g = GlyphProps(
            bmp.width, bmp.rows,
            norm_floor(f.glyph.advance.x) + letter_spacing,
            f.glyph.bitmap_left, f.glyph.bitmap_top,
            len(data), offset, c
        )
        offset += len(data)
        glyphs.append((g, data))
        prog.progress((i+1)/total)
        status.text(f"{i+1}/{total}")

    base_gp = [g for g,_ in glyphs]
    sorted_c = sorted({x.code_point for x in base_gp})
    out_intervals = single_enclosing_interval(sorted_c)
    gp, gd = rebuild_glyphs_for_intervals(glyphs, out_intervals)

    final_ram = len(out_intervals) * 12
    slot_count = len(gp)
    real_glyph_count = len(base_gp)
    placeholder_count = max(0, slot_count - real_glyph_count)
    glyph_table_bytes = slot_count * 13
    st.caption(f"Interval内存估算: 当前={len(out_intervals)}段/{final_ram}B")
    st.caption(
        f"Glyph索引估算: 实际字形={real_glyph_count}, 总槽位={slot_count}, 占位={placeholder_count}, 索引区≈{glyph_table_bytes}B"
    )
    

    ref = load_glyph(ord('|'), font_stack) or font_stack[0]
    out = BytesIO()
    H = 48
    i_len = len(out_intervals)*12
    g_len = len(gp)*13
    d_len = len(gd)

    oi = H
    og = oi + i_len
    od = og + g_len

    out.write(b'EPDF')
    out.write(struct.pack('<I', len(out_intervals)))
    out.write(struct.pack('<I', od + d_len))
    out.write(struct.pack('<I', norm_ceil(ref.size.height)))
    out.write(struct.pack('<I', len(gp)))
    out.write(struct.pack('<i', norm_ceil(ref.size.ascender)))
    out.write(struct.pack('<i', 0))
    out.write(struct.pack('<i', norm_floor(ref.size.descender)))
    out.write(struct.pack('<I', 1 if is2Bit else 0))
    out.write(struct.pack('<I', oi))
    out.write(struct.pack('<I', og))
    out.write(struct.pack('<I', od))

    idx = 0
    for s,e in out_intervals:
        out.write(struct.pack('<III', s,e,idx))
        idx += e-s+1

    for g in gp:
        out.write(struct.pack('<BBB b B b B H I',
            g.width, g.height, g.advance_x, g.left,0,g.top,0,g.data_length,g.data_offset))

    out.write(gd)
    prog.empty()
    status.success("生成完成")

    # ==========================
    # ✅ 最终文件名：字体名 + 字号
    # ==========================
    final_filename = f"{font_filename_base}{size}.epdfont"

    file_bytes = out.getvalue()
    b64 = base64.b64encode(file_bytes).decode("ascii")

    # 自动触发下载，避免二次点击。
    components.html(
        f"""
        <script>
        (function() {{
          const b64 = "{b64}";
          const binary = atob(b64);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
          const blob = new Blob([bytes], {{ type: 'application/octet-stream' }});
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = "{final_filename}";
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1500);
        }})();
        </script>
        """,
        height=0,
    )

    # 兜底：极少数浏览器可能拦截自动下载时，仍可手动点击。
    st.download_button("下载字体", file_bytes, file_name=final_filename, use_container_width=True)
    os.unlink(tmp_path)
