"""知核 · 工具包批量上传的标准格式定义与工具。

职责：
- 生成可直接下载的标准模板 ZIP（toolkits.json + covers/ + resources/ + lessons/）
- 生成配套的本地转换 Skill 包（Claude Code 技能）与转换提示词
- 解析并校验用户上传的批量 ZIP（含 zip-slip 路径防护）
"""
import io
import json
import posixpath
import zipfile
from decimal import Decimal

MAX_BATCH_ITEMS = 50
MAX_ITEM_BYTES = 40 * 1024 * 1024  # 单个封面/资源文件大小上限
MAX_LESSON_BYTES = 1024 * 1024  # 单节课程正文上限 1MB
MAX_ZIP_UNCOMPRESSED = 300 * 1024 * 1024  # 解压总大小预算（防 zip 炸弹）
MAX_ZIP_MEMBERS = 2000  # 文件数量上限
CATEGORIES = ["生信分析", "单细胞", "空间组学", "结构生物学", "基因编辑"]
LEVELS = ["入门", "进阶", "专业"]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
REJECTED_RESOURCE_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".vbs", ".jse", ".wsf", ".ps1", ".dll", ".msi", ".hta"}

MANIFEST_NAME = "toolkits.json"

# ---------------------------------------------------------------- 示例数据

SAMPLE_ITEM = {
    "title": "示例工具包：GSEA 富集分析与发表级绘图",
    "summary": "规范完成基因集富集分析，并输出可直接用于论文的图表。",
    "description": "本工具包将 GSEA 分析拆解为可复现的操作步骤，包含示例基因集、参数说明与绘图脚本。",
    "category": "生信分析",
    "level": "入门",
    "price": 0,
    "tags": "GSEA,富集分析,绘图",
    "cover": "covers/demo-cover.svg",
    "resource": "resources/gsea_helper.sh",
    "lessons": [
        {
            "title": "环境准备与示例数据",
            "contentFile": "lessons/01-prepare.md",
            "duration": 8,
            "preview": True,
        },
        {
            "title": "运行 GSEA 并解读结果",
            "content": "## 运行 GSEA\n\n加载基因集与表达矩阵，按推荐参数运行，重点关注 NES 与 FDR q-value。\n\n### 操作要点\n\n1. 检查基因集格式（gmt）\n2. 固定随机种子保证可复现\n3. 导出 leading edge 基因用于后续绘图",
            "duration": 12,
        },
    ],
}

DEMO_COVER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500" viewBox="0 0 800 500">
  <rect width="800" height="500" fill="#0a1f1a"/>
  <circle cx="640" cy="90" r="140" fill="#b9f36a" opacity="0.15"/>
  <text x="60" y="250" font-family="sans-serif" font-size="52" font-weight="bold" fill="#b9f36a">知核 ZHIHE</text>
  <text x="60" y="300" font-family="sans-serif" font-size="26" fill="#ffffff" opacity="0.7">示例封面 · 请替换为 covers/ 下的 jpg / png / svg</text>
</svg>
"""

DEMO_RESOURCE_SH = """#!/usr/bin/env bash
# 示例配套资源：请替换为你的脚本 / 压缩包 / 示例数据
echo "zhihe demo resource"
"""

DEMO_LESSON_MD = """## 环境准备与示例数据

1. 安装 GSEA 桌面版或 `clusterProfiler`（R）。
2. 准备表达矩阵（gene_symbol × sample）与 gmt 基因集。
3. 检查注释一致性，剔除 NA 值。

> 提示：本文件（lessons/01-prepare.md）会作为第一步的试看内容展示给学习者。
"""

README_TXT = """知核 · 工具包批量上传标准格式
================================

目录结构（ZIP 根目录，或仅套一层任意名字的文件夹）：

  toolkits.json        必需：工具包清单（UTF-8 JSON 数组，见下方字段说明）
  covers/              可选：封面图片（jpg / png / svg）
  resources/           可选：配套资源文件（脚本、压缩包、示例数据，单个 ≤ 40MB）
  lessons/             可选：课程正文 Markdown，用 contentFile 引用
  README.txt           本说明文件

