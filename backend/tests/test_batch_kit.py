"""Small, isolated standard-library regressions for ZIP parsing and the batch CLI.

Run directly with unittest, or pytest this file only. Never imports app/database.
"""
import copy
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
import warnings
import zipfile
from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import batch_kit
import batch_publish_cli as cli
import validation


def item(**updates):
    value = {"title": "工具包", "description": "详细介绍", "lessons": [{"title": "步骤", "content": "正文"}]}
    value.update(updates)
    return value


def make_zip(data=None, files=None, members=None, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            if data is not None:
                archive.writestr("toolkits.json", json.dumps(data, ensure_ascii=False))
            for name, content in (files or {}).items():
                archive.writestr(name, content)
            for name, content in members or []:
                archive.writestr(name, content)
    output.seek(0)
    return output


class CountingZip(zipfile.ZipFile):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opens = Counter()

    def open(self, name, *args, **kwargs):
        self.opens[name.filename if isinstance(name, zipfile.ZipInfo) else name] += 1
        return super().open(name, *args, **kwargs)

    def read(self, *args, **kwargs):
        raise AssertionError("loader must stream using open(), not read()")


class TemporaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()

    def tearDown(self):
        self.temporary.cleanup()


class BatchTests(TemporaryTests):
    def load(self, data, files=None):
        with zipfile.ZipFile(make_zip(data, files)) as archive:
            return batch_kit.load_batch_package(archive, self.stage)

    def test_money_finite_strict_and_rounding(self):
        bad = [None, True, False, [], {}, "", "NaN", "sNaN", "Infinity", "-Infinity",
               float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity"), -1, "9999.001", "1e100"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validation.parse_money(value, 0, 9999)
        self.assertEqual(validation.parse_money("1.005", 0, 9999), Decimal("1.01"))
        self.assertEqual(validation.parse_money(0, 0, 9999), Decimal("0.00"))
        self.assertEqual(validation.parse_money("9999", 0, 9999), Decimal("9999.00"))
        with self.assertRaises(ValueError):
            validation.parse_money("0.001", "0.001", 1)

    def test_raw_items_report_all_indices_without_type_crashes(self):
        values = [None, 7, [], "item", item(title=[]), item(description={}), item(summary=5),
                  item(category=[]), item(level={}), item(tags=["good", 3]), item(lessons={}), item()]
        parsed, errors = self.load(values)
        self.assertEqual([entry["index"] for entry in parsed], [11])
        self.assertEqual([entry["index"] for entry in errors], list(range(11)))
        for entry in errors:
            self.assertEqual(entry["status"], "failed")
            self.assertEqual(entry["message"], entry["error"])
            self.assertIsInstance(entry["title"], str)

    def test_metadata_fields_have_explicit_types_and_lengths(self):
        bad = [{"title": True}, {"title": "x" * 121}, {"description": None},
               {"description": "x" * 20001}, {"summary": []}, {"summary": "x" * 501},
               {"category": "unknown"}, {"category": None}, {"level": "unknown"},
               {"level": "x" * 21}, {"price": "NaN"}, {"price": True}, {"price": None},
               {"price": float("nan")}, {"price": float("inf")}, {"price": []},
               {"tags": "x" * 301}, {"tags": [None]}, {"featured": "true"},
               {"lessons": None}, {"lessons": [item()["lessons"][0]] * 51},
               {"cover": []}, {"resource": "https://example.test/resource.zip"}]
        for update in bad:
            with self.subTest(update=update):
                parsed, errors = self.load([item(**update), item(title="有效")])
                self.assertEqual([entry["index"] for entry in parsed], [1])
                self.assertEqual([entry["index"] for entry in errors], [0])

    def test_lesson_types_sources_duration_and_preview(self):
        bad = [None, [], {"title": None, "content": "x"}, {"title": "x" * 161, "content": "x"},
               {"title": "x", "content": []}, {"title": "x", "content": None},
               {"title": "x"}, {"title": "x", "content": "x", "contentFile": "body.md"},
               {"title": "x", "content": "", "contentFile": "body.md"},
               {"title": "x", "contentFile": False}, {"title": "x", "contentFile": "https://example.test/a.md"}]
        for duration in [0, 601, "10", 1.5, True, None, {}, []]:
            bad.append({"title": "x", "content": "x", "duration": duration})
        for preview in ["false", "true", 0, 1, None, [], {}]:
            bad.append({"title": "x", "content": "x", "preview": preview})
        bad.append({"title": "x", "content": "x", "image": {}})
        for lesson in bad:
            with self.subTest(lesson=lesson):
                parsed, errors = self.load([item(lessons=[lesson]), item(title="有效")], {"body.md": "body"})
                self.assertEqual([entry["index"] for entry in parsed], [1])
                self.assertEqual(len(errors), 1)

    def test_preview_omitted_defaults_and_explicit_false_preserved(self):
        lessons = [{"title": "one", "content": "x"}, {"title": "two", "content": "x"},
                   {"title": "three", "content": "x", "preview": True}]
        parsed, errors = self.load([item(lessons=lessons), item(lessons=[{"title": "explicit", "content": "x", "preview": False}])])
        self.assertFalse(errors)
        self.assertEqual([lesson["preview"] for lesson in parsed[0]["lessons"]], [True, False, True])
        self.assertIs(parsed[1]["lessons"][0]["preview"], False)

    def test_top_level_and_batch_limits(self):
        for data in [None, True, 2, "json", {}, {"toolkits": {}}, [], [item()] * 51]:
            with self.subTest(data=str(data)[:40]):
                body = make_zip(data) if data is not None else make_zip(files={"toolkits.json": "null"})
                with zipfile.ZipFile(body) as archive, self.assertRaises(ValueError):
                    batch_kit.load_batch_package(archive, self.stage)
        parsed, errors = self.load({"toolkits": [item()]})
        self.assertEqual(len(parsed), 1)
        self.assertFalse(errors)

    def test_missing_empty_invalid_utf8_and_oversize_content_files(self):
        for files in [{}, {"body.md": b""}, {"body.md": b"\xff"}, {"body.md": b"x" * (1024 * 1024 + 1)}]:
            with self.subTest(size=len(files.get("body.md", b""))):
                parsed, errors = self.load([item(lessons=[{"title": "x", "contentFile": "body.md"}]), item(title="有效")], files)
                self.assertEqual([entry["index"] for entry in parsed], [1])
                self.assertEqual(len(errors), 1)

    def test_template_and_long_chinese_roundtrip(self):
        with zipfile.ZipFile(batch_kit.build_template_zip()) as archive:
            parsed, errors = batch_kit.load_batch_package(archive, self.stage)
        self.assertFalse(errors)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["resource_name"], "gsea_helper.sh")
        self.assertIsInstance(parsed[0]["price"], Decimal)
        long_body = "## 长中文正文\n\n" + "科研步骤与参数说明。" * 30000
        parsed, errors = self.load([item(lessons=[{"title": "长正文", "contentFile": "body.md"}])], {"body.md": long_body})
        self.assertFalse(errors)
        self.assertEqual(parsed[0]["lessons"][0]["content"], long_body)

    def test_image_url_and_local_image_rules(self):
        parsed, errors = self.load([item(cover="https://example.test/image?id=1", lessons=[{"title": "x", "content": "x", "image": "http://example.test/a.png"}])])
        self.assertFalse(errors)
        self.assertEqual(parsed[0]["cover_url"], "https://example.test/image?id=1")
        for cover in ["https://", "http://user:pass@example.test/x", "https://example.test:99999/a", "http://example.test/a b", "javascript:bad", "cover.txt"]:
            with self.subTest(cover=cover):
                parsed, errors = self.load([item(cover=cover)])
                self.assertFalse(parsed)
                self.assertEqual(len(errors), 1)

    def test_all_unsafe_member_paths_rejected(self):
        for name in ["/absolute", "../outside", "dir/../outside", "C:/windows", "C:windows", "dir\\file", "name:stream"]:
            with self.subTest(name=name), zipfile.ZipFile(make_zip([item()], members=[(name, b"x")])) as archive:
                with self.assertRaises(ValueError):
                    batch_kit.load_batch_package(archive, self.stage)
        self.assertFalse(list(self.stage.iterdir()))

    def test_symlink_and_special_members_rejected(self):
        for mode in [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600, stat.S_IFSOCK | 0o600]:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = mode << 16
            with self.subTest(mode=mode), zipfile.ZipFile(make_zip([item()], members=[(info, "target")])) as archive:
                with self.assertRaises(ValueError):
                    batch_kit.load_batch_package(archive, self.stage)

    def test_duplicate_normalized_names_rejected(self):
        for names in [("a.bin", "a.bin"), ("dir/./a.bin", "dir/a.bin"), ("dir//a.bin", "dir/a.bin")]:
            with self.subTest(names=names), zipfile.ZipFile(make_zip([item()], members=[(name, b"x") for name in names])) as archive:
                with self.assertRaisesRegex(ValueError, "重复"):
                    batch_kit.load_batch_package(archive, self.stage)

    def test_crc_failure_in_unreferenced_member_rejected_and_staging_cleaned(self):
        body = make_zip([item()], {"unreferenced.bin": b"small original payload"})
        with zipfile.ZipFile(body) as archive:
            info = archive.getinfo("unreferenced.bin")
            offset = info.header_offset
        raw = bytearray(body.getvalue())
        name_len, extra_len = struct.unpack_from("<HH", raw, offset + 26)
        raw[offset + 30 + name_len + extra_len] ^= 1
        with zipfile.ZipFile(io.BytesIO(raw)) as archive, self.assertRaisesRegex(ValueError, "损坏"):
            batch_kit.load_batch_package(archive, self.stage)
        self.assertFalse(list(self.stage.iterdir()))

    def test_corrupt_deflate_encryption_and_directory_mismatch(self):
        body = make_zip([item()], {"unreferenced.bin": b"some small text" * 20}, compression=zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(body) as archive:
            offset = archive.getinfo("unreferenced.bin").header_offset
        raw = bytearray(body.getvalue())
        name_len, extra_len = struct.unpack_from("<HH", raw, offset + 26)
        # DEFLATE BTYPE=3 is reserved and deterministically raises zlib.error.
        raw[offset + 30 + name_len + extra_len] = 0x07
        with zipfile.ZipFile(io.BytesIO(raw)) as archive, self.assertRaises(ValueError):
            batch_kit.load_batch_package(archive, self.stage)
        with zipfile.ZipFile(make_zip([item()])) as archive:
            archive.getinfo("toolkits.json").flag_bits |= 1
            with self.assertRaisesRegex(ValueError, "加密"):
                batch_kit.load_batch_package(archive, self.stage)
        info = zipfile.ZipInfo("not-a-directory")
        info.create_system = 3
        info.external_attr = (stat.S_IFDIR | 0o755) << 16
        with zipfile.ZipFile(make_zip([item()], members=[(info, b"")])) as archive:
            with self.assertRaises(ValueError):
                batch_kit.load_batch_package(archive, self.stage)

    def test_member_declared_size_and_manifest_actual_budget(self):
        manifest = json.dumps([item()]).encode()
        info = zipfile.ZipInfo("toolkits.json")
        info.file_size = len(manifest)
        class UnderstatedManifest:
            fp = io.BytesIO(b"small zip")
            def infolist(self):
                return [info]
            def open(self, info, mode):
                return io.BytesIO(manifest + b" " * 1024)
        with mock.patch.object(batch_kit, "MAX_MANIFEST_BYTES", len(manifest) + 20), self.assertRaisesRegex(ValueError, "实际"):
            batch_kit.load_batch_package(UnderstatedManifest(), self.stage)
        with zipfile.ZipFile(make_zip([item()], {"large.bin": b"x" * 200})) as archive:
            # Keep manifest small enough to isolate the individual-member limit.
            with mock.patch.object(batch_kit, "MAX_ITEM_BYTES", 199), self.assertRaisesRegex(ValueError, "成员"):
                batch_kit.load_batch_package(archive, self.stage)

    def test_manifest_independent_budget_and_member_count(self):
        body = make_zip([item(description="x" * 300)])
        with mock.patch.object(batch_kit, "MAX_MANIFEST_BYTES", 256), zipfile.ZipFile(body) as archive:
            with self.assertRaisesRegex(ValueError, "清单"):
                batch_kit.load_batch_package(archive, self.stage)
        body = make_zip([item()], {"a": "a", "b": "b", "dir/": ""})
        with mock.patch.object(batch_kit, "MAX_ZIP_MEMBERS", 3), zipfile.ZipFile(body) as archive:
            with self.assertRaisesRegex(ValueError, "成员"):
                batch_kit.load_batch_package(archive, self.stage)

    def test_actual_compressed_and_metadata_budgets(self):
        body = make_zip([item()], {"a": b"123456"})
        size = len(body.getvalue())
        with mock.patch.object(batch_kit, "MAX_ZIP_COMPRESSED", size - 1), zipfile.ZipFile(body) as archive:
            with self.assertRaises(ValueError):
                batch_kit.load_batch_package(archive, self.stage)
        with mock.patch.object(batch_kit, "MAX_ZIP_UNCOMPRESSED", 10), zipfile.ZipFile(body) as archive:
            with self.assertRaises(ValueError):
                batch_kit.load_batch_package(archive, self.stage)

    def test_actual_stream_is_bounded_even_when_metadata_lies(self):
        manifest = json.dumps([item()]).encode()
        manifest_info = zipfile.ZipInfo("toolkits.json")
        manifest_info.file_size = len(manifest)
        asset_info = zipfile.ZipInfo("a.bin")
        asset_info.file_size = 1
        class LyingZip:
            fp = io.BytesIO(b"small compressed body")
            def infolist(self):
                return [manifest_info, asset_info]
            def open(self, info, mode):
                return io.BytesIO(manifest if info.filename == "toolkits.json" else b"x" * 1025)
        with mock.patch.object(batch_kit, "MAX_ITEM_BYTES", 1024), self.assertRaisesRegex(ValueError, "实际"):
            batch_kit.load_batch_package(LyingZip(), self.stage)
        self.assertFalse(list(self.stage.iterdir()))
        with mock.patch.object(batch_kit, "MAX_ZIP_UNCOMPRESSED", len(manifest) + 20), self.assertRaisesRegex(ValueError, "实际"):
            batch_kit.load_batch_package(LyingZip(), self.stage)

    def test_every_member_read_once_and_asset_memory_not_multiplied(self):
        lessons = [{"title": "step", "contentFile": "body.md", "image": "asset.svg"} for _ in range(50)]
        data = [item(cover="./asset.svg", resource="asset.svg", lessons=lessons)]
        body = make_zip(data, {"asset.svg": b"x" * (2 * 1024 * 1024), "body.md": "正文\n" * 1000})
        with CountingZip(body) as archive:
            tracemalloc.start()
            try:
                parsed, errors = batch_kit.load_batch_package(archive, self.stage)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertFalse(errors)
            self.assertEqual(archive.opens, Counter({"toolkits.json": 1, "asset.svg": 1, "body.md": 1}))
        descriptor = parsed[0]["cover_file"]
        self.assertIs(descriptor, parsed[0]["resource_file"])
        for lesson in parsed[0]["lessons"]:
            self.assertIs(descriptor, lesson["image_file"])
            self.assertIs(lesson["content"], parsed[0]["lessons"][0]["content"])
        self.assertEqual(set(descriptor), {"name", "path", "size", "cache_key"})
        self.assertTrue(Path(descriptor["path"]).is_relative_to(self.stage))
        self.assertLess(peak, 1024 * 1024)
        self.assertNotIn("cover_bytes", parsed[0])

    def test_text_budget_reuses_normalized_body_path(self):
        lessons = [{"title": "x", "contentFile": "./body.md"} for _ in range(50)]
        with mock.patch.object(batch_kit, "MAX_PARSED_TEXT_BYTES", 800):
            parsed, errors = self.load([item(lessons=lessons)], {"body.md": "汉字" * 80})
        self.assertFalse(errors)
        self.assertEqual(len(parsed[0]["lessons"]), 50)
        lessons = [{"title": "x", "contentFile": name} for name in ["a.md", "b.md"]]
        with mock.patch.object(batch_kit, "MAX_PARSED_TEXT_BYTES", 800):
            parsed, errors = self.load([item(lessons=lessons)], {"a.md": "汉字" * 80, "b.md": "汉字" * 80})
        self.assertFalse(parsed)
        self.assertIn("解析文本", errors[0]["message"])

    def test_one_level_manifest_and_reference_scope(self):
        data = json.dumps([item(resource="a.bin")])
        with zipfile.ZipFile(make_zip(files={"folder/toolkits.json": data, "folder/a.bin": "x"})) as archive:
            parsed, errors = batch_kit.load_batch_package(archive, self.stage)
        self.assertFalse(errors)
        self.assertEqual(parsed[0]["resource_file"]["cache_key"], "folder/a.bin")
        with zipfile.ZipFile(make_zip(files={"one/toolkits.json": data, "two/toolkits.json": data})) as archive:
            with self.assertRaises(ValueError):
                batch_kit.load_batch_package(archive, self.stage)


class CliTests(TemporaryTests):
    def test_cli_safe_join_rejects_symlinks_and_windows_ambiguity(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "body.md").write_text("private", encoding="utf-8")
        local = self.root / "local"
        local.mkdir()
        (local / "link").symlink_to(outside, target_is_directory=True)
        (local / "file.md").symlink_to(outside / "body.md")
        for rel in ["../outside/body.md", "/absolute", "C:/windows", "C:windows", "dir\\file", "link/body.md", "file.md"]:
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                cli.safe_join(local, rel)
        (self.root / "root-link").symlink_to(local, target_is_directory=True)
        with self.assertRaises(ValueError):
            cli.safe_join(self.root / "root-link", "body.md")

    def test_cli_secret_exclusion_output_exclusion_and_roundtrip(self):
        work = self.root / "work"
        self.assertTrue(cli.init(work))
        (work / ".env").write_text("TEST_SECRET_NOT_REAL", encoding="utf-8")
        (work / "notes.txt").write_text("not referenced", encoding="utf-8")
        (work / "resources" / "private.env").write_text("TEST_SECRET_NOT_REAL", encoding="utf-8")
        output = work / "batch.zip"
        self.assertTrue(cli.validate(work))
        self.assertTrue(cli.pack(work, output))
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {"toolkits.json", "covers/demo-cover.svg", "resources/gsea_helper.sh", "lessons/01-prepare.md"})
            parsed, errors = batch_kit.load_batch_package(archive, self.stage)
        self.assertFalse(errors)
        self.assertEqual(len(parsed), 1)
        before = output.read_bytes()
        self.assertFalse(cli.pack(work, output))
        self.assertEqual(output.read_bytes(), before)
        self.assertFalse(cli.init(work))
        self.assertTrue(cli.pack(work, output, force=True))

    def test_cli_explicit_self_reference_rejected_even_force(self):
        work = self.root / "work"
        self.assertTrue(cli.init(work))
        output = work / "output.zip"
        output.write_bytes(b"owned temporary output")
        manifest = json.loads((work / "toolkits.json").read_text(encoding="utf-8"))
        manifest[0]["resource"] = "./output.zip"
        (work / "toolkits.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertFalse(cli.pack(work, output, force=True))
        self.assertEqual(output.read_bytes(), b"owned temporary output")

    def test_cli_shared_validation_rejects_bad_items_and_symlinks(self):
        work = self.root / "work"
        work.mkdir()
        for data in [[None], [item(price="NaN")], [item(resource="http://example.test/a.zip")],
                     [item(lessons=[{"title": "x", "content": "x", "preview": "false"}])],
                     [item(lessons=[{"title": "x", "content": "x", "duration": 601}])],
                     [item(lessons=[{"title": "x", "content": "x"}] * 51)]]:
            with self.subTest(data=data):
                (work / "toolkits.json").write_text(json.dumps(data), encoding="utf-8")
                self.assertFalse(cli.validate(work))
        target = self.root / "target.md"
        target.write_text("text", encoding="utf-8")
        (work / "linked.md").symlink_to(target)
        (work / "toolkits.json").write_text(json.dumps([item(lessons=[{"title": "x", "contentFile": "linked.md"}])]), encoding="utf-8")
        self.assertFalse(cli.validate(work))

    def test_cli_output_failure_is_atomic_and_leaves_no_temp(self):
        work = self.root / "work"
        self.assertTrue(cli.init(work))
        output = work / "output.zip"
        output.write_bytes(b"owned temporary output")
        with mock.patch.object(cli, "_atomic_install", side_effect=OSError("injected write fault")):
            self.assertFalse(cli.pack(work, output, force=True))
        self.assertEqual(output.read_bytes(), b"owned temporary output")
        self.assertFalse(list(work.glob(".zhihe-batch-*.tmp")))
        missing = work / "new.zip"
        with mock.patch.object(cli, "_atomic_install", side_effect=OSError("injected write fault")):
            self.assertFalse(cli.pack(work, missing))
        self.assertFalse(missing.exists())

    def test_cli_root_output_paths_and_reference_escape_rejected(self):
        for root in ["C:ambiguous", "C:/ambiguous", "bad\\directory", "name:stream"]:
            with self.subTest(root=root), self.assertRaises(ValueError):
                cli.safe_join(root, "body.md")
        work = self.root / "work"
        self.assertTrue(cli.init(work))
        for output in ["C:out.zip", "bad\\out.zip"]:
            with self.subTest(output=output):
                self.assertFalse(cli.pack(work, output))
        outside = self.root / "outside.zip"
        outside.write_bytes(b"owned private fixture")
        target = work / "linked-output.zip"
        target.symlink_to(outside)
        self.assertFalse(cli.pack(work, target, force=True))
        self.assertEqual(outside.read_bytes(), b"owned private fixture")
        (work / "toolkits.json").write_text(json.dumps([item(resource="../outside.zip")]), encoding="utf-8")
        self.assertFalse(cli.validate(work))
        self.assertFalse(cli.pack(work, work / "new.zip"))
        self.assertFalse((work / "new.zip").exists())

    def test_cli_long_chinese_body_init_validate_pack_roundtrip(self):
        work = self.root / "work"
        self.assertTrue(cli.init(work))
        body = "## 中文正文\n" + "长中文科研说明。" * 30000
        (work / "lessons" / "01-prepare.md").write_text(body, encoding="utf-8")
        self.assertTrue(cli.validate(work))
        output = self.root / "long.zip"
        self.assertTrue(cli.pack(work, output))
        with zipfile.ZipFile(output) as archive:
            parsed, errors = batch_kit.load_batch_package(archive, self.stage)
        self.assertFalse(errors)
        self.assertEqual(parsed[0]["lessons"][0]["content"], body)

    def test_cli_reference_changed_after_validation_cannot_replace_output(self):
        work = self.root / "work"
        self.assertTrue(cli.init(work))
        output = work / "owned-output.zip"
        output.write_bytes(b"owned output")
        original = cli.inspect
        def changed_inspect(root):
            result = original(root)
            (work / "resources" / "gsea_helper.sh").write_text("changed during pack", encoding="utf-8")
            return result
        with mock.patch.object(cli, "inspect", side_effect=changed_inspect):
            self.assertFalse(cli.pack(work, output, force=True))
        self.assertEqual(output.read_bytes(), b"owned output")
        self.assertFalse(list(work.glob(".zhihe-batch-*.tmp")))

    def test_downloaded_cli_runs_outside_project_with_only_stdlib(self):
        external = self.root / "external"
        external.mkdir()
        scripts = external / "scripts"
        scripts.mkdir()
        with zipfile.ZipFile(batch_kit.build_skill_zip()) as archive:
            for name in ["build_batch.py", "validation.py"]:
                (scripts / name).write_bytes(archive.read("zhihe-toolkit-converter/scripts/" + name))
        script = scripts / "build_batch.py"
        env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME")}
        work = external / "work"
        output = external / "output.zip"
        for arguments in [["init", str(work)], ["validate", str(work)], ["pack", str(work), str(output)]]:
            result = subprocess.run([sys.executable, "-S", str(script), *arguments], cwd=external, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([sys.executable, "-S", str(script), "pack", str(work), str(output)], cwd=external, env=env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        with zipfile.ZipFile(output) as archive:
            parsed, errors = batch_kit.load_batch_package(archive, self.stage)
        self.assertFalse(errors)
        self.assertEqual(len(parsed), 1)


if __name__ == "__main__":
    unittest.main()
