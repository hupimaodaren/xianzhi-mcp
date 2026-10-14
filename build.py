"""PyInstaller 打包脚本：生成单文件 xianzhi-mcp.exe"""
import subprocess, sys, os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))

# 先确保 typer 装了（mcp collect-all 需要它）
print("==> 安装 build 依赖...")
subprocess.run([sys.executable, "-m", "pip", "install", "typer"], check=True)

# 清理旧构建
for d in ["build", "dist", "__pycache__"]:
    full = os.path.join(HERE, d)
    if os.path.exists(full):
        shutil.rmtree(full, ignore_errors=True)
spec = os.path.join(HERE, "xianzhi-mcp.spec")
if os.path.exists(spec):
    os.remove(spec)

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--name", "xianzhi-mcp",
    "--clean",
    "--noconfirm",
    "--collect-all", "mcp",
    os.path.join(HERE, "xianzhi_mcp.py"),
]

print(f"==> PyInstaller onefile build...")
result = subprocess.run(cmd, cwd=HERE)
if result.returncode != 0:
    print("BUILD FAILED")
    sys.exit(1)

exe = os.path.join(HERE, "dist", "xianzhi-mcp.exe")
if os.path.exists(exe):
    size_mb = os.path.getsize(exe) / (1024 * 1024)
    print(f"\n✅ BUILD SUCCESS: {exe} ({size_mb:.1f} MB)")
else:
    print("❌ exe 未生成")
    sys.exit(1)