toolkits.json 字段说明（数组每一项 = 一个工具包）：

  title        必填  工具包名称（≤120 字）
  summary      选填  一句话简介（≤500 字；缺省自动截取 description）
  description  必填  详细介绍（Markdown 文本）
  category     选填  分类，可选值：生信分析 / 单细胞 / 空间组学 / 结构生物学 / 基因编辑（默认 生信分析）
  level        选填  难度，可选值：入门 / 进阶 / 专业（默认 入门）
  price        选填  定价（元，≥0；0 表示免费，默认 0）
  tags         选填  标签，字符串或字符串数组
  cover        选填  封面：ZIP 内相对路径（如 covers/a.png）或 http(s) 图片 URL
  resource     选填  配套资源：ZIP 内相对路径（如 resources/data.zip）
  lessons      选填  教程步骤数组，每项：
      title        必填  步骤标题（≤160 字）
      content      二选一 步骤正文（Markdown）
      contentFile  二选一 指向 ZIP 内 Markdown 文件的相对路径
      duration     选填  预计学习分钟数（1-600，默认 10）
      image        选填  步骤配图：ZIP 内相对路径或图片 URL
      preview      选填  是否作为试看内容（默认仅第一个步骤试看，可显式 true/false）

文件限制：
  - 封面与步骤配图仅支持 jpg / jpeg / png / webp / gif / svg
  - 配套资源不接受可执行类型（exe / bat / cmd / scr / vbs / ps1 / dll / msi / hta 等）
  - 单节课程正文（content / contentFile）最大 1MB
  - 单个文件 ≤ 40MB；压缩包解压后总大小 ≤ 300MB、文件数 ≤ 2000

校验规则：
  - 一次最多上传 50 个工具包；定价 0～9999 元
  - 引用文件必须使用 ZIP 内相对路径，不允许 ".." 或绝对路径
  - 与你在平台上已发布的工具包同名的条目会被跳过
  - 推荐位（首页精选）仅由平台管理员设置，清单中的 featured 字段会被忽略

上传入口：知核 → 我的 → 我的工具包 → 批量上传
"""

# ---------------------------------------------------------------- 提示词

PROMPT_TEXT = f"""你是一名科研内容工程师。请把我在本地提供的教程资料（Markdown 笔记、脚本、示例数据等）转换成「知核科研工具平台」的批量上传标准格式，最终输出一个 ZIP。

转换要求：
1. 把资料按主题拆分为若干工具包；每个工具包生成 lessons（3-8 步，每步含 title、Markdown 正文 content 或写入文件后用 contentFile 引用、duration 分钟数）。第一步标记 "preview": true。
2. toolkits.json 是 UTF-8 JSON 数组，字段规则：
   - title 必填 ≤120 字；description 必填（详细介绍，Markdown）；summary ≤500 字（缺省可省略，平台会自动截取 description）
   - category 只能取：{ " / ".join(CATEGORIES) }
   - level 只能取：{ " / ".join(LEVELS) }
   - price 为 0～9999 的数字（0 = 免费）；tags 用英文逗号分隔
   - cover / image 仅支持 jpg / jpeg / png / webp / gif / svg；resource 不能是可执行文件类型（exe/bat/ps1/dll 等）；单节正文 ≤1MB
   - cover / resource / contentFile / image 一律使用 ZIP 内相对路径（如 covers/a.png、lessons/01.md），或 http(s) 图片 URL；禁止 ".." 与绝对路径
   - featured 字段无效（推荐位仅管理员可设置），不要写入清单
3. 目录结构：toolkits.json 放在 ZIP 根目录；封面放 covers/，配套资源（脚本、压缩包、示例数据，单个 ≤40MB）放 resources/，长正文放 lessons/。
4. 一次最多 50 个工具包。
5. 转换完成后自检：JSON 可解析、必填字段齐全、引用文件都存在；能运行则运行随 Skill 提供的 scripts/build_batch.py validate。
6. 打包为 zip（保持上述根目录结构），并提醒用户：在知核「我的 → 我的工具包 → 批量上传」页面上传该 ZIP，上传后查看逐条结果报告。

若我提供了 build_batch.py，优先用它 pack 命令打包。现在开始处理我给出的资料。"""

# ---------------------------------------------------------------- 模板/Skill ZIP

def _clean_manifest() -> bytes:
    return json.dumps([SAMPLE_ITEM], ensure_ascii=False, indent=2).encode("utf-8")


def build_template_zip() -> io.BytesIO:
    """生成标准格式示例模板包。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, _clean_manifest())
        zf.writestr("covers/demo-cover.svg", DEMO_COVER_SVG)
        zf.writestr("resources/gsea_helper.sh", DEMO_RESOURCE_SH)
        zf.writestr("lessons/01-prepare.md", DEMO_LESSON_MD)
        zf.writestr("README.txt", README_TXT)
    buf.seek(0)
    return buf


