# MCP 密钥管理（前端查看/重置 + 动态生效）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 WebUI 左侧导航新增「密钥」页，查看与重置 MCP 中转站 `/mcp` 的单一受管 API Key；重置后旧 key 立即失效、新 key 立即生效，无需重建容器。

**Architecture:** key 仍存 `data/.env` 的 `MCP_API_KEYS`（单一真相源）。鉴权由「进程启动冻结 key」改为「每请求经 TTL 缓存的 provider 实时读 `data/.env` 文件」——因容器内 `os.environ`/Config 单例启动后固定、写文件不更新它，唯有读文件才能即时生效。后端加 `MCPKeyService`（读/生成/重置 + chmod 600 兜底）+ 两个登录态保护端点；前端加密钥页 + 侧栏入口。

**Tech Stack:** Python 3 / FastAPI / Starlette ASGI / 官方 mcp SDK / React + TypeScript + Vite / axios / unittest+pytest / vitest

**关联 spec:** `docs/superpowers/specs/2026-06-03-mcp-key-management-design.md`

---

## 仓库约定（AGENTS.md，务必遵守）

- commit message **用英文**，**不加 `Co-Authored-By`**；前缀 `feat:`/`test:`/`docs:`/`chore:`。
- 未经明确确认不 `git push`/`git tag`；本计划只做本地 commit（部署 Task 9 已获用户授权）。
- 后端依赖装进项目 `.venv`；后端验证 `./scripts/ci_gate.sh`，最低 `python -m py_compile <changed>`，测试 `python -m pytest -m "not network"`。
- 前端改动跑 `cd apps/dsa-web && npm run lint && npm run build`。
- 涉及 API 行为变化更 `docs/CHANGELOG.md`（`[Unreleased]` 扁平 `- [类型] 描述`，**禁止** `### 标题`）；新配置项同步 `.env.example`（本计划不新增 env 配置项）。
- 不写死密钥/域名/端口。

---

## 文件结构（决定任务拆分）

**后端 新建**
- `src/services/mcp_key_service.py` — `MCPKeyService`：读当前 key / 生成 / 重置写回 + chmod 600。
- `api/v1/endpoints/mcp_keys.py` — `GET /api/v1/mcp-keys` + `POST /api/v1/mcp-keys/reset`。
- `tests/test_mcp_key_service.py`、`tests/test_mcp_keys_endpoint.py`、`tests/test_mcp_auth_dynamic.py`。

**后端 修改**
- `api/mcp/auth.py` — 加 `first_api_key()`、`load_mcp_api_keys_fresh()`、`_CachedKeyProvider`、`get_key_provider()`；`MCPAuthMiddleware` 改持 `key_provider` 回调。
- `api/mcp/server.py` — `build_mcp_asgi_app()` 传 `get_key_provider()`。
- `api/v1/router.py` — 注册 `mcp_keys.router`。
- `tests/test_mcp_auth.py` — 适配 `MCPAuthMiddleware` 新签名（set→lambda）。
- `tests/test_mcp_app_mount.py` — E2E 改用 `ENV_FILE` 临时文件喂 key（provider 读文件）。

**前端 新建**
- `apps/dsa-web/src/api/mcpKeys.ts` — `getMcpKey()` / `resetMcpKey()`。
- `apps/dsa-web/src/pages/MCPKeyPage.tsx` — 密钥页。
- `apps/dsa-web/src/pages/__tests__/MCPKeyPage.test.tsx`。

**前端 修改**
- `apps/dsa-web/src/components/layout/SidebarNav.tsx` — 加「密钥」项。
- `apps/dsa-web/src/components/layout/__tests__/SidebarNav.test.tsx` — 适配。
- `apps/dsa-web/src/App.tsx` — 注册 `/mcp-keys` 路由。
- `apps/dsa-web/src/components/layout/ShellHeader.tsx` — 加 `/mcp-keys` 标题映射。

**文档**
- `docs/mcp-gateway.md`、`docs/mcp-integration-guide.md`、`docs/CHANGELOG.md`。

---

## Task 1: MCPKeyService（读/生成/重置 + chmod 600）

**Files:**
- Create: `src/services/mcp_key_service.py`
- Modify: `api/mcp/auth.py`（加 `first_api_key`）
- Test: `tests/test_mcp_key_service.py`

- [ ] **Step 1: 在 auth.py 加 first_api_key（保序取首个 key）**

`parse_api_keys` 返回 set（无序），单 key 模型需保序取首个。在 `api/mcp/auth.py` 的 `parse_api_keys` 之后新增：

```python
def first_api_key(raw: str) -> "str | None":
    """Return the first key (order-preserving) from a 'k1:l1,k2:l2' string, or None."""
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        key = item.split(":", 1)[0].strip()
        if key:
            return key
    return None
```

- [ ] **Step 2: 写失败测试**

