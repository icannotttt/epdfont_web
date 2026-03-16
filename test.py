#!/usr/bin/env python3
import streamlit as st
import freetype
import math
import tempfile
import os
import struct
from collections import namedtuple
from io import BytesIO
import re
from PIL import Image, ImageOps

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

def _set_face_size_by_visual_height(face, target_px):
    target_px = max(1, int(target_px))
    effective_px = target_px
    probe_code_points = [ord("中"), ord("M"), ord("A"), ord("0"), ord("|")]

    for _ in range(4):
        face.set_pixel_sizes(0, effective_px)

        measured_height = 0
        for cp in probe_code_points:
            glyph_index = face.get_char_index(cp)
            if glyph_index > 0:
                face.load_glyph(glyph_index, freetype.FT_LOAD_RENDER)
                measured_height = int(face.glyph.bitmap.rows)
                if measured_height > 0:
                    break

        if measured_height <= 0:
            return
        if abs(measured_height - target_px) <= 1:
            return

        next_effective = max(1, int(round(effective_px * target_px / measured_height)))
        if next_effective == effective_px:
            return
        effective_px = next_effective

    face.set_pixel_sizes(0, effective_px)

def _get_glyph_with_fallback(cp, font_stack):
    face = _load_glyph(cp, font_stack)
    if face is not None:
        return face
    return _load_glyph(ord('?'), font_stack)

def _layout_text_like_epd(text, font_stack, ascender, advance_y, width, height):
    placements = []
    min_x = 0
    min_y = 0
    max_x = 0
    max_y = 0

    cursor_x = 0
    y_top = 0

    def move_to_next_line():
        nonlocal cursor_x, y_top
        cursor_x = 0
        y_top += int(advance_y)

    for ch in text:
        if ch == "\n":
            move_to_next_line()
            if y_top >= height:
                break
            continue

        face = _get_glyph_with_fallback(ord(ch), font_stack)
        if face is None:
            continue

        glyph = face.glyph
        bitmap = glyph.bitmap
        advance_x = norm_round(glyph.advance.x)
        glyph_left = cursor_x + glyph.bitmap_left
        glyph_right = glyph_left + bitmap.width

        if cursor_x > 0 and glyph_right > width:
            move_to_next_line()
            if y_top >= height:
                break
            glyph_left = cursor_x + glyph.bitmap_left
            glyph_right = glyph_left + bitmap.width

        baseline_y = y_top + int(ascender)
        glyph_top = baseline_y - glyph.bitmap_top
        if glyph_top >= height:
            break

        placements.append({
            "cursor_x": cursor_x,
            "baseline_y": baseline_y,
            "left": glyph.bitmap_left,
            "top": glyph.bitmap_top,
            "width": bitmap.width,
            "rows": bitmap.rows,
            "pitch": abs(bitmap.pitch),
            "buffer": bytes(bitmap.buffer),
        })
        min_x = min(min_x, glyph_left)
        max_x = max(max_x, glyph_right)
        min_y = min(min_y, baseline_y + glyph.bitmap_top - bitmap.rows)
        max_y = max(max_y, baseline_y + glyph.bitmap_top)
        cursor_x += advance_x

    return placements, max_x - min_x, max_y - min_y

def _calc_text_bounds_like_epd(text, font_stack, ascender, advance_y, width, height):
    _, text_w, text_h = _layout_text_like_epd(text, font_stack, ascender, advance_y, width, height)
    return text_w, text_h

def _render_preview_like_device(text, font_stack, ascender, advance_y, is2bit, width=480, height=800):
    placements, _, _ = _layout_text_like_epd(text, font_stack, ascender, advance_y, width, height)
    min_x = 0
    image = Image.new("L", (width, height), 255)
    pixels = image.load()

    for placement in placements:
        left = placement["left"]
        top = placement["top"]
        pitch = placement["pitch"]
        cursor_x = placement["cursor_x"]
        baseline_y = placement["baseline_y"]
        glyph_width = placement["width"]
        glyph_rows = placement["rows"]
        glyph_buffer = placement["buffer"]

        for gy in range(glyph_rows):
            row_start = gy * pitch
            for gx in range(glyph_width):
                coverage = glyph_buffer[row_start + gx]
                draw_on = False

                if is2bit:
                    level4 = coverage >> 4
                    draw_on = level4 >= 4
                else:
                    draw_on = coverage > 0

                if not draw_on:
                    continue

                sx = cursor_x + left + gx
                sy = baseline_y - top + gy
                if 0 <= sx < width and 0 <= sy < height:
                    pixels[sx, sy] = 0

    return image

# --- Streamlit App ---
st.set_page_config(page_title="crosspoint字体转换工具（网页版）", layout="wide")
st.title("crosspoint 字体转换工具（网页版）")
st.caption("将 TTF字体转换为 crosspoint 可用的 .epdfont 文件")

# 初始化 session state
if "intervals" not in st.session_state:
    st.session_state.intervals = []
if "show_preview" not in st.session_state:
    st.session_state.show_preview = False

# --- UI 输入 ---
uploaded_fonts = st.file_uploader(
    "📁 上传字体文件（支持 .ttf ，仅单选）",
    type=["ttf"],
    accept_multiple_files=False
)

font_loaded_ok = False
font_load_err = ""
if uploaded_fonts is not None:
    temp_validate_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tmp_font:
            tmp_font.write(uploaded_fonts.getvalue())
            temp_validate_path = tmp_font.name
        freetype.Face(temp_validate_path)
        font_loaded_ok = True
    except Exception as ex:
        font_load_err = str(ex)
    finally:
        if temp_validate_path and os.path.exists(temp_validate_path):
            try:
                os.unlink(temp_validate_path)
            except:
                pass

