#!/usr/bin/env python3
import streamlit as st
import freetype
import math
import tempfile
import os
import struct
from collections import namedtuple
from io import BytesIO

# --- 数据结构 ---
GlyphProps = namedtuple("GlyphProps", ["width", "height", "advance_x", "left", "top", "data_length", "data_offset", "code_point"])

# --- 默认 Unicode 范围（与原脚本一致）---
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
    (0xFF00, 0xFFEF),  # 全角标点
    # ========== 新增：中文阅读核心补充 ==========
    (0x2018, 0x201D),  # 中文弯引号（单/双）
    (0x2026, 0x2026),  # 省略号
    (0x200B, 0x200B),  # 零宽空格
    (0xFE10, 0xFE1F),  # 竖排标点
    (0x2F800, 0x2FA1F),# 古籍/繁体生僻字
]

# --- 辅助函数 ---
def norm_floor(val):
    return int(math.floor(val / (1 << 6)))

def norm_ceil(val):
    return int(math.ceil(val / (1 << 6)))

def norm_round(val):
    return int(round(val / 64.0))

def chunks(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]

def _load_glyph(code_point, font_stack):
    for face in font_stack:
        glyph_index = face.get_char_index(code_point)
        if glyph_index > 0:
            face.load_glyph(glyph_index, freetype.FT_LOAD_RENDER)
            return face
    return None

# --- Streamlit App ---
st.set_page_config(page_title="EPDiy 字体转换工具（网页版）", layout="wide")
st.title("🖨️ EPDiy 字体转换工具（支持中文 & 多字体）")
st.caption("将 TTF/OTF 字体转换为 EPDiy 可用的 .epdfont 或 C 头文件")

# 初始化 session state
if "intervals" not in st.session_state:
    st.session_state.intervals = []

# --- UI 输入 ---
col1, col2 = st.columns(2)

with col1:
    name = st.text_input("字体名称", value="MyFont", help="用于生成变量名和文件名")
    size = st.number_input("字号（像素）", min_value=8, max_value=256, value=24, step=1)
    is2bit = st.checkbox("生成 2-bit 灰度字体（默认为 1-bit 黑白）")
    is_binary = st.checkbox("输出二进制 .epdfont 文件（否则输出 C 头文件）")

uploaded_fonts = st.file_uploader(
    "📁 上传字体文件（支持 .ttf / .otf / .ttc，可多选）",
    type=["ttf", "otf", "ttc"],
    accept_multiple_files=True
)

# --- 额外 Unicode 区间 ---
st.subheader("🔤 额外 Unicode 区间（可选）")
extra_interval = st.text_input(
    "格式：0x3100,0x312F 或 12544,12591",
    placeholder="例如：0x3100,0x312F"
)

if st.button("➕ 添加区间"):
    if extra_interval.strip():
        try:
            parts = extra_interval.split(',')
            if len(parts) != 2:
                raise ValueError("必须包含两个值")
            start = int(parts[0], 0)
            end = int(parts[1], 0)
            if start > end:
                raise ValueError("起始值不能大于结束值")
            st.session_state.intervals.append((start, end))
            st.success(f"已添加区间: U+{start:04X} – U+{end:04X}")
        except Exception as e:
            st.error(f"❌ 区间格式错误: {e}")

# 显示已添加的区间
if st.session_state.intervals:
    st.write("当前自定义区间:")
    for i, (s, e) in enumerate(st.session_state.intervals[:]):
        cols = st.columns([5, 1])
        cols[0].text(f"U+{s:04X} – U+{e:04X}")
        if cols[1].button("🗑️", key=f"del_{i}"):
            st.session_state.intervals.pop(i)
            st.rerun()

