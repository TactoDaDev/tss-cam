"""Offline tests: the maths (test_rig.lua) and the real extension against a fake receiver (test_ext.lua).

Luau's require can't be swapped inside another module, so the extension is copied to tools/.build_ext.lua with
require(...) renamed to __require(...), which test_ext.lua provides (fake ffi, the real rig module).
Run:  python tools/run_tests.py
"""
import pathlib
import subprocess
import sys

here = pathlib.Path(__file__).resolve().parent
luau = pathlib.Path.home() / ".rokit" / "bin" / "luau.exe"
src = (here.parent / "mod/tss_handcam/lua/ge/extensions/tssHandcam.lua").read_text(encoding="utf-8")
# Luau gives each module its own globals: the copy is wrapped in a function that takes the game's globals as locals
GLOBALS = ["__require", "ui_imgui", "vec3", "quatFromDir", "ui_message", "jsonReadFile", "jsonWriteFile", "castRayStatic",
           "GFXDevice", "core_vehicle_manager", "core_camera", "log"]
body = src.replace("require(", "__require(").replace("pcall(require,", "pcall(__require,")
head = "return function(G)\n" + "".join(f"local {g} = G.{g}\n" for g in GLOBALS)
(here / ".build_ext.lua").write_text(head + body + "\nend\n", encoding="utf-8")
ok = True
for test in ("test_rig.lua", "test_ext.lua"):
    r = subprocess.run([str(luau), test], cwd=here, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    print(f"== {test}\n{out}\n")
    ok = ok and r.returncode == 0 and out.endswith("ALL OK")
sys.exit(0 if ok else 1)