创建 `tests/test_mcp_key_service.py`：

```python
# -*- coding: utf-8 -*-
"""MCPKeyService：读当前 key / 重置生成新 key 覆盖写回 / 文件权限 600。"""
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.core.config_manager import ConfigManager
from src.services.mcp_key_service import MCPKeyService


class TestMCPKeyService(unittest.TestCase):
    def _service(self, initial: str | None):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env_path = Path(self.tmp.name) / ".env"
        body = f"MCP_API_KEYS={initial}\n" if initial is not None else "OTHER=1\n"
        env_path.write_text(body, encoding="utf-8")
        os.chmod(env_path, 0o600)
        return MCPKeyService(ConfigManager(env_path=env_path)), env_path

    def test_get_current_key_returns_first(self):
        svc, _ = self._service("abc123:default")
        self.assertEqual(svc.get_current_key(), "abc123")

    def test_get_current_key_none_when_absent(self):
        svc, _ = self._service(None)
        self.assertIsNone(svc.get_current_key())

    def test_reset_generates_and_overwrites(self):
        svc, env_path = self._service("oldkey:default")
        new_key = svc.reset_key()
        self.assertNotEqual(new_key, "oldkey")
        self.assertGreaterEqual(len(new_key), 32)
        # 落盘且整体覆盖（旧 key 不再存在）
        self.assertEqual(svc.get_current_key(), new_key)
        self.assertNotIn("oldkey", env_path.read_text(encoding="utf-8"))

    def test_reset_keeps_permission_600(self):
        svc, env_path = self._service("oldkey")
        svc.reset_key()
        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_reset_first_time_when_no_key(self):
        svc, _ = self._service(None)
        new_key = svc.reset_key()
        self.assertEqual(svc.get_current_key(), new_key)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_key_service.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.services.mcp_key_service'`。

- [ ] **Step 4: 实现 MCPKeyService**

创建 `src/services/mcp_key_service.py`：

```python
# -*- coding: utf-8 -*-
"""MCP 中转站受管 API Key 的读取与重置。

单一受管 key，存于 data/.env 的 MCP_API_KEYS。重置整体覆盖（旧 key 一并失效），
写后显式 chmod 600（_atomic_upsert 的 os.replace 不保留权限）。
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from src.core.config_manager import ConfigManager
from api.mcp.auth import first_api_key

logger = logging.getLogger(__name__)

_KEY_ENV = "MCP_API_KEYS"
_MASK = "******"


class MCPKeyService:
    def __init__(self, manager: Optional[ConfigManager] = None):
        self._manager = manager or ConfigManager()

    def get_current_key(self) -> Optional[str]:
        raw = self._manager.read_config_map().get(_KEY_ENV, "")
        return first_api_key(raw)

    def reset_key(self) -> str:
        """生成新 key，整体覆盖 MCP_API_KEYS，保持文件 600，返回新 key。"""
        new_key = secrets.token_hex(24)
        self._manager.apply_updates(
            [(_KEY_ENV, new_key)],
            sensitive_keys={_KEY_ENV},
            mask_token=_MASK,
        )
        try:
            os.chmod(self._manager.env_path, 0o600)
        except OSError as exc:
            logger.warning("chmod 600 on %s failed: %s", self._manager.env_path, exc)
        return new_key
```

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_key_service.py -v`
Expected: PASS（5 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/services/mcp_key_service.py api/mcp/auth.py tests/test_mcp_key_service.py
git commit -m "feat: add MCPKeyService to read and reset the managed MCP API key"
```

---

## Task 2: 动态鉴权（key_provider + TTL 缓存 + 失效钩子）

**Files:**
- Modify: `api/mcp/auth.py`
- Test: `tests/test_mcp_auth_dynamic.py`、`tests/test_mcp_auth.py`（适配旧测试）

- [ ] **Step 1: 写失败测试（动态行为）**

创建 `tests/test_mcp_auth_dynamic.py`：