# --- 执行转换 ---
if st.button("🚀 开始生成字体", type="primary", use_container_width=True):
    if not name.strip():
        st.error("❌ 请输入有效的字体名称！")
    elif not uploaded_fonts:
        st.error("❌ 请至少上传一个字体文件！")
    else:
        with st.spinner("⏳ 正在处理字体...（可能需要几秒到几十秒）"):
            try:
                # 1. 加载字体到内存（使用临时文件）
                font_stack = []
                temp_paths = []

                for uf in uploaded_fonts:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tmp:
                        tmp.write(uf.getvalue())
                        tmp_path = tmp.name
                        temp_paths.append(tmp_path)
                    face = freetype.Face(tmp_path)
                    font_stack.append(face)

                # 2. 合并区间
                intervals = DEFAULT_INTERVALS + st.session_state.intervals
                unmerged = sorted(intervals)
                merged = []
                for start, end in unmerged:
                    if merged and start <= merged[-1][1] + 1:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                    else:
                        merged.append((start, end))
                intervals = merged

                # 3. 过滤有效字形
                valid_intervals = []
                for i_start, i_end in intervals:
                    start = i_start
                    for cp in range(i_start, i_end + 1):
                        face = _load_glyph(cp, font_stack)
                        if face is None:
                            if start <= cp - 1:
                                valid_intervals.append((start, cp - 1))
                            start = cp + 1
                    if start <= i_end:
                        valid_intervals.append((start, i_end))
                intervals = valid_intervals

                # 4. 设置字号
                for face in font_stack:
                    face.set_char_size(size << 6, size << 6, 150, 150)

                # 5. 渲染所有字形
                total_size = 0
                all_glyphs = []

                for i_start, i_end in intervals:
                    for code_point in range(i_start, i_end + 1):
                        face = _load_glyph(code_point, font_stack)
                        if face is None:
                            continue
                        bitmap = face.glyph.bitmap

                        # 构建 4-bit 灰度像素
                        pixels4g = []
                        px = 0
                        for i, v in enumerate(bitmap.buffer):
                            x = i % bitmap.width
                            if x % 2 == 0:
                                px = (v >> 4)
                            else:
                                px = px | (v & 0xF0)
                                pixels4g.append(px)
                                px = 0
                            if x == bitmap.width - 1 and bitmap.width % 2 == 1:
                                pixels4g.append(px)
                                px = 0

                        if is2bit:
                            pixels2b = []
                            px = 0
                            pitch = (bitmap.width + 1) // 2
                            for y in range(bitmap.rows):
                                for x in range(bitmap.width):
                                    px <<= 2
                                    bm = pixels4g[y * pitch + (x // 2)]
                                    bm = (bm >> ((x % 2) * 4)) & 0xF
                                    if bm >= 12:
                                        px |= 3
                                    elif bm >= 8:
                                        px |= 2
                                    elif bm >= 4:
                                        px |= 1
                                    if (y * bitmap.width + x) % 4 == 3:
                                        pixels2b.append(px)
                                        px = 0
                            if (bitmap.width * bitmap.rows) % 4 != 0:
                                px <<= (4 - (bitmap.width * bitmap.rows) % 4) * 2
                                pixels2b.append(px)
                            pixels = pixels2b
                        else:
                            pixelsbw = []
                            px = 0
                            pitch = (bitmap.width + 1) // 2
                            for y in range(bitmap.rows):
                                for x in range(bitmap.width):
                                    px <<= 1
                                    bm = pixels4g[y * pitch + (x // 2)]
                                    is_black = ((x % 2 == 0 and (bm & 0xE) > 0) or
                                                (x % 2 == 1 and (bm & 0xE0) > 0))
                                    px |= 1 if is_black else 0
                                    if (y * bitmap.width + x) % 8 == 7:
                                        pixelsbw.append(px)
                                        px = 0
                            if (bitmap.width * bitmap.rows) % 8 != 0:
                                px <<= 8 - (bitmap.width * bitmap.rows) % 8
                                pixelsbw.append(px)
                            pixels = pixelsbw

                        packed = bytes(pixels)
                        glyph = GlyphProps(
                            width=bitmap.width,
                            height=bitmap.rows,
                            advance_x=norm_round(face.glyph.advance.x),
                            left=face.glyph.bitmap_left,
                            top=face.glyph.bitmap_top,
                            data_length=len(packed),
                            data_offset=total_size,
                            code_point=code_point,
                        )
                        total_size += len(packed)
                        all_glyphs.append((glyph, packed))

                # 6. 获取参考字形（用于高度/ascender/descender）
                ref_face = _load_glyph(ord('|'), font_stack)
                if ref_face is None:
                    ref_face = font_stack[0]

                # 7. 准备数据
                glyph_data = []
                glyph_props = []
                for g, data in all_glyphs:
                    glyph_data.extend(data)
                    glyph_props.append(g)

                # 8. 生成输出
                output_filename = name + (".epdfont" if is_binary else ".h")
                output_buffer = BytesIO()

                if is_binary:
                    header_size = 48
                    intervals_size = len(intervals) * 12
                    glyphs_size = len(glyph_props) * 13
                    bitmaps_size = len(glyph_data)
                    offset_intervals = header_size
                    offset_glyphs = offset_intervals + intervals_size
                    offset_bitmaps = offset_glyphs + glyphs_size
                    file_size = offset_bitmaps + bitmaps_size

                    output_buffer.write(b"EPDF")
                    output_buffer.write(struct.pack("<I", len(intervals)))
                    output_buffer.write(struct.pack("<I", file_size))
                    output_buffer.write(struct.pack("<I", norm_ceil(ref_face.size.height)))
                    output_buffer.write(struct.pack("<I", len(glyph_props)))
                    output_buffer.write(struct.pack("<i", norm_ceil(ref_face.size.ascender)))
                    output_buffer.write(struct.pack("<i", 0))
                    output_buffer.write(struct.pack("<i", norm_floor(ref_face.size.descender)))
                    output_buffer.write(struct.pack("<I", 1 if is2bit else 0))
                    output_buffer.write(struct.pack("<I", offset_intervals))
                    output_buffer.write(struct.pack("<I", offset_glyphs))
                    output_buffer.write(struct.pack("<I", offset_bitmaps))

                    current_offset = 0
                    for i_start, i_end in intervals:
                        output_buffer.write(struct.pack("<III", i_start, i_end, current_offset))
                        current_offset += i_end - i_start + 1

                    for g in glyph_props:
                        output_buffer.write(struct.pack("<BBB b B b B H I",
                            g.width, g.height, g.advance_x,
                            g.left, 0, g.top, 0,
                            g.data_length, g.data_offset))

                    output_buffer.write(bytes(glyph_data))
                else:
                    lines = []
                    lines.append(f"/**\n * 由 EPDiy 字体转换工具生成\n * 字体名称: {name}\n * 字号: {size}\n * 模式: {'2-bit 灰度' if is2bit else '1-bit 黑白'}\n */")
                    lines.append("#pragma once")
                    lines.append('#include "EpdFontData.h"\n')

                    lines.append(f"static const uint8_t {name}Bitmaps[{len(glyph_data)}] = {{")
                    for c in chunks(glyph_data, 16):
                        line = "    " + " ".join(f"0x{b:02X}," for b in c)
                        lines.append(line)
                    lines.append("};\n")

                    lines.append(f"static const EpdGlyph {name}Glyphs[] = {{")
                    for g in glyph_props:
                        char_repr = repr(chr(g.code_point)) if 32 <= g.code_point <= 126 else f"U+{g.code_point:04X}"
                        line = f"    {{ {g.width}, {g.height}, {g.advance_x}, {g.left}, 0, {g.top}, 0, {g.data_length}, {g.data_offset} }}, // {char_repr}"
                        lines.append(line)
                    lines.append("};\n")

                    lines.append(f"static const EpdUnicodeInterval {name}Intervals[] = {{")
                    offset = 0
                    for i_start, i_end in intervals:
                        line = f"    {{ 0x{i_start:X}, 0x{i_end:X}, 0x{offset:X} }},"
                        lines.append(line)
                        offset += i_end - i_start + 1
                    lines.append("};\n")

                    lines.append(f"static const EpdFontData {name} = {{")
                    lines.append(f"    {name}Bitmaps,")
                    lines.append(f"    {name}Glyphs,")
                    lines.append(f"    {name}Intervals,")
                    lines.append(f"    {len(intervals)},")
                    lines.append(f"    {norm_ceil(ref_face.size.height)},")
                    lines.append(f"    {norm_ceil(ref_face.size.ascender)},")
                    lines.append(f"    {norm_floor(ref_face.size.descender)},")
                    lines.append(f"    {'true' if is2bit else 'false'},")
                    lines.append("};")

                    output_buffer.write("\n".join(lines).encode("utf-8"))

                # 9. 清理临时文件
                for p in temp_paths:
                    try:
                        os.unlink(p)
                    except:
                        pass

                # 10. 提供下载
                st.success("✅ 字体生成成功！")
                st.download_button(
                    label=f"📥 下载 {output_filename}",
                    data=output_buffer.getvalue(),
                    file_name=output_filename,
                    mime="application/octet-stream" if is_binary else "text/plain",
                    use_container_width=True
                )

            except Exception as e:
                st.error(f"❌ 转换失败: {str(e)}")
                st.exception(e)  # 开发时可保留，生产可移除

st.markdown("---")
st.caption("© 2026 基于 EPDiy 字体工具改造 | 支持中文路径与复杂排版")