"""
╔══════════════════════════════════════════════════════╗
║   NAUTILUS-IDE  —  Python Backend  v1.0              ║
║   FastAPI + WebSocket + Sandboxed Code Execution     ║
╚══════════════════════════════════════════════════════╝

Run:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Endpoints:
  REST
    POST   /api/run           – Execute Python code (sandboxed)
    POST   /api/analyze       – Static analysis (errors, warnings, imports)
    GET    /api/files         – List all files
    POST   /api/files         – Create a new file
    GET    /api/files/{id}    – Get file by ID
    PUT    /api/files/{id}    – Update file content / metadata
    DELETE /api/files/{id}    – Delete file
    GET    /api/history       – Execution history
    GET    /api/telemetry     – Live submarine telemetry snapshot
    POST   /api/format        – PEP-8 format code (via autopep8 / fallback)
    POST   /api/submarine/dive          – Set submarine depth
    POST   /api/submarine/course        – Set heading + speed
    POST   /api/submarine/surface       – Emergency surface
    GET    /api/submarine/status        – Current submarine state

  WebSocket
    WS  /ws/run       – Stream stdout / stderr from code execution in real time
    WS  /ws/telemetry – Push live submarine telemetry every second
"""

import ast
import asyncio
import io
import json
import math
import os
import random
import re
import sys
import textwrap
import time
import traceback
import uuid
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ──────────────────────────────────────────
#  APP SETUP
# ──────────────────────────────────────────