SKILL_MD = f"""---
name: zhihe-toolkit-converter
description: 将本地科研教程资料（Markdown 笔记、脚本、示例数据）整理并转换为知核平台批量上传标准格式 toolkits.json，校验后打包成 ZIP。当用户提到知核、批量上传工具包、toolkits.json 或转换科研教程时使用。
---

# 知核工具包转换器

把用户提供的科研教程资料转换成知核平台「批量上传」标准格式，产出一个 ZIP。

## 步骤

1. 阅读用户指定的资料目录（或对话中粘贴的内容）。按主题聚类为工具包；每个工具包整理出 3-8 个教程步骤（title + Markdown 正文 + duration 分钟数），第一步设 `"preview": true`。
2. 在工作目录生成如下结构（格式规范详见 references/format.md，可直接复制本 Skill 目录下 toolkits.template.json 起步）：

   toolkits.json          清单（UTF-8 JSON 数组）
   covers/                封面图片（jpg / png / svg，可选）
   resources/             配套资源文件（脚本 / 压缩包 / 示例数据，单个 ≤40MB，可选）
   lessons/               长正文 Markdown（用 contentFile 引用，可选）

3. 字段硬约束：title ≤120 字且必填；description 必填；summary ≤500 字；category ∈ { " / ".join(CATEGORIES) }；level ∈ { " / ".join(LEVELS) }；price 0～9999；单节正文 ≤1MB；图片仅 jpg/jpeg/png/webp/gif/svg；资源不能是可执行类型；一次最多 50 个工具包；文件引用只用相对路径；featured 字段无效（推荐位由管理员设置）。
4. 运行 `python3 scripts/build_batch.py validate <工作目录>` 自检；有问题就修正后重新校验，直到通过。
5. 运行 `python3 scripts/build_batch.py pack <工作目录> zhihe-batch.zip` 打包。
6. 告知用户：打开知核 → 我的 → 我的工具包 → 批量上传，选择该 ZIP 上传；上传后页面会逐条显示创建成功 / 失败原因，失败的按提示修正后可重新上传。

## 注意

- 保留资料中的命令、参数与注意事项，正文使用 Markdown。
- 图片优先复制进 covers/ 或 lessons/ 并用相对路径引用；外部图片 URL 也可直接使用。
- 不要臆造资料里没有的内容；缺失的简介可自动截取详细介绍前 500 字。
"""