```python
# -*- coding: utf-8 -*-
"""动态鉴权：provider 切换后免重启即时生效；TTL 缓存与失效。"""
import asyncio
import time
import unittest

from api.mcp.auth import MCPAuthMiddleware, _CachedKeyProvider


class _Spy:
    def __init__(self):
        self.called = False
    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _status(mw, token):
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    scope = {"type": "http", "headers": headers}
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(msg):
        sent.append(msg)
    asyncio.run(mw(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


class TestDynamicAuth(unittest.TestCase):
    def test_provider_switch_takes_effect(self):
        current = {"keys": {"old"}}
        mw = MCPAuthMiddleware(_Spy(), lambda: current["keys"])
        self.assertEqual(_status(mw, "old"), 200)
        current["keys"] = {"new"}           # 模拟重置
        self.assertEqual(_status(mw, "old"), 401)   # 旧 key 立即失效
        self.assertEqual(_status(mw, "new"), 200)   # 新 key 立即生效

    def test_empty_provider_denies(self):
        mw = MCPAuthMiddleware(_Spy(), lambda: set())
        self.assertEqual(_status(mw, "anything"), 401)


class TestCachedProvider(unittest.TestCase):
    def test_ttl_caches_then_refreshes(self):
        calls = {"n": 0}
        def loader():
            calls["n"] += 1
            return {f"k{calls['n']}"}
        p = _CachedKeyProvider(loader, ttl=10.0)
        self.assertEqual(p(), {"k1"})
        self.assertEqual(p(), {"k1"})        # 命中缓存，未再 load
        self.assertEqual(calls["n"], 1)
        p.invalidate()
        self.assertEqual(p(), {"k2"})        # 失效后重新 load
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_auth_dynamic.py -v`
Expected: FAIL，`ImportError: cannot import name '_CachedKeyProvider'` 或 `MCPAuthMiddleware` 签名不符。

- [ ] **Step 3: 改 auth.py（provider 化 + 缓存 + fresh loader）**

把 `api/mcp/auth.py` 顶部 import 补 `import time` 与 `from typing import Callable, Optional, Set`。

将 `MCPAuthMiddleware` 改为持 provider（替换原 `__init__`/`__call__`）：

```python
class MCPAuthMiddleware:
    """包裹 MCP ASGI 子应用，做 Bearer key 校验。

    key 由 key_provider() 每请求实时提供（而非构造时冻结），以支持重置后免重启生效。
    """

    def __init__(self, app, key_provider: "Callable[[], Set[str]]"):
        self.app = app
        self.key_provider = key_provider

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        keys = self.key_provider() or set()
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode("latin-1")
        token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        if not keys or token not in keys:
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    async def _reject(self, send):
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
```

在文件末尾（`MCPAuthMiddleware` 之后）新增 fresh loader、缓存 provider 与单例：

```python
def load_mcp_api_keys_fresh(env_path=None) -> Set[str]:
    """实时从 data/.env 文件读取 MCP_API_KEYS（不走 Config 单例/os.environ，
    以便重置写文件后即时反映）。"""
    from src.core.config_manager import ConfigManager
    manager = ConfigManager(env_path=env_path) if env_path else ConfigManager()
    raw = manager.read_config_map().get("MCP_API_KEYS", "")
    return parse_api_keys(raw)


class _CachedKeyProvider:
    """带 TTL 的 key provider：每 TTL 秒最多读一次源，避免每请求读盘。"""

    def __init__(self, loader: "Callable[[], Set[str]]", ttl: float = 3.0):
        self._loader = loader
        self._ttl = ttl
        self._cached: Set[str] = set()
        self._ts = 0.0

    def __call__(self) -> Set[str]:
        now = time.monotonic()
        if self._ts == 0.0 or (now - self._ts) >= self._ttl:
            self._cached = self._loader()
            self._ts = now
        return self._cached

    def invalidate(self) -> None:
        self._ts = 0.0


_default_key_provider: "Optional[_CachedKeyProvider]" = None


def get_key_provider() -> _CachedKeyProvider:
    """进程级单例 provider（鉴权中间件与重置端点共用，便于重置后失效缓存）。"""
    global _default_key_provider
    if _default_key_provider is None:
        _default_key_provider = _CachedKeyProvider(load_mcp_api_keys_fresh)
    return _default_key_provider
```

> 保留现有 `parse_api_keys`、`load_mcp_api_keys`（其它处可能引用），不删除。

- [ ] **Step 4: 适配旧测试 tests/test_mcp_auth.py**

旧测试用 `MCPAuthMiddleware(spy, {"secret1"})`（冻结 set），改为 lambda provider。修改 `tests/test_mcp_auth.py` 中四处构造：

```python
# 将每处  MCPAuthMiddleware(spy, {"secret1"})  改为：
mw = MCPAuthMiddleware(spy, lambda: {"secret1"})
# 将       MCPAuthMiddleware(spy, set())         改为：
mw = MCPAuthMiddleware(spy, lambda: set())
```

（`TestParseKeys`/`TestRateLimitStub` 不变。）

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_auth_dynamic.py tests/test_mcp_auth.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add api/mcp/auth.py tests/test_mcp_auth_dynamic.py tests/test_mcp_auth.py
git commit -m "feat: make MCP auth read keys dynamically via cached provider"
```

---

## Task 3: build_mcp_asgi_app 接 provider + 修 E2E 测试

**Files:**
- Modify: `api/mcp/server.py`
- Test: `tests/test_mcp_app_mount.py`

- [ ] **Step 1: 改 build_mcp_asgi_app 用 provider**

把 `api/mcp/server.py` 的 `build_mcp_asgi_app`（当前 `MCPAuthMiddleware(handle_streamable_http, load_mcp_api_keys())`）改为：

```python
def build_mcp_asgi_app():
    """返回带 API Key 鉴权的 ASGI 子应用，供 app.mount('/mcp', ...)。"""
    from api.mcp.auth import MCPAuthMiddleware, get_key_provider

    async def handle_streamable_http(scope, receive, send):
        await get_mcp_session_manager().handle_request(scope, receive, send)

    return MCPAuthMiddleware(handle_streamable_http, get_key_provider())