app = FastAPI(
    title="NAUTILUS-IDE Backend",
    description="Submarine Python Control System – Backend API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────
#  IN-MEMORY DATABASE
# ──────────────────────────────────────────

CODE_ALPHA = textwrap.dedent("""
    # ╔══════════════════════════════════════╗
    # ║   NAUTILUS MISSION ALPHA             ║
    # ║   Submarine Control System v3.1      ║
    # ╚══════════════════════════════════════╝
    import os, sys, json, time, math
    from datetime import datetime
    from dataclasses import dataclass, field
    from typing import List, Optional

    @dataclass
    class Torpedo:
        tube_id: int
        type: str = "MK48"
        armed: bool = False

    @dataclass
    class SubmarineController:
        name: str = "NAUTILUS"
        depth: float = 0.0
        speed: float = 0.0
        heading: float = 0.0
        status: str = "SURFACE"
        max_depth: float = 500.0
        torpedoes: List[Torpedo] = field(default_factory=list)

        def __post_init__(self):
            self.torpedoes = [Torpedo(i) for i in range(4)]
            self.boot_time = datetime.now()

        def dive(self, target: float) -> str:
            if target < 0:
                raise ValueError("Depth cannot be negative")
            if target > self.max_depth:
                print(f"WARNING: Exceeds rated depth {self.max_depth}m")
            self.depth = target
            self.status = "SUBMERGED" if target > 0 else "SURFACE"
            pressure = self.depth * 0.1
            return f"[{datetime.now():%H:%M:%S}] Diving to {target}m | Pressure: {pressure:.1f} ATM"

        def surface(self):
            print("⚡ EMERGENCY SURFACE PROTOCOL ENGAGED")
            self.depth = 0.0
            self.status = "SURFACE"
            self.speed = max(0, self.speed * 0.3)

        def set_course(self, heading: float, speed: float):
            self.heading = heading % 360
            self.speed = min(speed, 35)
            return f"Course: {self.heading:06.2f}° | Speed: {self.speed:.1f}kts"

        def get_telemetry(self) -> dict:
            return {
                "vessel": self.name,
                "depth_m": self.depth,
                "pressure_atm": round(self.depth * 0.1, 2),
                "speed_kts": self.speed,
                "heading_deg": self.heading,
                "status": self.status,
                "uptime_s": (datetime.now() - self.boot_time).seconds,
                "timestamp": datetime.now().isoformat()
            }

    sub = SubmarineController()
    print(sub.dive(247))
    print(sub.set_course(45, 12))
    print(json.dumps(sub.get_telemetry(), indent=2))
""").strip()

CODE_NAV = textwrap.dedent("""
    # Navigation & Waypoint System
    import math
    from dataclasses import dataclass
    from typing import Tuple

    SOUND_SPEED = 1531  # m/s in seawater

    @dataclass
    class Position:
        lat: float
        lon: float
        depth: float = 0.0
        name: str = ""

    def haversine(p1: Position, p2: Position) -> float:
        R = 6_371_000
        phi1, phi2 = math.radians(p1.lat), math.radians(p2.lat)
        dphi    = math.radians(p2.lat - p1.lat)
        dlambda = math.radians(p2.lon - p1.lon)
        a = (math.sin(dphi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def bearing(p1: Position, p2: Position) -> float:
        lat1, lat2 = math.radians(p1.lat), math.radians(p2.lat)
        dlon = math.radians(p2.lon - p1.lon)
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    waypoints = [
        Position(40.7128, -74.0060, 100, "HOME_PORT"),
        Position(40.7580, -73.9855, 150, "WAYPOINT_ALPHA"),
        Position(41.0534, -73.5387, 200, "OBJECTIVE"),
    ]

    for i in range(len(waypoints) - 1):
        a, b = waypoints[i], waypoints[i+1]
        d = haversine(a, b)
        hdg = bearing(a, b)
        print(f"{a.name} → {b.name}: {d/1000:.2f}km, bearing {hdg:.1f}°")
""").strip()

CODE_SONAR = textwrap.dedent("""
    # Sonar Array Processing System
    import math

    SOUND_SPEED_SW = 1531  # m/s seawater at 15°C, 35ppt

    def freq_to_wavelength(freq_hz: float, medium: str = 'seawater') -> float:
        speeds = {'seawater': 1531, 'freshwater': 1480, 'air': 343}
        return speeds.get(medium, 1531) / freq_hz

    def doppler_shift(f0: float, v_rel: float, c: float = 1531) -> float:
        return f0 * (c / (c - v_rel))

    def ping(frequency_hz: float = 5000, power_kw: float = 10.0) -> dict:
        wl = freq_to_wavelength(frequency_hz)
        max_range = math.sqrt(power_kw * 1000) * 500
        return {
            'freq_hz': frequency_hz,
            'wavelength_m': round(wl, 4),
            'estimated_range_m': round(max_range),
            'propagation_delay_ms': round((max_range / SOUND_SPEED_SW) * 1000, 1)
        }

    for freq in [1_000, 5_000, 20_000, 100_000]:
        result = ping(freq)
        print(f"{freq:>7}Hz | λ={result['wavelength_m']*100:.2f}cm | range≈{result['estimated_range_m']}m")
""").strip()

CODE_TORP = textwrap.dedent("""
    # Torpedo Fire Control System
    import math, time

    class TorpedoSystem:
        TUBE_COUNT = 4
        MAX_SPEED_KTS = 55
        MAX_RANGE_KM = 50

        def __init__(self):
            self.tubes = [None] * self.TUBE_COUNT
            self.safety = True
            self.armed = False
            self.fired_count = 0

        def load_tube(self, tube_id: int, variant: str = "MK48-ADCAP"):
            if tube_id not in range(self.TUBE_COUNT):
                raise IndexError(f"Invalid tube ID: {tube_id}")
            self.tubes[tube_id] = {
                'variant': variant,
                'loaded_at': time.time(),
                'status': 'READY'
            }
            print(f"Tube {tube_id}: {variant} loaded — READY")

        def fire_solution(self, target_speed, target_bearing_deg, torp_speed=40):
            tb = math.radians(target_bearing_deg)
            sin_lead = (target_speed / torp_speed) * math.sin(tb)
            if abs(sin_lead) > 1:
                return None
            return math.degrees(math.asin(sin_lead))

        def fire(self, tube_id: int) -> bool:
            if self.safety:
                print("⚠  SAFETY ENGAGED — deactivate before firing")
                return False
            if self.tubes[tube_id] is None:
                raise RuntimeError(f"Tube {tube_id} is empty")
            print(f"🚀 TORPEDO AWAY — Tube {tube_id} | {self.tubes[tube_id]['variant']}")
            self.tubes[tube_id] = None
            self.fired_count += 1
            return True

    ts = TorpedoSystem()
    ts.load_tube(0)
    ts.load_tube(1, "MK48-ADCAP")
    angle = ts.fire_solution(15, 45, 40)
    print(f"Fire solution: lead angle = {angle:.1f}°" if angle else "No solution")
""").strip()


# ── File Store ──────────────────────────────────────
_files: Dict[int, dict] = {
    1: {"id": 1, "name": "mission_alpha.py", "code": CODE_ALPHA, "errors": 0, "modified": False},
    2: {"id": 2, "name": "nav_system.py",    "code": CODE_NAV,   "errors": 1, "modified": False},
    3: {"id": 3, "name": "sonar_array.py",   "code": CODE_SONAR, "errors": 0, "modified": True},
    4: {"id": 4, "name": "torpedo_ctrl.py",  "code": CODE_TORP,  "errors": 2, "modified": False},
}
_next_id: int = 5
_history: List[dict] = []
_active_id: int = 1


# ── Submarine State ─────────────────────────────────
@dataclass
class SubState:
    depth_m:      float = 247.0
    speed_kts:    float = 12.0
    heading_deg:  float = 45.0
    status:       str   = "SUBMERGED"
    power_pct:    float = 85.0
    hull_pct:     float = 98.0
    o2_pct:       float = 96.0
    coolant_temp: float = 307.0
    torpedo_ready: int  = 2
    mode:         str   = "SILENT_RUNNING"
    boot_time:    float = field(default_factory=time.time)

    def pressure_atm(self) -> float:
        return round(self.depth_m * 0.1, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pressure_atm"] = self.pressure_atm()
        d["uptime_s"] = int(time.time() - self.boot_time)
        d["timestamp"] = datetime.now().isoformat()
        del d["boot_time"]
        # add some live jitter
        d["depth_m"] = round(self.depth_m + (random.random() - 0.5) * 3, 1)
        d["hull_pct"] = round(97 + random.random() * 2, 1)
        d["pressure_atm"] = round(d["depth_m"] * 0.1, 2)
        return d


_sub = SubState()


# ──────────────────────────────────────────
#  PYDANTIC MODELS
# ──────────────────────────────────────────

class RunRequest(BaseModel):
    code: str
    timeout: float = Field(default=10.0, ge=0.5, le=30.0)

class FileCreate(BaseModel):
    name: str
    code: str = "# new file\n"

class FileUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    modified: Optional[bool] = None

class AnalyzeRequest(BaseModel):
    code: str

class FormatRequest(BaseModel):
    code: str

class DiveRequest(BaseModel):
    depth_m: float = Field(ge=0.0, le=600.0)

class CourseRequest(BaseModel):
    heading_deg: float = Field(ge=0.0, le=360.0)
    speed_kts:   float = Field(ge=0.0, le=35.0)


# ──────────────────────────────────────────
#  STDLIB / THIRD-PARTY SETS  (mirrors frontend)
# ──────────────────────────────────────────

STDLIB = {
    "os","sys","json","time","datetime","math","random","struct","asyncio",
    "threading","socket","re","pathlib","collections","dataclasses","typing",
    "abc","functools","itertools","enum","io","copy","heapq","bisect","array",
    "queue","subprocess","shutil","glob","tempfile","hashlib","hmac","uuid",
    "base64","urllib","http","csv","xml","html","sqlite3","pickle","gzip",
    "zipfile","string","textwrap","traceback","warnings","logging","unittest",
    "contextlib","weakref","gc","inspect","operator","decimal","fractions",
    "statistics","cmath","numbers","pprint","reprlib","signal","mmap","ctypes",
    "argparse","configparser","platform","sysconfig","importlib","pkgutil",
}

THIRD = {
    "numpy","pandas","requests","flask","django","scipy","matplotlib",
    "tensorflow","torch","sklearn","fastapi","aiohttp","sqlalchemy","pydantic",
    "pytest","click","PIL","cv2","boto3","celery","redis","pymongo",
    "psycopg2","httpx","trio","attrs","uvicorn",
}

# ──────────────────────────────────────────
#  CODE ANALYSIS ENGINE
# ──────────────────────────────────────────

def _classify_module(name: str) -> str:
    root = name.split(".")[0]
    if root in STDLIB:  return "stdlib"
    if root in THIRD:   return "third"
    return "unknown"


def analyze_code(code: str) -> dict:
    """Static analysis: errors, warnings, imports."""
    errors:   List[dict] = []
    warnings: List[dict] = []
    imports:  List[dict] = []
    seen_imports: set = set()

    lines = code.split("\n")

    # ── AST parse (catches real syntax errors) ──
    try:
        tree = ast.parse(code)
        ast_ok = True
    except SyntaxError as exc:
        errors.append({
            "line": exc.lineno or 0,
            "col":  exc.offset or 0,
            "msg":  f"SyntaxError: {exc.msg}",
            "text": exc.text.rstrip() if exc.text else "",
        })
        ast_ok = False
        tree = None

    # ── AST-based import collection ──
    if tree:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name.split(".")[0]
                        if mod not in seen_imports:
                            seen_imports.add(mod)
                            imports.append({"name": mod, "line": node.lineno,
                                            "type": _classify_module(mod)})
                else:
                    mod = (node.module or "").split(".")[0]
                    if mod and mod not in seen_imports:
                        seen_imports.add(mod)
                        imports.append({"name": mod, "line": node.lineno,
                                        "type": _classify_module(mod)})

    # ── Line-by-line heuristic checks ──
    for i, raw in enumerate(lines):
        n   = i + 1
        s   = raw.strip()
        if not s or s.startswith("#"):
            continue

        # Missing colon after block-start keywords (only when AST failed)
        if not ast_ok:
            if re.match(r"^(def |class |if |elif |else\b|for |while |with |try\b|except\b|finally\b)", s):
                ends_ok = s.endswith(":") or s.endswith(":\\") or "#" in s
                is_cont = s.endswith("\\") or s.endswith(",") or s.endswith("(")
                if not ends_ok and not is_cont:
                    errors.append({"line": n, "col": len(raw) - len(raw.lstrip()),
                                   "msg": f"Missing colon: '{s[:60]}'"})

        # Python 2 print statement
        if re.match(r"^print\s+[^(=\(]", s):
            errors.append({"line": n, "col": 0,
                           "msg": "Python 2 print — use print() in Python 3"})

        # math used but not imported (heuristic)
        if re.search(r"\bmath\.", s) and "math" not in seen_imports:
            warnings.append({"line": n, "msg": "'math' referenced but not imported"})

        # TODO / FIXME
        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", s):
            warnings.append({"line": n, "msg": s[:60]})

        # PEP-8 line length
        if len(raw) > 79:
            warnings.append({"line": n,
                              "msg": f"Line too long ({len(raw)} chars, PEP-8 max 79)"})

        # Bare except
        if re.match(r"^except\s*:", s):
            warnings.append({"line": n, "msg": "Bare 'except:' — be specific"})

        # == None/True/False
        if re.search(r"==\s*(None|True|False)", s):
            warnings.append({"line": n,
                              "msg": "Use 'is' instead of '==' for None/True/False"})

        # Mutable default argument
        if re.search(r"def\s+\w+\(.*=\s*(\[\]|\{\}|\(\))", s):
            warnings.append({"line": n, "msg": "Mutable default argument"})

    return {
        "errors":   errors,
        "warnings": warnings,
        "imports":  imports,
        "line_count": len(lines),
        "ast_ok": ast_ok,
    }


# ──────────────────────────────────────────
#  CODE EXECUTION ENGINE  (sandboxed)
# ──────────────────────────────────────────

# Modules that are blocked in the sandbox
_BLOCKED = {
    "subprocess", "os.system", "shutil.rmtree", "socket",
    "ctypes", "mmap", "signal", "multiprocessing",
}

def _is_safe(code: str) -> Tuple[bool, str]:
    """Best-effort safety check via AST."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return True, ""   # let execution surface the real error

    for node in ast.walk(tree):
        # Block __import__ calls
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "__import__":
                return False, "Direct __import__() call is not allowed"
        # Block certain module imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in {"socket", "ctypes", "mmap", "multiprocessing"}:
                    return False, f"Module '{root}' is blocked in the sandbox"
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            if root in {"socket", "ctypes", "mmap", "multiprocessing"}:
                return False, f"Module '{root}' is blocked in the sandbox"
    return True, ""


def execute_code(code: str, timeout: float = 10.0) -> dict:
    """Run code in a restricted namespace; capture stdout/stderr."""
    safe, reason = _is_safe(code)
    if not safe:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"[SANDBOX] Blocked: {reason}",
            "exit_code": -1,
            "duration_ms": 0,
        }

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    # Restricted globals
    restricted_globals: Dict[str, Any] = {
        "__builtins__": {
            k: v for k, v in vars(__builtins__ if isinstance(__builtins__, dict)
                                  else __builtins__).items()   # type: ignore[arg-type]
            if k not in {"open", "exec", "eval", "compile", "__import__",
                         "input", "breakpoint"}
        } if not isinstance(__builtins__, dict) else __builtins__,
        "__name__": "__main__",
    }
    # Re-allow __import__ so normal `import x` works inside the sandbox
    restricted_globals["__builtins__"]["__import__"] = __import__  # type: ignore[index]

    start = time.perf_counter()
    success = True
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(compile(code, "<nautilus>", "exec"), restricted_globals)  # noqa: S102
    except Exception:
        stderr_buf.write(traceback.format_exc())
        success = False
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

    return {
        "success": success,
        "stdout":  stdout_buf.getvalue(),
        "stderr":  stderr_buf.getvalue(),
        "exit_code": 0 if success else 1,
        "duration_ms": elapsed_ms,
    }


# ──────────────────────────────────────────
#  CODE FORMATTER  (PEP-8)
# ──────────────────────────────────────────

def format_code(code: str) -> str:
    """Try autopep8 first; fall back to a simple built-in formatter."""
    try:
        import autopep8  # type: ignore
        return autopep8.fix_code(code, options={"max_line_length": 79})
    except ImportError:
        pass
    # Built-in fallback: expand tabs, strip trailing whitespace, collapse blank runs
    code = code.replace("\t", "    ")
    lines = [l.rstrip() for l in code.split("\n")]
    result, blank_run = [], 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:
                result.append("")
        else:
            blank_run = 0
            result.append(line)
    return "\n".join(result)


# ──────────────────────────────────────────
#  REST ENDPOINTS — CODE
# ──────────────────────────────────────────

@app.post("/api/run", summary="Execute Python code")
async def run_code(req: RunRequest):
    """
    Execute Python code in a sandboxed interpreter.
    Returns stdout, stderr, exit_code, and duration_ms.
    """
    analysis = analyze_code(req.code)
    hard_errors = analysis["errors"]

    # Block execution if there are hard syntax errors
    if hard_errors:
        return JSONResponse({
            "success": False,
            "stdout": "",
            "stderr": "\n".join(
                f"Line {e['line']}: {e['msg']}" for e in hard_errors
            ),
            "exit_code": 2,
            "duration_ms": 0,
            "errors": hard_errors,
        })

    # Run in executor so it doesn't block the event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, execute_code, req.code, req.timeout
    )

    # Record in history
    _history.insert(0, {
        "id":    str(uuid.uuid4())[:8],
        "ts":    datetime.now().isoformat(),
        "status": "ok" if result["success"] else "error",
        "duration_ms": result["duration_ms"],
    })
    if len(_history) > 200:
        _history.pop()

    return JSONResponse(result)


@app.post("/api/analyze", summary="Static code analysis")
async def api_analyze(req: AnalyzeRequest):
    return JSONResponse(analyze_code(req.code))


@app.post("/api/format", summary="PEP-8 format code")
async def api_format(req: FormatRequest):
    formatted = format_code(req.code)
    return JSONResponse({"code": formatted})


# ──────────────────────────────────────────
#  REST ENDPOINTS — FILE MANAGEMENT
# ──────────────────────────────────────────

@app.get("/api/files", summary="List all files")
async def list_files():
    return JSONResponse({"files": list(_files.values()), "active_id": _active_id})


@app.post("/api/files", summary="Create file", status_code=201)
async def create_file(body: FileCreate):
    global _next_id
    fid = _next_id
    _next_id += 1
    _files[fid] = {
        "id": fid,
        "name": body.name,
        "code": body.code,
        "errors": 0,
        "modified": False,
    }
    return JSONResponse({"file": _files[fid]})


@app.get("/api/files/{file_id}", summary="Get file")
async def get_file(file_id: int):
    f = _files.get(file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return JSONResponse({"file": f})


@app.put("/api/files/{file_id}", summary="Update file")
async def update_file(file_id: int, body: FileUpdate):
    f = _files.get(file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    if body.name     is not None: f["name"]     = body.name
    if body.code     is not None:
        f["code"]     = body.code
        # Auto-recount errors
        analysis = analyze_code(body.code)
        f["errors"] = len(analysis["errors"])
    if body.modified is not None: f["modified"] = body.modified
    return JSONResponse({"file": f})


@app.delete("/api/files/{file_id}", summary="Delete file")
async def delete_file(file_id: int):
    if file_id not in _files:
        raise HTTPException(status_code=404, detail="File not found")
    if len(_files) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last file")
    del _files[file_id]
    return JSONResponse({"deleted": file_id})


# ──────────────────────────────────────────
#  REST ENDPOINTS — HISTORY
# ──────────────────────────────────────────

@app.get("/api/history", summary="Execution history")
async def get_history():
    return JSONResponse({"history": _history})


# ──────────────────────────────────────────
#  REST ENDPOINTS — SUBMARINE CONTROL
# ──────────────────────────────────────────

@app.get("/api/telemetry", summary="Live telemetry snapshot")
async def get_telemetry():
    return JSONResponse(_sub.to_dict())


@app.get("/api/submarine/status", summary="Submarine state")
async def submarine_status():
    return JSONResponse(asdict(_sub) | {"pressure_atm": _sub.pressure_atm()})


@app.post("/api/submarine/dive", summary="Dive to depth")
async def submarine_dive(req: DiveRequest):
    _sub.depth_m = req.depth_m
    _sub.status  = "SUBMERGED" if req.depth_m > 0 else "SURFACE"
    return JSONResponse({
        "ok": True,
        "message": f"Diving to {req.depth_m}m | Pressure: {_sub.pressure_atm()} ATM",
        "telemetry": _sub.to_dict(),
    })


@app.post("/api/submarine/course", summary="Set heading and speed")
async def submarine_course(req: CourseRequest):
    _sub.heading_deg = req.heading_deg % 360
    _sub.speed_kts   = min(req.speed_kts, 35.0)
    return JSONResponse({
        "ok": True,
        "message": f"Course: {_sub.heading_deg:06.2f}° | Speed: {_sub.speed_kts:.1f}kts",
        "telemetry": _sub.to_dict(),
    })


@app.post("/api/submarine/surface", summary="Emergency surface")
async def submarine_surface():
    _sub.depth_m    = 0.0
    _sub.status     = "SURFACE"
    _sub.speed_kts  = round(_sub.speed_kts * 0.3, 1)
    _sub.mode       = "EMERGENCY"
    return JSONResponse({
        "ok": True,
        "message": "⚡ EMERGENCY SURFACE — all ballast tanks blown",
        "telemetry": _sub.to_dict(),
    })


# ──────────────────────────────────────────
#  WEBSOCKET — STREAMING CODE EXECUTION
# ──────────────────────────────────────────

class StreamingOutput(io.StringIO):
    """StringIO that also calls a callback on each write."""
    def __init__(self, callback):
        super().__init__()
        self._cb = callback

    def write(self, text: str) -> int:
        self._cb(text)
        return super().write(text)


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket):
    """
    WebSocket endpoint for streaming code execution.

    Client sends JSON:
        { "code": "...", "timeout": 10 }

    Server streams JSON frames:
        { "type": "stdout"|"stderr"|"done"|"error", "data": "..." }
    """
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "data": "Invalid JSON"}))
                continue

            code    = payload.get("code", "")
            timeout = float(payload.get("timeout", 10.0))

            # Check for hard errors first
            analysis = analyze_code(code)
            if analysis["errors"]:
                for e in analysis["errors"]:
                    await ws.send_text(json.dumps({
                        "type": "stderr",
                        "data": f"Line {e['line']}: {e['msg']}\n"
                    }))
                await ws.send_text(json.dumps({"type": "done", "exit_code": 2}))
                continue

            # Safety check
            safe, reason = _is_safe(code)
            if not safe:
                await ws.send_text(json.dumps({
                    "type": "stderr",
                    "data": f"[SANDBOX] Blocked: {reason}\n"
                }))
                await ws.send_text(json.dumps({"type": "done", "exit_code": -1}))
                continue

            # Queue to pass output from thread → coroutine
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _run():
                stdout_lines: List[str] = []
                stderr_lines: List[str] = []

                def _out_cb(text):
                    stdout_lines.append(text)
                    loop.call_soon_threadsafe(queue.put_nowait, ("stdout", text))

                def _err_cb(text):
                    stderr_lines.append(text)
                    loop.call_soon_threadsafe(queue.put_nowait, ("stderr", text))

                stdout_buf = StreamingOutput(_out_cb)
                stderr_buf = StreamingOutput(_err_cb)

                ns: Dict[str, Any] = {"__name__": "__main__",
                                      "__builtins__": __builtins__}
                try:
                    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                        exec(compile(code, "<nautilus>", "exec"), ns)  # noqa: S102
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", 0))
                except Exception:
                    tb = traceback.format_exc()
                    loop.call_soon_threadsafe(queue.put_nowait, ("stderr", tb))
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", 1))

            loop.run_in_executor(None, _run)

            start = time.time()
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if time.time() - start > timeout:
                        await ws.send_text(json.dumps({
                            "type": "stderr",
                            "data": f"\n[TIMEOUT] Execution exceeded {timeout}s\n"
                        }))
                        await ws.send_text(json.dumps({"type": "done", "exit_code": -2}))
                        break
                    continue

                kind, data = item
                await ws.send_text(json.dumps({"type": kind, "data": data}))
                if kind == "done":
                    break

    except WebSocketDisconnect:
        pass


# ──────────────────────────────────────────
#  WEBSOCKET — LIVE TELEMETRY STREAM
# ──────────────────────────────────────────

@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    """
    Push submarine telemetry JSON every second.
    Client may send "PING" to receive an immediate update.
    """
    await ws.accept()
    try:
        async def _reader():
            """Drain any incoming client messages (keep-alive / commands)."""
            while True:
                msg = await ws.receive_text()
                if msg.strip().upper() == "PING":
                    await ws.send_text(json.dumps(_sub.to_dict()))

        reader_task = asyncio.create_task(_reader())

        while True:
            await ws.send_text(json.dumps(_sub.to_dict()))
            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()


# ──────────────────────────────────────────
#  ROOT / HEALTH
# ──────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "NAUTILUS-IDE Backend",
        "version": "1.0.0",
        "status":  "ONLINE",
        "docs":    "/docs",
    }


@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