# 自动设置默认字体名称（取第一个文件名，不含扩展名）
default_name = "MyFont"
if uploaded_fonts is not None:
    first_file = uploaded_fonts.name
    # 去掉扩展名
    if "." in first_file:
        base_name = first_file.rsplit(".", 1)[0]
    else:
        base_name = first_file
    # 仅保留中文和英文字母
    cleaned = re.sub(r'[^\u4e00-\u9fffA-Za-z]', '', base_name)
    # 如果清洗后为空，回退到默认名
    default_name = cleaned if cleaned else "MyFont"

col1, col2 = st.columns(2)
with col1:
    size = st.number_input("字号", min_value=8, max_value=256, value=24, step=1)
    default_name= f"{default_name}{size}"
    name = st.text_input("字体名称（用于生成文件名）", value=default_name, help="默认为上传的第一个字体文件名（不含扩展名）")
    
    is2bit = st.checkbox("生成 2-bit 灰度字体（默认开启）", value=True)

if uploaded_fonts is not None:
    if font_loaded_ok:
        st.success("✅ 字体读取成功")
    else:
        st.error(f"❌ 字体读取失败: {font_load_err}")

preview_text = st.text_area(
    "预览文本",
    value="海客谈瀛洲，烟涛微茫信难求。\n越人语天姥，云霞明灭或可睹。\n天姥连天向天横，势拔五岳掩赤城。\n天台四万八千丈，对此欲倒东南倾。\n我欲因之梦吴越，一夜飞度镜湖月。\n湖月照我影，送我至剡溪。\n谢公宿处今尚在，渌水荡漾清猿啼。",
    height=180
)
    
if font_loaded_ok and st.button("预览大小（仅参考）", use_container_width=True):
    st.session_state.show_preview = True

if st.session_state.show_preview and uploaded_fonts is not None and font_loaded_ok:
    tmp_preview_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tmp_preview_font:
            tmp_preview_font.write(uploaded_fonts.getvalue())
            tmp_preview_path = tmp_preview_font.name

        preview_face = freetype.Face(tmp_preview_path)
        preview_stack = [preview_face]
        preview_face.set_char_size(size << 6, size << 6, 150, 150)

        ascender = norm_ceil(preview_face.size.ascender)
        advance_y = max(1, norm_ceil(preview_face.size.height))

        preview_image = _render_preview_like_device(
            preview_text,
            preview_stack,
            ascender=ascender,
            advance_y=advance_y,
            is2bit=is2bit,
            width=480,
            height=800,
        )
        preview_with_border = ImageOps.expand(preview_image, border=3, fill=0)
        text_w, text_h = _calc_text_bounds_like_epd(preview_text, preview_stack, ascender, advance_y, 480, 800)

        st.image(preview_with_border, caption="实机近似预览", width=243)
        st.caption(
            f"预览度量：bbox={text_w}×{text_h}px | ascender={ascender}px | lineHeight(advanceY)={advance_y}px"
        )
    except Exception as preview_ex:
        st.error(f"❌ 预览生成失败: {preview_ex}")
    finally:
        if tmp_preview_path and os.path.exists(tmp_preview_path):
            try:
                os.unlink(tmp_preview_path)
            except:
                pass
    

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
        try:
            # 1. 加载字体到内存（使用临时文件）
            if uploaded_fonts is None:
                st.error("❌ 请上传一个字体文件！")
                st.stop()

            # 只处理这一个文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tmp:
                tmp.write(uploaded_fonts.getvalue())  # ← 直接用 uploaded_fonts，不是 uf
                tmp_path = tmp.name

            face = freetype.Face(tmp_path)
            temp_paths = [tmp_path]  # 仍用列表方便后面统一清理
            font_stack = [face]
            # 注意：不再有 font_stack，只有一个 face

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

            # 5. 统计总字形数（用于进度条）
            total_glyphs = sum(i_end - i_start + 1 for i_start, i_end in intervals)
            processed = 0
            progress_bar = st.progress(0)
            status_text = st.empty()

            # 6. 渲染所有字形
            total_size = 0
            all_glyphs = []

            for i_start, i_end in intervals:
                for code_point in range(i_start, i_end + 1):
                    face = _load_glyph(code_point, font_stack)
                    if face is None:
                        processed += 1
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

                    processed += 1
                    progress = min(1.0, processed / total_glyphs)
                    progress_bar.progress(progress)
                    status_text.text(f"正在处理字形... ({processed}/{total_glyphs})")

            # 7. 获取参考字形（用于高度/ascender/descender）
            ref_face = _load_glyph(ord('|'), font_stack)
            if ref_face is None:
                ref_face = font_stack[0]

            # 8. 准备数据
            glyph_data = []
            glyph_props = []
            for g, data in all_glyphs:
                glyph_data.extend(data)
                glyph_props.append(g)

            # 9. 生成 .epdfont 二进制文件（强制）
            output_filename = name + ".epdfont"
            output_buffer = BytesIO()

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

            # 10. 清理临时文件
            for p in temp_paths:
                try:
                    os.unlink(p)
                except:
                    pass

            # 11. 提供下载
            progress_bar.empty()
            status_text.empty()
            st.success("✅ 字体生成成功！")
            st.download_button(
                label=f"📥 下载 {output_filename}",
                data=output_buffer.getvalue(),
                file_name=output_filename,
                mime="application/octet-stream",
                use_container_width=True
            )

        except Exception as e:
            st.error(f"❌ 转换失败: {str(e)}")
            st.exception(e)

st.markdown("---")
st.caption("© 2026 基于 crosspoint 字体工具改造 | 仅输出 .epdfont 二进制格式")