```

- [ ] **Step 2: 改 E2E 测试改用 ENV_FILE 临时文件喂 key**

现有 `tests/test_mcp_app_mount.py::TestMcpInitializeE2E` 用 `os.environ["MCP_API_KEYS"]`，但新 provider 读 **data/.env 文件**。改为写临时 `.env` 并指向它。替换 `test_initialize_through_full_chain` 的环境准备：把

```python
        with patch.dict(os.environ, {"MCP_API_KEYS": "testkey"}):
            Config.reset_instance()
            try:
                r, r_slash, r401 = asyncio.run(_drive())
            finally:
                Config.reset_instance()
```

改为：

```python
        import tempfile
        from api.mcp.auth import get_key_provider
        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("MCP_API_KEYS=testkey\n")
            with patch.dict(os.environ, {"ENV_FILE": env_path}):
                Config.reset_instance()
                get_key_provider().invalidate()   # 清掉可能的旧缓存
                try:
                    r, r_slash, r401 = asyncio.run(_drive())
                finally:
                    Config.reset_instance()
                    get_key_provider().invalidate()
```

（`_drive()` 与断言不变；provider 经 `ConfigManager()` 解析 `ENV_FILE`→临时文件读到 `testkey`。）

- [ ] **Step 3: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_app_mount.py tests/test_mcp_server.py -v`
Expected: 全部 PASS（E2E 仍 `/mcp`→200、`/mcp/`→200、无 key→401）。

- [ ] **Step 4: py_compile + Commit**

```bash
source .venv/bin/activate && python -m py_compile api/mcp/server.py api/mcp/auth.py
git add api/mcp/server.py tests/test_mcp_app_mount.py
git commit -m "feat: wire MCP gateway auth to dynamic key provider"
```

---

## Task 4: 端点 mcp_keys.py + 路由注册

**Files:**
- Create: `api/v1/endpoints/mcp_keys.py`
- Modify: `api/v1/router.py`
- Test: `tests/test_mcp_keys_endpoint.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_mcp_keys_endpoint.py`：

```python
# -*- coding: utf-8 -*-
"""mcp-keys 端点：GET 返回当前 key/端点；POST reset 生成新 key 并失效鉴权缓存。"""
import unittest
from unittest.mock import patch, MagicMock

import api.v1.endpoints.mcp_keys as ep


class TestMcpKeysEndpoint(unittest.TestCase):
    def _request(self, host="a-stock.tech-monthly.online", proto="https"):
        req = MagicMock()
        req.headers = {"host": host, "x-forwarded-proto": proto}
        req.url.scheme = "http"
        return req

    def test_get_returns_key_and_endpoint(self):
        svc = MagicMock(); svc.get_current_key.return_value = "abc123"
        with patch.object(ep, "MCPKeyService", return_value=svc):
            out = ep.get_mcp_key(self._request())
        self.assertEqual(out["key"], "abc123")
        self.assertTrue(out["configured"])
        self.assertEqual(out["endpoint"], "https://a-stock.tech-monthly.online/mcp")

    def test_get_unconfigured(self):
        svc = MagicMock(); svc.get_current_key.return_value = None
        with patch.object(ep, "MCPKeyService", return_value=svc):
            out = ep.get_mcp_key(self._request())
        self.assertIsNone(out["key"])
        self.assertFalse(out["configured"])

    def test_reset_generates_and_invalidates_cache(self):
        svc = MagicMock(); svc.reset_key.return_value = "newkey"
        provider = MagicMock()
        with patch.object(ep, "MCPKeyService", return_value=svc), \
             patch("api.mcp.auth.get_key_provider", return_value=provider):
            out = ep.reset_mcp_key(self._request())
        self.assertEqual(out["key"], "newkey")
        self.assertTrue(out["configured"])
        svc.reset_key.assert_called_once()
        provider.invalidate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_keys_endpoint.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'api.v1.endpoints.mcp_keys'`。

- [ ] **Step 3: 实现端点**

创建 `api/v1/endpoints/mcp_keys.py`：