SCRIPT_PY = f"""#!/usr/bin/env python3
\"\"\"知核批量上传包：校验与打包工具（仅标准库）。

用法：
  python3 build_batch.py init     <目录>        生成模板骨架
  python3 build_batch.py validate <目录>        校验 toolkits.json 与引用文件
  python3 build_batch.py pack     <目录> <out.zip>  校验通过后打包 ZIP
\"\"\"
import json, os, sys, zipfile

CATEGORIES = {json.dumps(CATEGORIES, ensure_ascii=False)}
LEVELS = {json.dumps(LEVELS, ensure_ascii=False)}
IMAGE_EXT = {json.dumps(sorted(IMAGE_EXTENSIONS), ensure_ascii=False)}
BAD_RES_EXT = {json.dumps(sorted(REJECTED_RESOURCE_EXTENSIONS), ensure_ascii=False)}
MAX_LESSON_BYTES = {MAX_LESSON_BYTES}
MAX_ITEMS = {MAX_BATCH_ITEMS}
MANIFEST = "toolkits.json"

TEMPLATE = {json.dumps(json.dumps([SAMPLE_ITEM], ensure_ascii=False, indent=2), ensure_ascii=False)}
DEMO_FILES = {json.dumps({"covers/demo-cover.svg": DEMO_COVER_SVG, "resources/gsea_helper.sh": DEMO_RESOURCE_SH, "lessons/01-prepare.md": DEMO_LESSON_MD, "README.txt": README_TXT}, ensure_ascii=False)}

def fail(msg):
    print(f"  ✗ " + msg); return False

def ok(msg):
    print(f"  ✓ " + msg); return True

def find_manifest(root):
    if os.path.isfile(os.path.join(root, MANIFEST)):
        return root
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, MANIFEST)):
            return p
    return None

def safe_join(base, rel):
    if rel.startswith(("/", os.sep)) or ".." in rel.split("/"):
        return None
    p = os.path.normpath(os.path.join(base, rel))
    return p if p.startswith(os.path.normpath(base) + os.sep) or p == base else None

def validate(root):
    mdir = find_manifest(root)
    if not mdir:
        print(f"✗ 未找到 {{MANIFEST}}（应在 {{root}} 或其一级子目录下）"); return False
    try:
        data = json.load(open(mdir + "/" + MANIFEST, encoding="utf-8"))
    except Exception as e:
        print(f"✗ toolkits.json 解析失败: {{e}}"); return False
    items = data if isinstance(data, list) else data.get("toolkits") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        print("✗ toolkits.json 必须是非空 JSON 数组"); return False
    if len(items) > MAX_ITEMS:
        print(f"✗ 一次最多 {{MAX_ITEMS}} 个工具包，当前 {{len(items)}}"); return False
    passed = True
    for i, it in enumerate(items, 1):
        label = (it.get("title") or f"第{{i}}项")[:40]
        t = it.get("title"); d = it.get("description")
        if not (isinstance(t, str) and 0 < len(t.strip()) <= 120): passed &= fail(f"{{label}}: title 缺失或超长")
        if not (isinstance(d, str) and d.strip()): passed &= fail(f"{{label}}: description 缺失")
        if it.get("category") and it["category"] not in CATEGORIES: passed &= fail(f"{{label}}: category 须为 {{'/'.join(CATEGORIES)}}")
        if it.get("level") and it["level"] not in LEVELS: passed &= fail(f"{{label}}: level 须为 {{'/'.join(LEVELS)}}")
        s = it.get("summary")
        if s and len(str(s)) > 500: passed &= fail(f"{{label}}: summary 超过 500 字")
        try:
            price = float(it.get("price", 0))
            if price < 0 or price > 9999: passed &= fail(f"{{label}}: price 须在 0～9999")
        except Exception: passed &= fail(f"{{label}}: price 无效")
        for key in ("cover", "resource"):
            v = it.get(key)
            if v and not str(v).startswith(("http://", "https://")):
                p = safe_join(mdir, str(v))
                if not p or not os.path.isfile(p): passed &= fail(f"{{label}}: {{key}} 文件不存在 {{v}}")
                elif os.path.getsize(p) > 40 * 1024 * 1024: passed &= fail(f"{{label}}: {{key}} 超过 40MB")
                else:
                    ext = os.path.splitext(str(v))[1].lower()
                    if key == "cover" and ext not in IMAGE_EXT: passed &= fail(f"{{label}}: cover 仅支持 {{'/'.join(IMAGE_EXT)}}")
                    if key == "resource" and ext in BAD_RES_EXT: passed &= fail(f"{{label}}: resource 不能是可执行文件 {{ext}}")
        lessons = it.get("lessons") or []
        if not isinstance(lessons, list): passed &= fail(f"{{label}}: lessons 须为数组"); continue
        for j, ls in enumerate(lessons, 1):
            if not (ls.get("title") or "").strip(): passed &= fail(f"{{label}} 步骤{{j}}: 缺少 title")
            body = ls.get("content") or ""
            cf = ls.get("contentFile")
            if not body and not cf: passed &= fail(f"{{label}} 步骤{{j}}: content 与 contentFile 至少一个")
            if body and len(str(body).encode("utf-8")) > MAX_LESSON_BYTES: passed &= fail(f"{{label}} 步骤{{j}}: content 超过 1MB")
            if cf:
                p = safe_join(mdir, str(cf))
                if not p or not os.path.isfile(p): passed &= fail(f"{{label}} 步骤{{j}}: contentFile 不存在 {{cf}}")
                elif os.path.getsize(p) > MAX_LESSON_BYTES: passed &= fail(f"{{label}} 步骤{{j}}: contentFile 超过 1MB")
            img = ls.get("image")
            if img and not str(img).startswith(("http://", "https://")):
                p = safe_join(mdir, str(img))
                if not p or not os.path.isfile(p): passed &= fail(f"{{label}} 步骤{{j}}: image 文件不存在 {{img}}")
                elif os.path.splitext(str(img))[1].lower() not in IMAGE_EXT: passed &= fail(f"{{label}} 步骤{{j}}: image 仅支持 {{'/'.join(IMAGE_EXT)}}")
    if passed: ok(f"校验通过，共 {{len(items)}} 个工具包")
    return passed

def pack(root, out):
    mdir = find_manifest(root)
    if not validate(root): return False
    if not out.endswith(".zip"): out += ".zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, dirs, files in os.walk(mdir):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for f in files:
                if os.path.basename(out) == f and os.path.dirname(base) == mdir: continue
                p = os.path.join(base, f)
                zf.write(p, os.path.relpath(p, mdir))
    print(f"✓ 已打包 {{out}}")
    return True

def init(root):
    os.makedirs(root, exist_ok=True)
    with open(root + "/" + MANIFEST, "w", encoding="utf-8") as f:
        f.write(json.dumps(json.loads(TEMPLATE), ensure_ascii=False, indent=2))
    for rel, content in DEMO_FILES.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    print(f"✓ 已在 {{root}} 生成模板骨架（含示例封面/资源/正文），编辑后运行 validate")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    cmd, arg = sys.argv[1], sys.argv[2]
    good = init(arg) if cmd == "init" else validate(arg) if cmd == "validate" else pack(arg, sys.argv[3]) if cmd == "pack" and len(sys.argv) > 3 else False
    sys.exit(0 if good else 1)
"""

