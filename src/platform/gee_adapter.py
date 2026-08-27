"""Google Earth Engine 适配器 (真实实现, 2026-08-24)。

GEE 的接入方式 (与 obsidian 13 号存档的查证一致):
  - 调官方 Python API `earthengine-api`, 不是模拟登录网站
  - 认证: Service Account (后端无人值守标准方式)
    credentials 预期: {service_account_email, key_json} 或 {key_path}

执行模型 (D=1 自由脚本的落地契约):
  生成的代码在受控命名空间里 exec, 注入:
    - ee: 已用 per-user 凭证 Initialize 的 earthengine 模块
    - REGION: {lon_min, lat_min, lon_max, lat_max}
    - TASK: 任务描述文本
  代码必须产出 (见 GENERATOR_SYSTEM_PROMPT 契约):
    - OUTPUT_JPEG: str  写出的 JPEG 文件路径
    - METRICS: dict     中间指标 (ndvi_mean/nir_mean/red_mean/valid_ratio,
                        供第四层锚点评测判断纠错)

安全:
  - 凭证只在本次 execute 内使用, 不落日志/不进 error/不存全局
  - exec 有 240s 超时 (ThreadPoolExecutor), GEE 计算慢但不许无限挂;
    max 挡高清大图 (分块取数+亿级像素 savefig) 放宽到 600s
  - 产物做 JPEG 魔数校验 (与 output.py 的防线一致, 假图在此拦截)
"""
from __future__ import annotations

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

from src.platform.base import ExecutionResult, RemoteSensingPlatform

_EXECUTE_TIMEOUT_S = 240
_EXECUTE_TIMEOUT_MAX_S = 600   # max 挡高清: 分块取数 + 亿级像素 savefig 需更久
_JPEG_MAGIC = b"\xff\xd8"


def _build_credentials(credentials: dict):
    """从 per-user 凭证 dict 构造 GEE ServiceAccountCredentials。

    支持三种形态:
      1. {service_account_email, key_json} (前端绑定标准形态)
      2. {service_account_email, key_path} (本地开发用文件路径)
      3. 原生 GEE Service Account JSON 直接粘贴 (含 client_email/private_key,
         桌面版向导路径 —— 用户从 Google 下载什么就贴什么)
    """
    import ee  # 延迟导入: 未装 earthengine-api 时本适配器仍可被注册

    if "client_email" in credentials and "private_key" in credentials \
            and "service_account_email" not in credentials:
        # 原生 SA JSON 形态: 整个 dict 即 key_json (Google 下载什么贴什么)
        return ee.ServiceAccountCredentials(
            credentials["client_email"], key_data=json.dumps(credentials))
    email = credentials.get("service_account_email")
    key_json = credentials.get("key_json")
    key_path = credentials.get("key_path")
    if not email:
        raise ValueError("凭证缺少 service_account_email (或原生 JSON 的 client_email)")
    if key_json:
        if isinstance(key_json, str):
            json.loads(key_json)  # 预校验, 脏 JSON 提前报人话错
        return ee.ServiceAccountCredentials(email, key_data=json.dumps(key_json) if not isinstance(key_json, str) else key_json)
    if key_path:
        return ee.ServiceAccountCredentials(email, key_file=key_path)
    raise ValueError("凭证缺少 key_json 或 key_path")


class GEEAdapter(RemoteSensingPlatform):
    """GEE 适配器 (真实实现)。"""

    name = "gee"

    def execute(self, code: str, credentials: dict, region: dict, **kwargs) -> ExecutionResult:
        """在本进程受控命名空间执行生成的遥感代码 (GEE 云端计算, 本地只收结果)。

        遵守铁决策 3: 云端算、只下结果——代码里 getInfo()/getDownloadURL()
        取回的只是指标与出图数据, 不下载原始影像。
        """
        try:
            creds = _build_credentials(credentials)
        except (ValueError, json.JSONDecodeError) as e:
            return ExecutionResult(success=False, error=f"GEE 凭证无效: {e}")

        import ee
        project = credentials.get("gee_project") or kwargs.get("gee_project")
        try:
            # 每次调用独立初始化 (per-user), 不污染全局; project 缺省用凭证文件里的
            if project:
                ee.Initialize(creds, project=project)
            else:
                ee.Initialize(creds)
        except Exception as e:
            # 异常文本只含 Google 返回的信息, 不含私钥本体
            return ExecutionResult(success=False, error=f"GEE 初始化失败: {e}")

        fd, tmp_code = tempfile.mkstemp(suffix=".py", prefix="gee_run_")
        out_jpeg = Path(tempfile.gettempdir()) / f"{os.path.basename(tmp_code)}.jpg"
        ns = {
            "ee": ee,
            # GAUL 区县模式 region 为 None: 代码按 PLACE 从 GAUL level2 动态取 roi
            "REGION": dict(region) if region else None,
            "PLACE": str(kwargs.get("place") or ""),
            "TASK": kwargs.get("task", ""),
            # 沙箱试跑标志: 生成代码据此缩小出图网格快速验证 (见 GENERATOR 契约)
            "SANDBOX": bool(kwargs.get("_sandbox")),
            # O1 下载挡位: standard(<1MB) | high(1-10MB) | max(>10MB)
            "QUALITY_TIER": str(kwargs.get("quality") or "standard"),
            "OUTPUT_JPEG": str(out_jpeg),
            "METRICS": {},
        }

        def _run() -> None:
            exec(compile(code, "<gee-generated>", "exec"), ns)  # noqa: S102 - D=1 契约

        # max 挡高清大图放宽超时 (沙箱试跑仍用短超时快速失败)
        timeout = (_EXECUTE_TIMEOUT_MAX_S
                   if str(kwargs.get("quality") or "") == "max"
                   and not kwargs.get("_sandbox") else _EXECUTE_TIMEOUT_S)

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_run)
                fut.result(timeout=timeout)
        except FutTimeout:
            return ExecutionResult(success=False, error=f"云端计算超时 (>{timeout}s), 已放弃本次结果")
        except Exception as e:
            return ExecutionResult(success=False, error=f"代码执行失败: {type(e).__name__}: {e}")

        produced = ns.get("OUTPUT_JPEG")
        metrics = ns.get("METRICS") or {}
        if not produced or not Path(produced).exists():
            return ExecutionResult(
                success=False,
                error="代码没有产出 OUTPUT_JPEG (契约: 结果图路径必须写入该变量)",
            )
        with open(produced, "rb") as f:
            if f.read(2) != _JPEG_MAGIC:
                return ExecutionResult(success=False, error="产物不是有效 JPEG (魔数校验失败)")

        # 指标字段规整为锚点层熟悉的键 (不删代码给的额外键, 防护只增不减)
        return ExecutionResult(success=True, output_path=str(produced), metrics=dict(metrics))

    def test_connection(self, credentials: dict) -> bool:
        """轻量校验: 字段齐全且 key_json 可解析 (不出网, 真连通性在 execute 时验证)。"""
        email = credentials.get("service_account_email")
        if not email:
            return False
        key_json = credentials.get("key_json")
        key_path = credentials.get("key_path")
        if key_json:
            try:
                json.loads(key_json) if isinstance(key_json, str) else key_json
                return True
            except json.JSONDecodeError:
                return False
        return bool(key_path and Path(key_path).exists())
