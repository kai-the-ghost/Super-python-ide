# NAUTILUS-IDE — Python Backend

A full **FastAPI** backend that powers every feature of the Nautilus IDE frontend.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the dev server
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Interactive API docs
open http://localhost:8000/docs
```

---

## API Reference

### Code Execution

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/run` | Execute Python code (sandboxed); returns stdout, stderr, exit_code, duration_ms |
| `WS`   | `/ws/run` | Stream stdout/stderr in real time as the code runs |

**POST /api/run — request**
```json
{ "code": "print('hello')", "timeout": 10.0 }
```
**POST /api/run — response**
```json
{
  "success": true,
  "stdout": "hello\n",
  "stderr": "",
  "exit_code": 0,
  "duration_ms": 3.2
}
```

**WS /ws/run — client sends**
```json
{ "code": "for i in range(5): print(i)", "timeout": 10 }
```
**WS /ws/run — server streams**
```json
{ "type": "stdout", "data": "0\n" }
{ "type": "stdout", "data": "1\n" }
...
{ "type": "done",   "data": 0 }
```

---

### Static Analysis

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/analyze` | Returns errors, warnings, imports, line count |
| `POST` | `/api/format`  | PEP-8 format code (uses autopep8 if installed) |

**POST /api/analyze — response**
```json
{
  "errors":   [{ "line": 12, "col": 0, "msg": "SyntaxError: invalid syntax" }],
  "warnings": [{ "line": 7,  "msg": "Line too long (94 chars)" }],
  "imports":  [{ "name": "math", "line": 2, "type": "stdlib" }],
  "line_count": 42,
  "ast_ok": true
}
```

---

### File Management

| Method   | Path               | Description |
|----------|--------------------|-------------|
| `GET`    | `/api/files`       | List all files + active_id |
| `POST`   | `/api/files`       | Create new file |
| `GET`    | `/api/files/{id}`  | Get file by ID |
| `PUT`    | `/api/files/{id}`  | Update name / code / modified flag |
| `DELETE` | `/api/files/{id}`  | Delete file (min 1 file enforced) |

---

### Execution History

| Method | Path          | Description |
|--------|---------------|-------------|
| `GET`  | `/api/history`| Last 200 run records (id, ts, status, duration_ms) |

---

### Submarine Control

| Method | Path                        | Description |
|--------|-----------------------------|-------------|
| `GET`  | `/api/telemetry`            | Live telemetry snapshot |
| `GET`  | `/api/submarine/status`     | Full submarine state |
| `POST` | `/api/submarine/dive`       | `{ "depth_m": 300 }` |
| `POST` | `/api/submarine/course`     | `{ "heading_deg": 45, "speed_kts": 12 }` |
| `POST` | `/api/submarine/surface`    | Emergency surface protocol |
| `WS`   | `/ws/telemetry`             | Push telemetry every second |

---

## Connecting the Frontend

Update the frontend's `runCode()` function to call the backend instead of simulating output:

```js
async function runCode() {
  const code = editorEl.value;
  setMode('RUNNING', 'a');
  const res = await fetch('http://localhost:8000/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, timeout: 10 })
  });
  const data = await res.json();
  if (data.stdout) data.stdout.split('\n').forEach(l => l && log('  › ' + l, 'o-ok'));
  if (data.stderr) data.stderr.split('\n').forEach(l => l && log('  ✗ ' + l, 'o-err'));
  log(data.success ? '[SYS] ✓ exit 0' : '[SYS] ✗ exit 1', data.success ? 'o-ok' : 'o-err');
  setMode(data.success ? 'READY' : 'ERROR', data.success ? 'g' : 'r');
}
```

For streaming execution via WebSocket:

```js
function runCodeStreaming() {
  const ws = new WebSocket('ws://localhost:8000/ws/run');
  ws.onopen = () => ws.send(JSON.stringify({ code: editorEl.value }));
  ws.onmessage = ({ data }) => {
    const { type, data: text } = JSON.parse(data);
    if (type === 'stdout') log('  › ' + text.trimEnd(), 'o-ok');
    if (type === 'stderr') log('  ✗ ' + text.trimEnd(), 'o-err');
    if (type === 'done')   { ws.close(); setMode('READY', 'g'); }
  };
}
```

---

## Security Notes

The sandbox blocks: `socket`, `ctypes`, `mmap`, `multiprocessing`, `__import__()`.
`open()`, `exec()`, `eval()`, `compile()`, and `input()` are also removed from builtins.
For production, consider running user code in a Docker container with resource limits.