def build_skill_zip() -> io.BytesIO:
    """生成本地转换 Skill 包（解压后放入 ~/.claude/skills/ 目录）。"""
    root = "zhihe-toolkit-converter/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(root + "SKILL.md", SKILL_MD)
        zf.writestr(root + "references/format.md", README_TXT)
        zf.writestr(root + "scripts/build_batch.py", SCRIPT_PY)
        zf.writestr(root + "toolkits.template.json", _clean_manifest())
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- 解析上传包

class ManifestError(ValueError):
    """清单级错误（整个包无法解析）。"""


def _find_manifest_name(names):
    if MANIFEST_NAME in names:
        return "", MANIFEST_NAME
    for n in names:
        parts = n.split("/")
        if len(parts) == 2 and parts[1] == MANIFEST_NAME:
            return parts[0] + "/", n
    raise ManifestError(f"缺少 {MANIFEST_NAME}，请参考标准模板")


def _resolve(zf, prefix, base_dir, rel, field):
    """安全解析 ZIP 内相对路径，返回 (name, bytes)。"""
    rel = str(rel)
    if rel.startswith("/") or ".." in posixpath.normpath(rel).split("/"):
        raise ValueError(f"{field} 路径不合法：{rel}")
    candidate = posixpath.normpath(posixpath.join(base_dir, rel))
    if base_dir and not (candidate == base_dir or candidate.startswith(base_dir + "/")):
        raise ValueError(f"{field} 路径越界：{rel}")
    if candidate not in zf.namelist():
        raise ValueError(f"{field} 引用的文件不存在：{rel}")
    info = zf.getinfo(candidate)
    if info.is_dir():
        raise ValueError(f"{field} 指向的不是文件：{rel}")
    if info.file_size > MAX_ITEM_BYTES:
        raise ValueError(f"{field} 文件超过 40MB：{rel}")
    return posixpath.basename(candidate), zf.read(candidate)


def _check_image_ext(name, field):
    ext = posixpath.splitext(name)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(f"{field} 仅支持 {'/'.join(sorted(IMAGE_EXTENSIONS))} 格式：{name}")
    return name


def _check_resource_ext(name, field):
    ext = posixpath.splitext(name)[1].lower()
    if ext in REJECTED_RESOURCE_EXTENSIONS:
        raise ValueError(f"{field} 不能是可执行文件类型（{ext}）：{name}")
    return name


def _load_text(zf, prefix, base_dir, field, rel):
    raw = _resolve(zf, prefix, base_dir, rel, field)[1]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{field} 文件必须为 UTF-8 文本")


