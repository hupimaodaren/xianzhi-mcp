"""自动发现逻辑测试（用临时目录构造场景，不依赖本机具体 db）"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xianzhi_mcp

TXT_DB = "xianzhi.db"
IMG_DB = "xianzhi_img.db"


def _mk_dummy(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("dummy", encoding="utf-8")


def test_explicit_file():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / TXT_DB
        _mk_dummy(db)
        r = xianzhi_mcp.find_db_path(str(db))
        assert r == db.resolve(), f"显式文件应原样命中, got {r}"
    print("1) 显式 db 文件            OK")


def test_explicit_dir_prefers_image():
    with tempfile.TemporaryDirectory() as tmp:
        _mk_dummy(Path(tmp) / TXT_DB)
        _mk_dummy(Path(tmp) / IMG_DB)
        r = xianzhi_mcp.find_db_path(tmp)
        assert r.name == IMG_DB, f"目录内应优先图文版, got {r.name}"
    print("2) 显式目录优先图文版      OK")


def test_env_var():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / TXT_DB
        _mk_dummy(db)
        os.environ["XIANZHI_DB"] = str(db)
        try:
            r = xianzhi_mcp.find_db_path(None)
            assert r == db.resolve(), f"环境变量应命中, got {r}"
        finally:
            del os.environ["XIANZHI_DB"]
    print("3) XIANZHI_DB 环境变量     OK")


def test_sibling_dir():
    """模拟：db 与脚本/exe 同级。"""
    with tempfile.TemporaryDirectory() as tmp:
        _mk_dummy(Path(tmp) / TXT_DB)
        old_argv0 = sys.argv[0]
        sys.argv[0] = str(Path(tmp) / "fake.py")
        # 清掉可能干扰的环境变量
        saved = {k: os.environ.pop(k, None) for k in ("XIANZHI_DB", "XIANZHI_HOME")}
        try:
            r = xianzhi_mcp.find_db_path(None)
            assert r.parent == Path(tmp).resolve(), f"应命中同级目录, got {r}"
        finally:
            sys.argv[0] = old_argv0
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
    print("4) 脚本同级目录            OK")


def test_packed_dir():
    """模拟：便携版 app/resources/data 布局。"""
    with tempfile.TemporaryDirectory() as tmp:
        packed = Path(tmp) / "app" / "resources" / "data"
        _mk_dummy(packed / TXT_DB)
        old_argv0 = sys.argv[0]
        sys.argv[0] = str(Path(tmp) / "fake.py")
        saved = {k: os.environ.pop(k, None) for k in ("XIANZHI_DB", "XIANZHI_HOME")}
        try:
            r = xianzhi_mcp.find_db_path(None)
            assert r == (packed / TXT_DB).resolve(), f"应命中包内资源目录, got {r}"
        finally:
            sys.argv[0] = old_argv0
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
    print("5) 便携版 app/resources   OK")


def test_not_found():
    saved = {k: os.environ.pop(k, None) for k in ("XIANZHI_DB", "XIANZHI_HOME")}
    try:
        # 显式指定不存在的路径 → 直接报错
        try:
            xianzhi_mcp.find_db_path(str(Path(tempfile.gettempdir()) / "no_such_xyz.db"))
            assert False, "应抛 FileNotFoundError"
        except FileNotFoundError as e:
            assert "指定的 db 未找到" in str(e), str(e)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("6) 找不到时友好报错        OK")


if __name__ == "__main__":
    test_explicit_file()
    test_explicit_dir_prefers_image()
    test_env_var()
    test_sibling_dir()
    test_packed_dir()
    test_not_found()
    print("\n自动发现测试全部通过")