```python
# -*- coding: utf-8 -*-
"""MCP 受管 API Key 端点（登录态保护，挂 /api/v1/mcp-keys）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from src.services.mcp_key_service import MCPKeyService

router = APIRouter()


def _endpoint(request: Request) -> str:
    host = request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}/mcp"


@router.get("")
def get_mcp_key(request: Request) -> dict:
    key = MCPKeyService().get_current_key()
    return {"key": key, "endpoint": _endpoint(request), "configured": key is not None}


@router.post("/reset")
def reset_mcp_key(request: Request) -> dict:
    new_key = MCPKeyService().reset_key()
    from api.mcp.auth import get_key_provider
    get_key_provider().invalidate()
    return {"key": new_key, "endpoint": _endpoint(request), "configured": True}
```

- [ ] **Step 4: 注册路由**

在 `api/v1/router.py` 顶部 import 区加入 `mcp_keys`（与现有 `from api.v1.endpoints import ...` 同处；若是逐个 import 则照现有风格加一行 `from api.v1.endpoints import mcp_keys`），并在末尾 `router.include_router(...)` 区追加：

```python
router.include_router(
    mcp_keys.router,
    prefix="/mcp-keys",
    tags=["MCP Keys"],
)
```

> 先 `grep -nE "from api.v1.endpoints import|include_router" api/v1/router.py` 确认 import 风格，照搬。

- [ ] **Step 5: 运行测试确认通过 + 路由挂载冒烟**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_mcp_keys_endpoint.py -v
python -c "from api.app import create_app; app=create_app(); assert any(getattr(r,'path','')=='/api/v1/mcp-keys' for r in app.routes), [getattr(r,'path','') for r in app.routes if 'mcp' in getattr(r,'path','')]; print('route ok')"
```
Expected: 测试 PASS；打印 `route ok`。

- [ ] **Step 6: Commit**

```bash
git add api/v1/endpoints/mcp_keys.py api/v1/router.py tests/test_mcp_keys_endpoint.py
git commit -m "feat: add admin-protected MCP key view/reset endpoints"
```

---

## Task 5: 前端 api/mcpKeys.ts

**Files:**
- Create: `apps/dsa-web/src/api/mcpKeys.ts`

- [ ] **Step 1: 实现 api 封装**

创建 `apps/dsa-web/src/api/mcpKeys.ts`（照 `api/screen.ts` 风格，用 `apiClient` + `toCamelCase`）：

```typescript
import apiClient from './index';
import { toCamelCase } from './utils';

export type McpKeyInfo = {
  key: string | null;
  endpoint: string;
  configured: boolean;
};

export async function getMcpKey(): Promise<McpKeyInfo> {
  const { data } = await apiClient.get('/api/v1/mcp-keys');
  return toCamelCase<McpKeyInfo>(data);
}

export async function resetMcpKey(): Promise<McpKeyInfo> {
  const { data } = await apiClient.post('/api/v1/mcp-keys/reset');
  return toCamelCase<McpKeyInfo>(data);
}
```

> 先 `sed -n '1,15p' apps/dsa-web/src/api/index.ts` 确认 `apiClient` 默认导出与 `baseURL`；若 `baseURL` 已含 `/api`，则上面路径相应去掉前缀 `/api` 改 `/v1/mcp-keys`（以现有 `api/screen.ts`/`api/stocks.ts` 的实际调用前缀为准，照搬同款写法）。

- [ ] **Step 2: 类型检查**

Run: `cd apps/dsa-web && npx tsc --noEmit 2>&1 | head` （应无新错误）

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/api/mcpKeys.ts
git commit -m "feat: add web api client for MCP key view/reset"
```

---

## Task 6: 前端密钥页 + 侧栏 + 路由 + 标题

**Files:**
- Create: `apps/dsa-web/src/pages/MCPKeyPage.tsx`、`apps/dsa-web/src/pages/__tests__/MCPKeyPage.test.tsx`
- Modify: `SidebarNav.tsx`、`SidebarNav.test.tsx`、`App.tsx`、`ShellHeader.tsx`

- [ ] **Step 1: 写页面失败测试**

创建 `apps/dsa-web/src/pages/__tests__/MCPKeyPage.test.tsx`（照 `pages/__tests__` 现有范式：vitest + @testing-library/react，mock api 模块）：

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import MCPKeyPage from '../MCPKeyPage';
import * as api from '../../api/mcpKeys';

vi.mock('../../api/mcpKeys');

