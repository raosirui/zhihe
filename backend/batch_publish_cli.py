"""Standard-library-only batch CLI, also embedded in the downloadable converter.

Commands: init DIR [--force]; validate DIR; pack DIR OUT.zip [--force].
Existing user files are never replaced without explicit --force.
"""
import argparse
import json
import ntpath
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

from validation import (
    MANIFEST_NAME, MAX_MANIFEST_BYTES, MAX_ITEM_BYTES, MAX_LESSON_BYTES,
    MAX_ZIP_UNCOMPRESSED, MAX_ZIP_MEMBERS, MAX_ZIP_COMPRESSED,
    TextBudget, manifest_items, normalize_batch_item, normalize_relative_path, item_error,
)

CHUNK_SIZE = 64 * 1024


def _reject_symlink_components(path):
    current = Path(os.path.abspath(path))
    for part in (current, *current.parents):
        if part.is_symlink():
            raise ValueError(f"不允许符号链接：{part}")


def _local_path(value):
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError("本地路径不合法")
    value = os.fspath(value)
    if not isinstance(value, str) or any(char in value for char in ("\\", ":", "\x00")) or ntpath.splitdrive(value)[0]:
        raise ValueError("本地路径不允许 Windows 盘符、反斜杠或冒号")
    return os.path.abspath(value)


def safe_join(base, rel):
    """Resolve only POSIX relative file names inside a real, non-symlink root."""
    root = _local_path(base)
    _reject_symlink_components(root)
    rel = normalize_relative_path(rel)
    target = os.path.abspath(os.path.join(root, *rel.split("/")))
    _reject_symlink_components(target)
    if os.path.commonpath((os.path.realpath(root), os.path.realpath(target))) != os.path.realpath(root):
        raise ValueError("引用路径越界")
    return target


def find_manifest(root):
    root = _local_path(root)
    _reject_symlink_components(root)
    if not os.path.isdir(root):
        raise ValueError("资料目录不存在")
    direct = safe_join(root, MANIFEST_NAME)
    if os.path.isfile(direct):
        return root
    matches = []
    for entry in os.scandir(root):
        if entry.is_dir(follow_symlinks=False):
            candidate = safe_join(entry.path, MANIFEST_NAME)
            if os.path.isfile(candidate):
                matches.append(entry.path)
    if len(matches) != 1:
        raise ValueError(f"应在根目录或唯一一级子目录提供 {MANIFEST_NAME}")
    return matches[0]


def _open_regular(path):
    _reject_symlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("引用的路径不是普通文件")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def bounded_read(path, maximum):
    with _open_regular(path) as source:
        size = os.fstat(source.fileno()).st_size
        if size > maximum:
            raise ValueError(f"文件超过 {maximum} 字节上限")
        data = source.read(min(maximum, size) + 1)
    if len(data) > maximum or len(data) != size:
        raise ValueError(f"文件超过 {maximum} 字节上限或读取期间发生变化")
    return data


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class LocalResolver:
    def __init__(self, root, budget, manifest_size):
        self.root = root
        self.budget = budget
        self.files = {}
        self.texts = {}
        self.signatures = {}
        self.total = manifest_size

    def asset(self, rel, field):
        rel = normalize_relative_path(rel, field)
        if rel in self.files:
            return self.files[rel]
        path = safe_join(self.root, rel)
        with _open_regular(path) as source:
            info = os.fstat(source.fileno())
            size = info.st_size
        self.signatures[rel] = _signature(info)
        if size > MAX_ITEM_BYTES:
            raise ValueError(f"{field}文件超过 40MB")
        # A reference to the manifest does not add a second ZIP member.
        if rel != MANIFEST_NAME:
            self.total += size
        if self.total > MAX_ZIP_UNCOMPRESSED or len(self.files) + 1 >= MAX_ZIP_MEMBERS:
            raise ValueError("引用文件总量或数量超过 ZIP 上限")
        descriptor = {"name": os.path.basename(path), "path": path, "size": size, "cache_key": rel}
        self.files[rel] = descriptor
        return descriptor

    def text(self, rel, field):
        descriptor = self.asset(rel, field)
        key = descriptor["cache_key"]
        if key in self.texts:
            return self.texts[key]
        if descriptor["size"] > MAX_LESSON_BYTES:
            raise ValueError(f"{field}正文超过 1MB")
        data = bounded_read(descriptor["path"], MAX_LESSON_BYTES)
        if _signature(os.stat(descriptor["path"], follow_symlinks=False)) != self.signatures[key]:
            raise ValueError(f"{field}文件读取期间发生变化")
        self.budget.charge(len(data), key)
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ValueError(f"{field}文件必须是 UTF-8 文本") from exc
        if not text:
            raise ValueError(f"{field}正文不能为空")
        self.texts[key] = text
        return text


def inspect(root):
    mdir = find_manifest(root)
    path = safe_join(mdir, MANIFEST_NAME)
    raw = bounded_read(path, MAX_MANIFEST_BYTES)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
        entries = manifest_items(data)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"toolkits.json 解析失败：{exc}") from exc
    budget = TextBudget()
    resolver = LocalResolver(mdir, budget, len(raw))
    items, errors = [], []
    for index, entry in enumerate(entries):
        try:
            items.append(normalize_batch_item(entry, index, resolver, budget))
        except (ValueError, OSError, UnicodeError) as exc:
            errors.append(item_error(index, entry, exc))
    return mdir, raw, items, errors, resolver


