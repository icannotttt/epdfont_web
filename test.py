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
# 固定标准格式（和 ttf_to_epdfont.py 完全一致）
# ------------------------------------------------------------------------------
EPDFONT_MAGIC = 0x46445045
EPDFONT_VERSION = 1
HEADER_PACK = '<IHBBBBBB5I'
GLYPH_PACK = '<4B2h2I'
INTERVAL_PACK = '<3I'
HEADER_SIZE = 32
GLYPH_SIZE = 16
INTERVAL_SIZE = 12

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

# ------------------------------------------------------------------------------
# 合并连续区间（官方标准逻辑）
# ------------------------------------------------------------------------------
def merge_intervals(sorted_codes):
    if not sorted_codes:
        return []
    res = []
    cur_s = sorted_codes[0]
    cur_e = sorted_codes[0]
    for c in sorted_codes[1:]:
        if c == cur_e + 1:
            cur_e = c
        else:
            res.append((cur_s, cur_e))
            cur_s = c
            cur_e = c
    res.append((cur_s, cur_e))
    return res

# ------------------------------------------------------------------------------
# 官方标准 epdfont 写入（修复核心）
# ------------------------------------------------------------------------------
def write_standard_epdfont(out, intervals, glyph_list, bitmap_data_list, advance_y, ascender, descender, is_2bit):
    interval_cnt = len(intervals)
    glyph_cnt = len(glyph_list)
    
    off_intervals = HEADER_SIZE
    off_glyphs = off_intervals + interval_cnt * INTERVAL_SIZE
    off_bitmap = off_glyphs + glyph_cnt * GLYPH_SIZE

    header = struct.pack(
        HEADER_PACK,
        EPDFONT_MAGIC,
        EPDFONT_VERSION,
        1 if is_2bit else 0,
        0,
        advance_y & 0xFF,
        ascender & 0xFF,
        descender & 0xFF,
        0,
        interval_cnt,
        glyph_cnt,
        off_intervals,
        off_glyphs,
        off_bitmap
    )
    out.write(header)

    idx = 0
    for s, e in intervals:
        out.write(struct.pack(INTERVAL_PACK, s, e, idx))
        idx += e - s + 1

    for g in glyph_list:
        out.write(struct.pack(
            GLYPH_PACK,
            g.width, g.height, g.advance_x, 0,
            g.left, g.top, g.data_length, g.data_offset
        ))

    for d in bitmap_data_list:
        out.write(d)

# ------------------------------------------------------------------------------
# 超轻量预览
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

uploaded = st.file_uploader("上传 TTF", type=["ttf","otf"])
tmp_path = None
font_filename_base = "font"

if uploaded:
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

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.text_input("字体名称（自动读取）", value=font_filename_base, disabled=False)
with col2:
    size = st.number_input("字号", 8, 128, 18)
with col3:
    letter_spacing = st.number_input("字距", -10, 20, 0)
with col4:
    is2Bit = st.checkbox("2-bit 灰度", True)

preview_text = """海客谈瀛洲 烟涛微茫信难求
越人语天姥 云霞明灭或可睹
天姥连天向天横 势拔五岳掩赤城"""

if uploaded and tmp_path:
    face = load_font(tmp_path)
    img = render_fast_preview(face, size, letter_spacing, is2Bit, preview_text)
    st.image(img, caption="480×800 预览", width=480)

# --------------------------
# 生成逻辑（修复版，保留 txt 读取）
# --------------------------
if st.button("生成字体", type="primary", use_container_width=True) and uploaded:
    font_stack = [freetype.Face(tmp_path)]
    required_codes = set()

    # 基础必选字符：英文、标点
    base_ranges = [
        (0x20, 0x7E), (0x2000, 0x206F), (0x3000, 0x303F), (0xFF00, 0xFFEF)
    ]
    for s, e in base_ranges:
        for c in range(s, e+1):
            required_codes.add(c)

    # 从 txt 读取汉字
    if is_mode_common:
        loadtxt = "常用五千.txt" if is_mode_5000 else "常用七千.txt"
        common = load_common_chars_from_txt(loadtxt)
        if common:
            for ch in common:
                required_codes.add(ord(ch))

    # 全字体模式
    else:
        for c in range(0x4E00, 0x9FA6):
            required_codes.add(c)

    # 过滤有效字符
    ordered = []
    for c in sorted(required_codes):
        if load_glyph(c, font_stack):
            ordered.append(c)

    # 合并区间
    out_intervals = merge_intervals(ordered)

    # 渲染字形
    for f in font_stack:
        f.set_char_size(size * 64, size * 64, 150, 150)

    total = len(ordered)
    prog = st.progress(0)
    status = st.empty()
    glyph_list = []
    bitmap_list = []
    data_offset = 0

    for i, c in enumerate(ordered):
        f = load_glyph(c, font_stack)
        if not f:
            continue

        bmp = f.glyph.bitmap
        w = bmp.width
        h = bmp.rows
        buf = bmp.buffer

        if is2Bit:
            res = []
            px = 0
            cnt = 0
            for v in buf:
                g = v >> 6
                px = (px << 2) | g
                cnt += 1
                if cnt == 4:
                    res.append(px)
                    px = 0
                    cnt = 0
            if cnt > 0:
                px <<= (4 - cnt) * 2
                res.append(px)
        else:
            res = []
            px = 0
            cnt = 0
            for v in buf:
                px = (px << 1) | (1 if v >= 128 else 0)
                cnt += 1
                if cnt == 8:
                    res.append(px)
                    px = 0
                    cnt = 0
            if cnt > 0:
                px <<= (8 - cnt)
                res.append(px)

        data = bytes(res)
        g = GlyphProps(
            width=w,
            height=h,
            advance_x=norm_floor(f.glyph.advance.x) + letter_spacing,
            left=f.glyph.bitmap_left,
            top=f.glyph.bitmap_top,
            data_length=len(data),
            data_offset=data_offset,
            code_point=c
        )
        glyph_list.append(g)
        bitmap_list.append(data)
        data_offset += len(data)
        prog.progress((i+1)/total)
        status.text(f"{i+1}/{total}")

    # 全局参数
    ref = load_glyph(ord('A'), font_stack) or load_glyph(ord('|'), font_stack) or font_stack[0]
    advance_y = norm_ceil(ref.size.height)
    ascender = norm_ceil(ref.size.ascender)
    descender = norm_floor(ref.size.descender)

    # 输出标准 epdfont
    out = BytesIO()
    write_standard_epdfont(out, out_intervals, glyph_list, bitmap_list, advance_y, ascender, descender, is2Bit)

    prog.empty()
    status.success("生成完成（标准兼容版）")
    final_filename = f"{font_filename_base}{size}.epdfont"
    file_bytes = out.getvalue()
    b64 = base64.b64encode(file_bytes).decode()

    # 自动下载
    components.html(f"""
    <script>
    const b64="{b64}";const bin=atob(b64);const arr=new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++)arr[i]=bin.charCodeAt(i);
    const blob=new Blob([arr],{{type:'application/octet-stream'}});
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download="{final_filename}";
    document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    </script>
    """, height=0)

    st.download_button("下载字体", file_bytes, final_filename, use_container_width=True)
    os.unlink(tmp_path)
