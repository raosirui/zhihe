"""Template/converter downloads and bounded, staged batch ZIP parsing.

All validation is standard library only and shared with the downloadable CLI.
No extraction paths come from archive member names; ZIP assets live in staging.
"""
import io
import json
import os
import posixpath
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path

from validation import (
    CATEGORIES, LEVELS, IMAGE_EXTENSIONS, REJECTED_RESOURCE_EXTENSIONS,
    MANIFEST_NAME, MAX_BATCH_ITEMS, MAX_ITEM_BYTES, MAX_LESSON_BYTES,
    MAX_MANIFEST_BYTES, MAX_PARSED_TEXT_BYTES, MAX_ZIP_COMPRESSED,
    MAX_ZIP_UNCOMPRESSED, MAX_ZIP_MEMBERS, MAX_DESCRIPTION_CHARS,
    TextBudget, manifest_items, normalize_batch_item, normalize_relative_path, item_error,
)

SAMPLE_ITEM = {
    "title": "示例工具包：GSEA 富集分析与发表级绘图",
    "summary": "规范完成基因集富集分析，并输出可直接用于论文的图表。",
    "description": "本工具包将 GSEA 分析拆解为可复现的操作步骤，包含示例基因集、参数说明与绘图脚本。",
    "category": "生信分析", "level": "入门", "price": 0, "tags": "GSEA,富集分析,绘图",
    "cover": "covers/demo-cover.svg", "resource": "resources/gsea_helper.sh",
    "resources": ["resources/demo-genes.gmt"],
    "lessons": [
        {"title": "环境准备与示例数据", "contentFile": "lessons/01-prepare.md",
         "documentFile": "lessons/01-prepare.md", "image": "lessons/gsea-flow.svg",
         "duration": 8, "preview": True},
        {"title": "运行 GSEA 并解读结果", "content": "## 运行 GSEA\n\n加载基因集与表达矩阵，按推荐参数运行，重点关注 NES 与 FDR q-value。\n\n### 操作要点\n\n1. 检查基因集格式（gmt）\n2. 固定随机种子保证可复现\n3. 导出 leading edge 基因用于后续绘图", "duration": 12},
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
# 示例配套资源：仅作为附件提供，平台和转换器不会执行它。
echo "zhihe demo resource"
"""
DEMO_RESOURCE_GMT = """DEMO_GSEA_SET	Example gene set (replace with your validated data)	TP53	EGFR	MAPK1
"""
DEMO_LESSON_IMAGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="800" height="180" viewBox="0 0 800 180">
  <rect width="800" height="180" fill="#0a1f1a"/>
  <text x="40" y="80" font-family="sans-serif" font-size="26" fill="#b9f36a">表达矩阵 + 基因集 → GSEA</text>
  <text x="40" y="130" font-family="sans-serif" font-size="20" fill="#ffffff">示例步骤配图 · 请替换为实际分析图</text>
</svg>
"""
DEMO_LESSON_MD = """## 环境准备与示例数据

1. 安装 GSEA 桌面版或 `clusterProfiler`（R）。
2. 准备表达矩阵（gene_symbol × sample）与 gmt 基因集。
3. 检查注释一致性，剔除 NA 值。

> 提示：本文件（lessons/01-prepare.md）作为第一步的试看正文；同路径的原始教学文档遵循该教程的学习权限；免费或试看步骤可能允许匿名下载，勿放入敏感资料。
"""

README_TXT = """知核 · 工具包批量上传标准格式
================================
目录（ZIP 根目录，或唯一一级子目录）：
  toolkits.json  必需：UTF-8 JSON 非空数组；也兼容 {"toolkits": [...]} 对象
  covers/       可选：封面图片
  resources/    可选：配套资源（附件，不执行脚本）
  lessons/      可选：正文、原始教学文档与教程配图

模板示例：第一节的 contentFile 与 documentFile 均引用 lessons/01-prepare.md。
前者提供 Markdown 正文，后者保留同一文件作为原始教学文档；文件只入包一次。
此外还演示了 cover、image、resource 与 resources 附件。按需替换示例内容。

每个工具包：
  title        必填字符串，1～120 字
  description  必填字符串，1～20000 字
  summary      选填字符串，≤500 字；省略/空字符串时自动截取 description
  category     选填字符串，生信分析/单细胞/空间组学/结构生物学/基因编辑，默认生信分析
  level        选填字符串，入门/进阶/专业，默认入门
  price        选填有限数字或十进制数字字符串，0～9999，默认0；四舍五入到两位小数
  tags         选填字符串或≤100项的字符串数组，合并后≤300字
  cover        选填字符串，ZIP 内相对图片路径或 http(s) 图片 URL
  resource     选填字符串，只能是 ZIP 内相对文件路径，禁止 HTTP URL
  resources    选填数组（≤20项），每项同 resource；与 resource 合并去重，
               resource 排最前；单个工具包附件总数（含resource）≤20
  lessons      选填数组，最多50项；新建省略默认为[]，更新省略保留旧步骤；显式[]可清空草稿，上架不可清空。每项必须是对象：
    title        必填字符串，1～160 字
    id           选填正整数；更新时需属于目标工具包。重排教程应填写原id，
                 否则更新按位置匹配，可能把原件/图片关联到错误步骤
    content      选填 Markdown 字符串，UTF-8≤1MB；仅配合documentFile时可为空；
                 不得与contentFile并用
    contentFile  选填 ZIP 内 UTF-8 正文文件相对路径，≤1MB；不得与content并用
    documentFile 选填 ZIP 内原始教学文档相对路径，支持
                 .md/.markdown/.txt/.docx/.pdf/.html/.htm/.rtf/.pptx
                 可以单独提供，也可与 content 或 contentFile 搭配；Markdown类原件≤1MB；
                 无 documentFile 时必须提供 content 或 contentFile
    clearDocument 选填JSON布尔值；true仅用于更新已匹配教程的原件移除，
                 须提供新的非空content或contentFile，且不得同填documentFile
    duration     选填整数，1～600分钟，默认10；不接受字符串/小数/bool
    image        选填字符串，相对图片路径或http(s)图片URL
    preview      选填JSON布尔true/false；仅真正省略时默认第一节true、其余false
                 false保留；"false"字符串、null或数字无效
  featured     不授予推荐位，仅管理员设置；如提供，必须是JSON布尔值

限制与安全：
  - 一次最多50个工具包，每个最多50节；所有字段不得以null/容器替代具体类型
  - 图片文件扩展名jpg/jpeg/png/webp/gif/svg；图片实际格式/安全性由服务端再验证
  - 资源拒绝exe/bat/cmd/com/scr/vbs/jse/wsf/ps1/dll/msi/hta等危险类型
  - 清单独立上限2MB；解析后文本累计20MB（重复contentFile按规范路径仅计一次）
  - ZIP压缩体≤100MiB-64KiB（为100MiB请求预留表单空间）、解压≤300MB、单成员≤40MB、成员≤2000
  - 正文和Markdown类原件单节≤1MB；转换后预览单节≤4MB、每工具包累计≤20MB（服务端检查）
  - 文件路径≤500字，每段文件名≤200字；禁止绝对路径、..路径段、反斜杠、
    Windows盘符/冒号、符号链接、重复规范化成员名
  - 不接受损坏成员；即使未被清单引用也检查ZIP成员。平台不执行附件
  - 教程原件遵循该教程的学习权限：已发布的免费教程/付费试看可能允许匿名下载，
    付费锁定教程须通过购买等权益检查；请勿将敏感原件放入试看步骤。
    配套资源附件另须登录并满足附件下载权限，平台不会执行附件
  - mode=skip 按同一作者+标题跳过已有工具包（含草稿）；mode=update 更新同名工具包
    但保留其现有发布状态；mode=create 不去重。publish 仅决定新建条目的发布/草稿状态。
    更新时已有教程按id（如提供）或位置匹配，未传documentFile会保留匹配教程的原件；
    保留非Markdown原件时不可只改正文，须替换原件或用clearDocument:true与非空正文移除旧原件；重排请填写原教程id。
    上传后查看逐条 created/updated/skipped/failed 报告；本地 validate 无法预知这些结果

本地转换（仅Python标准库，validation.py必须和build_batch.py放在同一scripts目录）：
  python3 scripts/build_batch.py init 工作目录
  python3 scripts/build_batch.py validate 工作目录
  python3 scripts/build_batch.py pack 工作目录 zhihe-batch.zip
  已有模板或输出文件默认拒绝覆盖；仅明确确认后加 --force 覆盖
  pack仅包含toolkits.json与显式引用文件（含documentFile）；不会扫描/夹带.env、
  README.txt、notes或未引用资料。输出ZIP不得被清单显式引用；输出在同目录
  临时写入，再原子安装。
  validate做本地结构、路径、引用文件存在/大小及UTF-8正文检查，不能代替服务端
  的ZIP完整性、图片和文档实际内容/格式验证、转换、配额、权限及发布结果检查。
上传：知核 → 我的 → 我的工具包 → 批量上传。下载转换器不等于安装或执行Skill。
"""

DEMO_FILES = {
    "covers/demo-cover.svg": DEMO_COVER_SVG,
    "resources/gsea_helper.sh": DEMO_RESOURCE_SH,
    "resources/demo-genes.gmt": DEMO_RESOURCE_GMT,
    "lessons/01-prepare.md": DEMO_LESSON_MD,
    "lessons/gsea-flow.svg": DEMO_LESSON_IMAGE_SVG,
    "README.txt": README_TXT,
}

PROMPT_TEXT = f"""请把用户明确指定的本地科研教程资料整理为知核批量上传ZIP。保留真实命令与说明，不编造内容、不执行附件、不读取未指定的秘密文件。
每个主题生成工具包及教程步骤；toolkits.json为UTF-8非空数组，最多50项、每项最多50节；新建省略lessons视为[]，更新省略保留旧步骤，显式[]只能清空草稿。模板第一节同时引用同一个Markdown的contentFile与documentFile，并含封面、配图、两项附件；请按用户实际资料替换示例。
title必填≤120字，description必填≤20000字，summary≤500字，tags字符串/字符串数组合并≤300字；category仅{' / '.join(CATEGORIES)}，level仅{' / '.join(LEVELS)}；price有限0～9999，禁止NaN/Infinity/bool。
每节title必填≤160字；正文content/contentFile只能选一个，且无documentFile时必须选其一，UTF-8正文≤1MB（仅有documentFile时content可留空）。documentFile为可单独使用、也可搭配正文的ZIP内原始文档路径（Markdown类≤1MB），支持.md/.markdown/.txt/.docx/.pdf/.html/.htm/.rtf/.pptx；duration整数1～600；preview只能JSON true/false，第一节省略才默认true，显式false必须保留。更新重排教程时提供目标工具包内原教程的正整数id；不提供则按位置匹配，会错误继承其他步骤的原件/图片。更新未传documentFile会保留匹配教程的原件；保留非Markdown原件时不可只改正文，须替换原件或清除；要删除原件，提供clearDocument:true与新的非空content/contentFile，不得同时传documentFile。
cover/image可相对图片路径或http(s)图片URL；resource/resources/contentFile/documentFile只能相对文件路径，禁止HTTP URL、绝对路径、..、反斜杠、盘符/冒号及symlink。图片仅jpg/jpeg/png/webp/gif/svg，附件最多20项且禁止exe/bat/ps1/dll等危险类型；不写featured。
清单≤2MB，累计解析文本≤20MB（重复正文路径仅计一次），每文件≤40MB，ZIP压缩体≤100MiB-64KiB（预留表单空间）、解压总量≤300MB、成员≤2000，路径≤500字/每段文件名≤200字；原件另受服务端格式/内容/大小检查，转换后HTML每节≤4MB、每工具包累计≤20MB。长中文正文使用contentFile。
使用python3 scripts/build_batch.py init <工作目录>起步，再validate <工作目录>，通过后pack <工作目录> <输出.zip>。工具只打包清单显式引用文件（含原件），不扫描资料目录；未引用.env/README.txt/notes不进入ZIP，禁止输出自引用。已有模板/输出默认拒绝覆盖，必须用户明确确认后才加--force。
本地validate只检查结构、路径和引用文件存在/大小及UTF-8正文，不能替代服务端ZIP完整性、图片/文档实际格式/安全性/转换、配额及权限验证。教程原件按该教程学习权限检查（已发布的免费/付费试看步骤可能允许匿名下载）；附件下载仍需登录及相应权益。请勿将敏感原件放在试看步骤。
上传时mode=skip跳过同作者+同名已有条目（含草稿），mode=update更新同名工具包但保留其原发布状态，mode=create不去重；publish仅决定新建工具包是否发布。完成后提醒用户在知核「我的 → 我的工具包 → 批量上传」上传并查看逐条created/updated/skipped/failed报告。转换器只依赖标准库，其validation.py与build_batch.py必须保持同目录。"""

SKILL_MD = f"""---
name: zhihe-toolkit-converter
description: 将用户指定的科研教程资料整理成知核批量上传toolkits.json，并用标准库工具校验和打包。
---
# 知核工具包转换器
1. 仅阅读用户指定的资料，按主题生成工具包。规则详见 references/format.md；参考 toolkits.template.json 后先运行 init 生成其引用文件。
2. `python3 scripts/build_batch.py init <工作目录>` 生成模板，再编辑toolkits.json及引用文件。
3. `python3 scripts/build_batch.py validate <工作目录>`；失败先修正，不能跳过校验。
4. `python3 scripts/build_batch.py pack <工作目录> <输出.zip>`；仅清单与显式引用文件（含原件）入包。
5. 提示在知核 → 我的 → 我的工具包 → 批量上传，查看逐条 created/updated/skipped/failed 结果。

硬约束：最多50工具包/每包50节、最多20项附件；新建省略lessons视为[]，更新省略保留旧步骤，显式[]只可清空草稿；title≤120、description≤20000、summary≤500、lesson.title≤160字。
category只能{' / '.join(CATEGORIES)}；level只能{' / '.join(LEVELS)}；price有限0～9999。
正文content/contentFile只能二选一且≤1MB；无documentFile时须有非空正文，搭配documentFile时content可留空。documentFile是可独立或搭配正文的原始文档相对路径（Markdown类≤1MB），支持.md/.markdown/.txt/.docx/.pdf/.html/.htm/.rtf/.pptx。模板同一个.md同时用作contentFile和documentFile，只入包一次。更新已有教程可传目标工具包内的正整数id；未传按位置匹配，重排必须用原id以免把原件/图片关联错。更新未传documentFile保留匹配教程的原件；保留非Markdown原件时不可只改正文，须替换或清除；要移除须传clearDocument:true与新的非空content/contentFile，不得同时传documentFile。
duration整数1～600；preview只能JSON true/false，第一节仅省略默认true，显式false保留。
cover/image可http(s)图片URL或相对路径；resource/resources/contentFile/documentFile禁止URL；禁止symlink、..、绝对路径、反斜杠、盘符/冒号。图片仅jpg/jpeg/png/webp/gif/svg；资源拒绝exe/bat/ps1/dll等。
清单≤2MB、解析文本累计≤20MB（重复正文只计一次）、每文件≤40MB、ZIP压缩≤100MiB-64KiB（预留表单空间）/解压≤300MB/成员≤2000，路径≤500字/每段文件名≤200字。长中文正文用contentFile；原件需再过服务端内容/格式/大小检查，转换后HTML每节≤4MB、每工具包累计≤20MB。
不要扫描未指定目录/读取秘密/执行附件，不把未引用.env/README.txt/notes打包，不显式引用输出ZIP。
已有模板或输出默认不覆盖；取得用户明确确认后可加--force。输出同目录临时写入再原子安装。
本地validate验证结构、路径及引用文件存在/大小和UTF-8正文，不能代替服务端ZIP完整性、图片/文档实际格式/转换、配额及权限检查。教程原件遵循教程学习权限（已发布的免费/付费试看步骤可能允许匿名下载）；资源附件下载仍需登录及相应权益。请勿将敏感原件放在试看步骤。
上传时mode=skip按同作者+同名跳过（含草稿）；mode=update更新同名并保留已有发布状态；mode=create不去重；publish仅作用于新建条目。需查看逐条处理结果。
scripts/validation.py是共享标准库模块，必须与build_batch.py保持同目录。推荐位仅管理员设置，不写featured。下载Skill不等于安装或执行它。
"""


def _clean_manifest():
    return json.dumps([SAMPLE_ITEM], ensure_ascii=False, indent=2).encode("utf-8")


def build_template_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, _clean_manifest())
        for name, content in DEMO_FILES.items():
            archive.writestr(name, content)
    buf.seek(0)
    return buf


# Keep SCRIPT_PY as the download entry point without duplicating CLI validation.
SCRIPT_PY = ("#!/usr/bin/env python3\n"
             + "TEMPLATE = " + repr(_clean_manifest().decode("utf-8")) + "\n"
             + "DEMO_FILES = " + repr(DEMO_FILES) + "\n"
             + Path(__file__).with_name("batch_publish_cli.py").read_text(encoding="utf-8"))


def build_skill_zip():
    """Build a download only. It does not install or execute a Skill."""
    root = "zhihe-toolkit-converter/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(root + "SKILL.md", SKILL_MD)
        archive.writestr(root + "references/format.md", README_TXT)
        archive.writestr(root + "scripts/build_batch.py", SCRIPT_PY)
        archive.writestr(root + "scripts/validation.py", Path(__file__).with_name("validation.py").read_bytes())
        archive.writestr(root + "toolkits.template.json", _clean_manifest())
    buf.seek(0)
    return buf


class ManifestError(ValueError):
    """Package-level error: the route must report HTTP 400, not HTTP 500."""


def _find_manifest_name(names):
    if MANIFEST_NAME in names:
        return "", MANIFEST_NAME
    matches = [name for name in names if len(name.split("/")) == 2 and name.split("/")[1] == MANIFEST_NAME]
    if len(matches) != 1:
        raise ManifestError(f"缺少或存在多个一级目录 {MANIFEST_NAME}，请参考标准模板")
    name = matches[0]
    return name.split("/")[0] + "/", name


def _archive_size(archive):
    try:
        position = archive.fp.tell()
        archive.fp.seek(0, os.SEEK_END)
        size = archive.fp.tell()
        archive.fp.seek(position)
        return size
    except (AttributeError, OSError, ValueError) as exc:
        raise ManifestError("无法检查 ZIP 压缩体大小") from exc


def _member_index(archive):
    if _archive_size(archive) > MAX_ZIP_COMPRESSED:
        raise ManifestError("压缩包超过上传上限（100MB 请求须预留 64KB 表单空间）")
    members = archive.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ManifestError(f"压缩包成员过多（上限 {MAX_ZIP_MEMBERS} 个，含目录）")
    index, total, compressed = {}, 0, 0
    for info in members:
        try:
            original = getattr(info, "orig_filename", info.filename)
            if "\x00" in original:
                raise ValueError("成员名含空字符")
            name = normalize_relative_path(info.filename.rstrip("/"), "ZIP 成员")
        except ValueError as exc:
            raise ManifestError(str(exc)) from exc
        if name in index:
            raise ManifestError(f"存在重复规范化成员名：{name}")
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if (file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or stat.S_ISLNK(mode)
                or (file_type == stat.S_IFDIR and not info.is_dir())
                or (file_type == stat.S_IFREG and info.is_dir())):
            raise ManifestError(f"压缩包不允许符号链接、特殊文件或目录类型不一致：{name}")
        if info.flag_bits & 1:
            raise ManifestError(f"不支持加密成员：{name}")
        if info.file_size < 0 or info.compress_size < 0 or info.file_size > MAX_ITEM_BYTES:
            raise ManifestError(f"ZIP 成员超过 40MB 或大小不合法：{name}")
        total += info.file_size
        compressed += info.compress_size
        if total > MAX_ZIP_UNCOMPRESSED or compressed > MAX_ZIP_COMPRESSED:
            raise ManifestError("压缩包解压总量超过 300MB 或压缩体超过上传上限")
        index[name] = info
    return index


def _copy_member(archive, info, destination, maximum, total):
    """Bound every actual stream read, including when metadata lies; checks CRC."""
    size = 0
    try:
        with archive.open(info, "r") as source:
            while True:
                chunk = source.read(min(64 * 1024, maximum - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                total[0] += len(chunk)
                if size > maximum or total[0] > MAX_ZIP_UNCOMPRESSED:
                    raise ManifestError("ZIP 实际解压大小超过成员或总量上限")
                destination.write(chunk)
        if size != info.file_size:
            raise ManifestError(f"ZIP 成员实际大小与清单不一致：{info.filename}")
        return size
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError, ValueError) as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError(f"ZIP 成员损坏或无法读取：{info.filename}") from exc


class _ZipResolver:
    def __init__(self, files, base_dir, budget):
        self.files, self.base_dir, self.budget = files, base_dir, budget
        self.texts = {}

    def asset(self, rel, field):
        rel = normalize_relative_path(rel, field)
        key = posixpath.join(self.base_dir, rel) if self.base_dir else rel
        descriptor = self.files.get(key)
        if descriptor is None:
            raise ValueError(f"{field}引用的文件不存在或不是普通文件：{rel}")
        return descriptor

    def text(self, rel, field):
        descriptor = self.asset(rel, field)
        key = descriptor["cache_key"]
        if key in self.texts:
            return self.texts[key]
        if descriptor["size"] > MAX_LESSON_BYTES:
            raise ValueError(f"{field}正文超过 1MB")
        self.budget.charge(descriptor["size"], key)
        with open(descriptor["path"], "rb") as source:
            raw = source.read(min(MAX_LESSON_BYTES, descriptor["size"]) + 1)
        if len(raw) > MAX_LESSON_BYTES or len(raw) != descriptor["size"]:
            raise ValueError(f"{field}正文超过 1MB 或暂存文件发生变化")
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ValueError(f"{field}文件必须为 UTF-8 文本") from exc
        if not text:
            raise ValueError(f"{field}正文不能为空")
        self.texts[key] = text
        return text


def load_batch_package(zf, staging_dir):
    """Return (items, errors), staging assets once per normalized member path.

    Caller owns an isolated staging_dir and keeps it alive until its file service
    finishes. Each asset is a dict{name,path,size,cache_key}, never in-memory bytes.
    Corrupt/unsafe archives raise ManifestError (ValueError); malformed individual
    items produce index/title/status/message/error records and do not hide peers.
    """
    stage = None
    try:
        members = _member_index(zf)
        file_names = [name for name, info in members.items() if not info.is_dir()]
        prefix, manifest_name = _find_manifest_name(file_names)
        info = members[manifest_name]
        if info.file_size > MAX_MANIFEST_BYTES:
            raise ManifestError("toolkits.json 清单超过独立 2MB 上限；长正文请用 contentFile")
        total = [0]
        manifest_buffer = io.BytesIO()
        _copy_member(zf, info, manifest_buffer, MAX_MANIFEST_BYTES, total)
        manifest = manifest_buffer.getvalue()
        try:
            entries = manifest_items(json.loads(manifest.decode("utf-8-sig")))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ManifestError(f"toolkits.json 解析失败：{exc}") from exc
        root = os.path.abspath(staging_dir)
        if not os.path.isdir(root) or os.path.realpath(root) != root:
            raise ManifestError("staging_dir 必须是已创建、无符号链接的隔离目录")
        stage = tempfile.mkdtemp(prefix="zhihe-batch-", dir=root)
        files = {}
        # Stage every member once: even unreferenced files must pass CRC/limits.
        for name, member in members.items():
            if member.is_dir():
                _copy_member(zf, member, io.BytesIO(), 0, total)
                continue
            fd, path = tempfile.mkstemp(prefix="member-", dir=stage)
            with os.fdopen(fd, "wb") as destination:
                if name == manifest_name:
                    destination.write(manifest)
                    size = len(manifest)
                else:
                    size = _copy_member(zf, member, destination, MAX_ITEM_BYTES, total)
            files[name] = {"name": posixpath.basename(name), "path": path, "size": size, "cache_key": name}
        del manifest, manifest_buffer
        budget = TextBudget(MAX_PARSED_TEXT_BYTES)
        resolver = _ZipResolver(files, prefix.rstrip("/"), budget)
        items, errors = [], []
        for index, raw in enumerate(entries):
            try:
                items.append(normalize_batch_item(raw, index, resolver, budget))
            except (ValueError, KeyError, OSError, UnicodeError) as exc:
                errors.append(item_error(index, raw, exc))
        return items, errors
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, TypeError, ValueError) as exc:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError(f"无法解析批量 ZIP：{exc}") from exc