describe('MCPKeyPage', () => {
  beforeEach(() => {
    vi.mocked(api.getMcpKey).mockResolvedValue({
      key: '0bc83118abcdef', endpoint: 'https://x/mcp', configured: true,
    });
    vi.mocked(api.resetMcpKey).mockResolvedValue({
      key: 'newresetkey123', endpoint: 'https://x/mcp', configured: true,
    });
  });

  it('masks key by default and reveals on toggle', async () => {
    render(<MCPKeyPage />);
    await waitFor(() => expect(api.getMcpKey).toHaveBeenCalled());
    // 默认脱敏：不直接出现完整 key
    expect(screen.queryByText('0bc83118abcdef')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /显示|reveal/i }));
    await waitFor(() => expect(screen.getByText('0bc83118abcdef')).toBeInTheDocument());
  });

  it('resets key after confirm', async () => {
    render(<MCPKeyPage />);
    await waitFor(() => expect(api.getMcpKey).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: /重置|reset/i }));
    // 二次确认
    fireEvent.click(screen.getByRole('button', { name: /确认|confirm/i }));
    await waitFor(() => expect(api.resetMcpKey).toHaveBeenCalled());
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/pages/__tests__/MCPKeyPage.test.tsx 2>&1 | tail -15`
Expected: FAIL（找不到 `../MCPKeyPage`）。

- [ ] **Step 3: 实现 MCPKeyPage.tsx**

创建 `apps/dsa-web/src/pages/MCPKeyPage.tsx`。复用项目现有 UI 组件（先 `ls apps/dsa-web/src/components/ui` 看可用按钮/卡片；若无 UI 库则用 settings 卡片同款 className）。核心结构：

```tsx
import { useEffect, useState } from 'react';
import { getMcpKey, resetMcpKey, type McpKeyInfo } from '../api/mcpKeys';

function mask(key: string): string {
  if (key.length <= 8) return '••••••';
  return `${key.slice(0, 4)}${'•'.repeat(12)}${key.slice(-4)}`;
}

