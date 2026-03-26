#!/usr/bin/env python3
import streamlit as st
import freetype
import math
import tempfile
import os
import struct
from collections import namedtuple
from io import BytesIO
from PIL import Image, ImageOps

st.markdown("""
<style>
/* ====================== 全局基础 ====================== */
.stApp {
    background-color: #f7f6f2 !important;
}
* {
    color: #222222 !important;
}

/* ====================== 按钮 ====================== */
.stButton > button[kind="primary"] {
    background-color: #eeeeec !important;
    color: #222222 !important;
    border: 1px solid #cccccc !important;
    border-radius: 4px !important;
}
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
.stCheckbox [data-baseweb="checkbox"] div[role="checkbox"] {
    background-color: #aaaaaa !important;
    border-color: #666666 !important;
}
.stCheckbox [data-baseweb="checkbox"] svg {
    stroke: #222222 !important;
    fill: #222222 !important;
}

/* ====================== 进度条 ====================== */
.stProgress > div > div > div > div {
    background-color: #444444 !important;
}
.stProgress > div > div > div {
    background-color: #d8d8d8 !important;
}

/* ====================== 提示框 ====================== */
div[data-testid="stAlert"] svg[aria-hidden="true"] {
    fill: #666666 !important;
}
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
# 完整默认字符集（你给的原版完整Unicode区间）
# ------------------------------------------------------------------------------
DEFAULT_INTERVALS = [
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
    (0xFF00, 0xFFEF),
    (0x2018, 0x201D),
    (0x2026, 0x2026),
    (0x200B, 0x200B),
    (0xFE10, 0xFE1F),
    (0x2F800, 0x2FA1F),
]

# ------------------------------------------------------------------------------
# 从文件读取字符
# ------------------------------------------------------------------------------
def load_chars_from_file(filename):
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

# ------------------------------------------------------------------------------
# 预览
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

if uploaded:
    font_filename_base = os.path.splitext(uploaded.name)[0]
    with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as f:
        f.write(uploaded.getvalue())
        tmp_path = f.name

# --------------------------
# ✅ 新增：字符集选择（3选1）
# --------------------------
char_mode = st.radio(
    "字符集选择",
    ["常用5000字", "常用7000字", "沿用原逻辑"],
    horizontal=True
)

# 布局
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.text_input("字体名称（自动读取）", value=font_filename_base, disabled=False)
with col2:
    size = st.number_input("字号", 8, 128, 24)
with col3:
    letter_spacing = st.number_input("字距", -10, 20, 0)
with col4:
    is2Bit = st.checkbox("2-bit 灰度", True)

preview_text = """海客谈瀛洲 烟涛微茫信难求
越人语天姥 云霞明灭或可睹
天姥连天向天横 势拔五岳掩赤城
天台四万八千丈 对此欲倒东南倾
我欲因之梦吴越 一夜飞度镜湖月
湖月照我影 送我至剡溪
谢公宿处今尚在 渌水荡漾清猿啼"""

if uploaded and tmp_path:
    face = load_font(tmp_path)
    img = render_fast_preview(face, size, letter_spacing, is2Bit, preview_text)
    st.image(img, caption="480×800 预览", width=480)

# --------------------------
# 生成逻辑
# --------------------------
if st.button("生成字体", type="primary", use_container_width=True) and uploaded:
    font_stack = [freetype.Face(tmp_path)]
    ordered = []
    seen = set()

    def add(c):
        if c not in seen:
            seen.add(c)
            ordered.append(c)

    # ==========================
    # ✅ 根据选项加载不同字符
    # ==========================
    if char_mode == "常用5000字":
        chars = load_chars_from_file("常用五千.txt")
        if chars:
            for c in chars:
                add(ord(c))
    elif char_mode == "常用7000字":
        chars = load_chars_from_file("常用七千.txt")
        if chars:
            for c in chars:
                add(ord(c))
    elif char_mode == "完整Unicode原逻辑":
        for s, e in DEFAULT_INTERVALS:
            for c in range(s, e + 1):
                if load_glyph(c, font_stack):
                    add(c)

    # 统一设置大小
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
        g = GlyphProps(bmp.width, bmp.rows, norm_floor(f.glyph.advance.x) + letter_spacing,
                      f.glyph.bitmap_left, f.glyph.bitmap_top, len(data), offset, c)
        offset += len(data)
        glyphs.append((g, data))
        prog.progress((i+1)/total)
        status.text(f"{i+1}/{total}")

    gp = [g for g,_ in glyphs]
    gd = b"".join([d for _,d in glyphs])
    sorted_c = sorted({x.code_point for x in gp})
    out_intervals = []
    if sorted_c:
        s = e = sorted_c[0]
        for c in sorted_c[1:]:
            if c == e+1:
                e = c
            else:
                out_intervals.append((s,e))
                s=e=c
        out_intervals.append((s,e))

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

    final_filename = f"{font_filename_base}{size}.epdfont"
    st.download_button("下载字体", out.getvalue(), file_name=final_filename, use_container_width=True)
    os.unlink(tmp_path)