def load_batch_package(zf):
    """解析批量 ZIP。返回 (items, errors)：items 为校验通过的条目，errors 为逐条失败项。"""
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if any(n.startswith("/") or ".." in posixpath.normpath(n).split("/") for n in names):
        raise ManifestError("压缩包中存在不合法的路径条目")
    if len(names) > MAX_ZIP_MEMBERS:
        raise ManifestError(f"压缩包内文件过多（上限 {MAX_ZIP_MEMBERS} 个）")
    if sum(zf.getinfo(n).file_size for n in names) > MAX_ZIP_UNCOMPRESSED:
        raise ManifestError("压缩包解压后总大小超过 300MB")
    prefix, manifest_name = _find_manifest_name(names)
    try:
        data = json.loads(zf.read(manifest_name).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"toolkits.json 解析失败：{exc}") from exc
    items = data if isinstance(data, list) else (data.get("toolkits") if isinstance(data, dict) else None)
    if not isinstance(items, list) or not items:
        raise ManifestError("toolkits.json 必须是非空 JSON 数组")
    if len(items) > MAX_BATCH_ITEMS:
        raise ManifestError(f"一次最多上传 {MAX_BATCH_ITEMS} 个工具包")

    base_dir = prefix.rstrip("/")
    parsed, errors = [], []
    for index, raw in enumerate(items):
        title = (raw.get("title") or "").strip() if isinstance(raw, dict) else ""
        try:
            if not isinstance(raw, dict):
                raise ValueError("条目必须是 JSON 对象")
            if not title or len(title) > 120:
                raise ValueError("标题不能为空且不超过 120 字")
            description = (raw.get("description") or "").strip()
            if not description:
                raise ValueError("详细介绍不能为空")
            summary = (raw.get("summary") or "").strip()[:500] or description[:500]
            category = raw.get("category") or "生信分析"
            if category not in CATEGORIES:
                raise ValueError(f"分类须为：{' / '.join(CATEGORIES)}")
            level = raw.get("level") or "入门"
            if level not in LEVELS:
                raise ValueError(f"难度须为：{' / '.join(LEVELS)}")
            try:
                price = Decimal(str(raw.get("price", 0))).quantize(Decimal("0.01"))
            except Exception:
                raise ValueError("定价必须是数字")
            if price < 0 or price > 9999:
                raise ValueError("定价须在 0～9999 元之间")
            tags = raw.get("tags") or ""
            if isinstance(tags, list):
                tags = ",".join(str(x).strip() for x in tags if str(x).strip())
            tags = str(tags)[:300]

            cover_url, cover_bytes, cover_name = None, None, None
            if raw.get("cover"):
                value = str(raw["cover"])
                if value.startswith(("http://", "https://")):
                    cover_url = value
                else:
                    cover_name, cover_bytes = _resolve(zf, prefix, base_dir, value, "cover")
                    _check_image_ext(cover_name, "封面")
            resource_name, resource_bytes = None, None
            if raw.get("resource"):
                resource_name, resource_bytes = _resolve(zf, prefix, base_dir, raw["resource"], "resource")
                _check_resource_ext(resource_name, "配套资源")

            lessons = []
            for j, lesson in enumerate(raw.get("lessons") or []):
                if len(lessons) >= 50:
                    raise ValueError("教程步骤最多 50 个")
                if not isinstance(lesson, dict):
                    raise ValueError(f"步骤 {j + 1} 必须是 JSON 对象")
                ltitle = (lesson.get("title") or "").strip()[:160] or f"步骤 {j + 1}"
                content = lesson.get("content")
                if not content and lesson.get("contentFile"):
                    content = _load_text(zf, prefix, base_dir, "contentFile", lesson["contentFile"])
                content = (content or "").strip()
                if not content:
                    raise ValueError(f"步骤 {j + 1} 缺少 content 或 contentFile")
                if len(content.encode("utf-8")) > MAX_LESSON_BYTES:
                    raise ValueError(f"步骤 {j + 1} 正文超过 1MB")
                try:
                    duration = max(1, min(600, int(lesson.get("duration", 10))))
                except Exception:
                    raise ValueError(f"步骤 {j + 1} 的学习时长无效")
                image_url, image_bytes, image_name = None, None, None
                if lesson.get("image"):
                    value = str(lesson["image"])
                    if value.startswith(("http://", "https://")):
                        image_url = value
                    else:
                        image_name, image_bytes = _resolve(zf, prefix, base_dir, value, f"步骤 {j + 1} image")
                        _check_image_ext(image_name, f"步骤 {j + 1} 配图")
                preview = bool(lesson.get("preview"))
                if j == 0 and "preview" not in lesson:
                    preview = True
                lessons.append({"title": ltitle, "content": content, "duration": duration,
                                "preview": preview, "image_url": image_url,
                                "image_bytes": image_bytes, "image_name": image_name})

            parsed.append({"index": index, "title": title, "summary": summary, "description": description,
                           "category": category, "level": level, "price": price,
                           "tags": tags, "cover_url": cover_url, "cover_bytes": cover_bytes, "cover_name": cover_name,
                           "resource_bytes": resource_bytes, "resource_name": resource_name, "lessons": lessons})
        except (ValueError, KeyError) as exc:
            errors.append({"index": index, "title": title or f"第 {index + 1} 项", "status": "failed", "error": str(exc)})
    return parsed, errors