export default function MCPKeyPage() {
  const [info, setInfo] = useState<McpKeyInfo | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => { getMcpKey().then(setInfo).catch(() => setInfo(null)); }, []);

  const copy = (text: string) => navigator.clipboard?.writeText(text);

  const doReset = async () => {
    setBusy(true);
    try {
      const next = await resetMcpKey();
      setInfo(next);
      setRevealed(true);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const key = info?.key ?? null;
  const endpoint = info?.endpoint ?? '';
  const snippet = key
    ? `{\n  "mcpServers": {\n    "a-stock": {\n      "type": "streamable-http",\n      "url": "${endpoint}",\n      "headers": { "Authorization": "Bearer ${key}" }\n    }\n  }\n}`
    : '';

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <h1 className="text-xl font-semibold">MCP 密钥</h1>

      <section className="space-y-2">
        <div className="text-sm text-muted-foreground">端点地址</div>
        <div className="flex items-center gap-2">
          <code className="px-2 py-1 rounded bg-muted">{endpoint}</code>
          <button onClick={() => copy(endpoint)}>复制</button>
        </div>
      </section>

      <section className="space-y-2">
        <div className="text-sm text-muted-foreground">当前生效 Key</div>
        {key ? (
          <div className="flex items-center gap-2">
            <code className="px-2 py-1 rounded bg-muted">{revealed ? key : mask(key)}</code>
            <button onClick={() => setRevealed((v) => !v)}>{revealed ? '隐藏' : '显示'}</button>
            <button onClick={() => copy(key)}>复制</button>
          </div>
        ) : (
          <div className="text-sm">尚未生成</div>
        )}
      </section>

      {key && (
        <section className="space-y-2">
          <div className="text-sm text-muted-foreground">客户端连接配置</div>
          <pre className="p-3 rounded bg-muted overflow-auto text-xs">{snippet}</pre>
          <button onClick={() => copy(snippet)}>复制配置</button>
        </section>
      )}

      <section>
        {!confirming ? (
          <button onClick={() => setConfirming(true)} disabled={busy}>
            {key ? '重置 Key' : '生成 Key'}
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-red-600">
              重置后旧 Key 立即失效，正在使用的智能体须更新为新 Key。确认重置？
            </p>
            <div className="flex gap-2">
              <button onClick={doReset} disabled={busy}>确认重置</button>
              <button onClick={() => setConfirming(false)} disabled={busy}>取消</button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
```

> 若项目有统一 `Button`/`Card` 组件（`components/ui`），用它们替换原生 `<button>`，保持视觉一致；测试里的 `getByRole('button', {name})` 依赖按钮可访问名（中文文案已满足）。

- [ ] **Step 4: 注册侧栏项**

在 `apps/dsa-web/src/components/layout/SidebarNav.tsx`：import 区把 `lucide-react` 增加 `KeyRound`，`navItems` 在 `settings` 之前插入：

```tsx
  { key: 'mcp-keys', label: '密钥', to: '/mcp-keys', icon: KeyRound },
```

- [ ] **Step 5: 注册路由与标题**

`apps/dsa-web/src/App.tsx`：在 `<Route path="/settings" .../>` 同处加：

```tsx
        <Route path="/mcp-keys" element={<MCPKeyPage />} />
```

并在文件顶部 import：`import MCPKeyPage from './pages/MCPKeyPage';`（照现有页面 import 风格）。

`apps/dsa-web/src/components/layout/ShellHeader.tsx`：在路由标题映射对象中加：

```tsx
  '/mcp-keys': { title: '密钥', description: 'MCP 中转站 API Key 查看与重置' },
```

- [ ] **Step 6: 适配 SidebarNav.test.tsx**

`SidebarNav.test.tsx` 断言了前若干项 href（`hrefs.slice(0, 4)` === `['/', '/chat', '/screening', '/portfolio']`）。新增「密钥」插在 `settings` 前、位置在前 4 之后，不影响该断言。若该测试另有「项数」或「包含 settings」断言，按实际补一条 `/mcp-keys` 存在性断言：

```tsx
    expect(hrefs).toContain('/mcp-keys');
```

> 先 `sed -n '1,60p' apps/dsa-web/src/components/layout/__tests__/SidebarNav.test.tsx` 看断言细节，仅在被新增项破坏处最小改动。

- [ ] **Step 7: 运行前端测试 + lint + build**

Run:
```bash
cd apps/dsa-web && npx vitest run src/pages/__tests__/MCPKeyPage.test.tsx src/components/layout/__tests__/SidebarNav.test.tsx 2>&1 | tail -15
npm run lint && npm run build
```
Expected: 测试 PASS；lint/build 通过。

- [ ] **Step 8: Commit**

```bash
git add apps/dsa-web/src/pages/MCPKeyPage.tsx apps/dsa-web/src/pages/__tests__/MCPKeyPage.test.tsx \
  apps/dsa-web/src/components/layout/SidebarNav.tsx apps/dsa-web/src/components/layout/__tests__/SidebarNav.test.tsx \
  apps/dsa-web/src/App.tsx apps/dsa-web/src/components/layout/ShellHeader.tsx
git commit -m "feat: add MCP key management page with sidebar entry"
```

---

## Task 7: 文档 + 全量回归

**Files:**
- Modify: `docs/mcp-gateway.md`、`docs/mcp-integration-guide.md`、`docs/CHANGELOG.md`

- [ ] **Step 1: 更新 mcp-gateway.md**

在「配置（data/.env）」小节后补一句：key 现可在 **WebUI 左侧「密钥」页**查看与重置，**重置即时生效（无需重建容器）**；CLI/手动改 `data/.env` 仍有效，两者读同一 `MCP_API_KEYS`。

- [ ] **Step 2: 更新 mcp-integration-guide.md**

在「一分钟接入」附近加一句：获取/重置 key 可登录 WebUI →左侧「密钥」页一键复制连接配置。

- [ ] **Step 3: 更新 CHANGELOG（[Unreleased] 扁平）**

```
- [新功能] WebUI 新增「密钥」页：查看/重置 MCP 中转站 API Key，重置后旧 key 立即失效、新 key 即时生效（无需重建容器）
- [改进] MCP 鉴权改为动态读取 data/.env 的 MCP_API_KEYS（带短 TTL 缓存），支持运行时改 key 免重启
```

- [ ] **Step 4: 全量后端回归**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_mcp_key_service.py tests/test_mcp_auth_dynamic.py tests/test_mcp_auth.py tests/test_mcp_keys_endpoint.py tests/test_mcp_app_mount.py tests/test_mcp_server.py -v
./scripts/ci_gate.sh
```
Expected: 本特性测试全绿；ci_gate 全绿（守住 2771+）。

- [ ] **Step 5: 铁律烟雾自查（未触动数据源路由）**

Run:
```bash
source .venv/bin/activate && python -c "
from data_provider.base import DataFetcherManager
import inspect
assert 'tickflow' in inspect.getsource(DataFetcherManager.get_realtime_quote).lower()
print('数据源铁律未触动')
"
```
Expected: 打印确认。

- [ ] **Step 6: 前端全量**

Run: `cd apps/dsa-web && npm run lint && npm run build`
Expected: 通过。

- [ ] **Step 7: Commit**

```bash
git add docs/mcp-gateway.md docs/mcp-integration-guide.md docs/CHANGELOG.md
git commit -m "docs: document MCP key management page and dynamic auth"
```

---

## Task 8: 真实服务器烟雾（本地 uvicorn 验证动态生效）

**Files:** 无（验证）

- [ ] **Step 1: 本地起 uvicorn 验证重置即时生效**

写一个临时 `data/.env` 测试不便，改为直接验证「动态 provider 读文件 + 重置失效缓存」链路（Task 2/3 已单测覆盖即时生效）。此处做一次真实 HTTP 烟雾：

```bash
cd "/Volumes/Mac硬盘/project/股票分析部署/daily_stock_analysis" && source .venv/bin/activate
# 用临时 env 文件
TMPENV=$(mktemp); echo "MCP_API_KEYS=key-aaa" > "$TMPENV"
ENV_FILE="$TMPENV" WEBUI_ENABLED=false ADMIN_AUTH_ENABLED=false \
  python -m uvicorn server:app --host 127.0.0.1 --port 8761 --log-level warning &
UVI=$!; sleep 6
echo "--- 旧 key-aaa 应 200 ---"
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8761/mcp \
  -H "Authorization: Bearer key-aaa" -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
echo "--- 改文件为 key-bbb（模拟重置），等 TTL 过期 ---"
echo "MCP_API_KEYS=key-bbb" > "$TMPENV"; sleep 4
echo "旧 key-aaa 现应 401:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8761/mcp -H "Authorization: Bearer key-aaa" -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
echo "新 key-bbb 现应 200:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8761/mcp -H "Authorization: Bearer key-bbb" -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
kill $UVI 2>/dev/null; rm -f "$TMPENV"
```
Expected: 旧 key 先 200 → 改文件 + TTL 过期后旧 key 401、新 key 200（证明免重启动态生效）。

> 测试产物（临时 env、后台进程）跑完即清理（已含 kill/rm）。

---

## Task 9: 部署上线（用户已授权）

> 用户已授权「测试通过后部署上线」，方式：合并 main + 推送 origin → 服务器 pull + 重建。

- [ ] **Step 1: 合并并推送**

```bash
git checkout main && git merge --ff-only <feature-branch-or-current>
git push origin main
```
（若本计划直接在 main 上 commit，则跳过 merge，直接 `git push origin main`。）

- [ ] **Step 2: 服务器部署（前端构建随镜像）**

scp 一个部署脚本或直接 ssh 执行：备份 data/.env → `git pull --ff-only` → `docker compose -f docker/docker-compose.yml up -d --build`（**前端产物在镜像构建阶段生成**，故必须 `--build`）→ 等 server healthy。

- [ ] **Step 3: 生产验收**

```bash
# 公网：登录 WebUI →「密钥」页可查看当前 key、可重置；
# 命令行验证动态生效（用页面重置前后的 key 各打一次 /mcp initialize）：
# 旧 key → 401，新 key → 200（无需重建容器）。
```
确认：WebUI 密钥页正常；`/mcp` 旧 key 重置后即 401、新 key 200；两容器 healthy；数据源/分析功能不受影响。

- [ ] **Step 4: 更新维护记忆**

在自动记忆 `dsa-mcp-gateway` 追加：key 现支持 WebUI 查看/重置 + 动态生效；新增本地分叉文件（`mcp_key_service.py`、`mcp_keys.py`、前端密钥页等）须随同步上游保留；记录「`apply_updates` 不保权限、reset 显式 chmod 600」这一坑。

---

## 风险点与未验证项

- **多 worker**：若 server 以多 worker 运行，重置端点只失效自身 worker 缓存，其它 worker 靠 ≤3s TTL 自愈（生产 serve-only 通常单进程，影响可忽略）。
- **`apply_updates` 不保留 600**：本计划已用 reset 后显式 chmod 600 兜底；其它配置保存路径的同类问题不在本计划范围。
- **前端 baseURL 前缀**：Task 5 需按现有 `api/*.ts` 实际调用前缀对齐（`/api/v1` vs `/v1`），照搬同款写法。
- **真实 MCP 客户端**：动态生效已由本地 HTTP 烟雾（Task 8）与单测验证；真实 Claude/Cursor 重连体验建议上线后顺手确认。

---

## Self-Review

**1. Spec 覆盖：**
- 单一 key + 查看 + 重置 → Task 1/4/6 ✓
- 动态鉴权（每请求读文件 + TTL）→ Task 2/3 ✓
- chmod 600 兜底 → Task 1 ✓
- 端点登录态保护（挂 /api/v1）→ Task 4（沿用 AuthMiddleware）✓
- 前端页 + 侧栏 + 路由 + 标题 + 连接片段 → Task 5/6 ✓
- 端点地址由请求 Host/Proto 推导 → Task 4 `_endpoint` ✓
- 错误处理（未登录401、写失败保旧、旧key 401）→ Task 4 + Task 2 测试 ✓
- 测试（后端动态生效/前端组件/ci_gate/铁律）→ Task 2/6/7 ✓
- 部署 → Task 9 ✓

**2. Placeholder 扫描：** 各 code step 均给完整代码；前端 UI 组件以「复用现有 ui 或同款 className」明确，非占位。Task 5 baseURL 前缀给了「照现有 api/*.ts 实际前缀」的确定判据。

**3. 类型/命名一致：** `MCPKeyService.get_current_key/reset_key`、`first_api_key`、`load_mcp_api_keys_fresh`、`_CachedKeyProvider(loader,ttl)/__call__/invalidate`、`get_key_provider`、`MCPAuthMiddleware(app,key_provider)`、端点 `get_mcp_key/reset_mcp_key/_endpoint`、前端 `getMcpKey/resetMcpKey/McpKeyInfo` 跨任务一致。