def validate(root):
    try:
        _, _, items, errors, _ = inspect(root)
    except (ValueError, OSError) as exc:
        print(f"校验失败：{exc}")
        return False
    for error in errors:
        print(f"第 {error['index'] + 1} 项 {error['title']}：{error['message']}")
    if errors:
        return False
    print(f"校验通过，共 {len(items)} 个工具包")
    return True


def _output_path(out):
    out = _local_path(out)
    if not out.lower().endswith(".zip"):
        out += ".zip"
    _reject_symlink_components(out)
    if not os.path.isdir(os.path.dirname(out)):
        raise ValueError("输出目录不存在")
    return out


def _atomic_install(temporary, target, force):
    # link() makes no-force creation atomic even if another process creates target.
    if force:
        _reject_symlink_components(target)
        os.replace(temporary, target)
    else:
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise ValueError("输出文件已存在；仅显式 --force 可覆盖") from exc
        os.unlink(temporary)


def pack(root, out, force=False):
    temporary = None
    try:
        out = _output_path(out)
        mdir, manifest, _, errors, resolver = inspect(root)
        if errors:
            raise ValueError("；".join(error["message"] for error in errors))
        paths = {MANIFEST_NAME: safe_join(mdir, MANIFEST_NAME)}
        paths.update({rel: descriptor["path"] for rel, descriptor in resolver.files.items()})
        if any(os.path.realpath(path) == os.path.realpath(out) for path in paths.values()):
            raise ValueError("清单不得显式引用输出 ZIP（自引用）")
        if os.path.exists(out) and not force:
            raise ValueError("输出文件已存在；仅显式 --force 可覆盖")
        fd, temporary = tempfile.mkstemp(prefix=".zhihe-batch-", suffix=".tmp", dir=os.path.dirname(out))
        os.close(fd)
        total = len(manifest)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, manifest)
            for rel, path in sorted(paths.items()):
                if rel == MANIFEST_NAME:
                    continue
                # No directory traversal: only the explicit, normalized references.
                size = 0
                maximum = MAX_LESSON_BYTES if rel in resolver.texts else MAX_ITEM_BYTES
                with _open_regular(safe_join(mdir, rel)) as source, archive.open(rel, "w") as destination:
                    if _signature(os.fstat(source.fileno())) != resolver.signatures[rel]:
                        raise ValueError("校验后引用文件发生变化，请重新运行")
                    while True:
                        chunk = source.read(min(CHUNK_SIZE, maximum - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        if size > maximum or total > MAX_ZIP_UNCOMPRESSED:
                            raise ValueError("实际读取文件大小超过 ZIP 上限")
                        destination.write(chunk)
                    if (size != resolver.files[rel]["size"]
                            or _signature(os.fstat(source.fileno())) != resolver.signatures[rel]):
                        raise ValueError("打包期间引用文件发生变化，请重新运行")
        if os.path.getsize(temporary) > MAX_ZIP_COMPRESSED:
            raise ValueError("压缩包超过 100MB")
        _atomic_install(temporary, out, force)
        temporary = None
        print(f"已打包 {out}（仅清单与显式引用文件）")
        return True
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"打包失败：{exc}")
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def init(root, force=False):
    temporaries = []
    try:
        # The downloadable script gets these literals prefixed by batch_kit.
        if "TEMPLATE" in globals() and "DEMO_FILES" in globals():
            template, files = TEMPLATE, DEMO_FILES
        else:
            from batch_kit import _clean_manifest, DEMO_COVER_SVG, DEMO_RESOURCE_SH, DEMO_LESSON_MD, README_TXT
            template = _clean_manifest().decode("utf-8")
            files = {"covers/demo-cover.svg": DEMO_COVER_SVG,
                     "resources/gsea_helper.sh": DEMO_RESOURCE_SH,
                     "lessons/01-prepare.md": DEMO_LESSON_MD, "README.txt": README_TXT}
        root = _local_path(root)
        _reject_symlink_components(root)
        os.makedirs(root, exist_ok=True)
        targets = {MANIFEST_NAME: template, **files}
        # Preflight all existing targets before writing any template file.
        for rel in targets:
            target = safe_join(root, rel)
            if os.path.exists(target) and not force:
                raise ValueError(f"文件已存在：{rel}；仅显式 --force 可覆盖")
            if os.path.exists(target) and not os.path.isfile(target):
                raise ValueError(f"目标不是普通文件：{rel}")
        for rel, content in targets.items():
            target = safe_join(root, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".zhihe-init-", dir=os.path.dirname(target))
            temporaries.append(temporary)
            with os.fdopen(fd, "w", encoding="utf-8") as destination:
                destination.write(content)
            _atomic_install(temporary, target, force)
            temporaries.remove(temporary)
        print(f"已在 {os.path.abspath(root)} 生成模板；编辑后运行 validate")
        return True
    except (ValueError, OSError) as exc:
        print(f"初始化失败：{exc}")
        return False
    finally:
        for path in temporaries:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("init")
    create.add_argument("root")
    create.add_argument("--force", action="store_true", help="明确确认覆盖已有模板文件")
    check = commands.add_parser("validate")
    check.add_argument("root")
    create_zip = commands.add_parser("pack")
    create_zip.add_argument("root")
    create_zip.add_argument("out")
    create_zip.add_argument("--force", action="store_true", help="明确确认覆盖已有输出 ZIP")
    args = parser.parse_args(argv)
    if args.command == "init":
        good = init(args.root, force=args.force)
    elif args.command == "validate":
        good = validate(args.root)
    else:
        good = pack(args.root, args.out, force=args.force)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
