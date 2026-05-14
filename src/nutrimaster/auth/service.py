"""
后端认证模块 —— FastAPI Depends 版
使用 supabase-py 验证前端传来的 access_token
"""
import os
import asyncio
import logging
import random
import threading
import hashlib
import hmac
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from supabase import create_client
from nutrimaster.auth.email import send_verification_code
from nutrimaster.config.settings import Settings

logger = logging.getLogger(__name__)

_SETTINGS = Settings.from_env()
_CODE_SALT = os.getenv("AUTH_CODE_SALT") or _SETTINGS.supabase_service_role_key
_VERIFY_ATTEMPT_LOCK = threading.Lock()
_VERIFY_ATTEMPTS: dict[str, dict] = {}
_MAX_VERIFY_ATTEMPTS = 5
_VERIFY_LOCK_MINUTES = 15

# ===== 初始化 Supabase 客户端（用 service_role_key 以便验证任意用户 token） =====
supabase = create_client(_SETTINGS.supabase_url, _SETTINGS.supabase_service_role_key)

# ===== 管理员邮箱列表（逗号分隔，从环境变量读取） =====
_admin_raw = os.getenv("ADMIN_EMAILS") or os.getenv("ADMIN_EMAIL") or ""
ADMIN_EMAILS = set(
    e.strip().lower()
    for e in _admin_raw.split(",")
    if e.strip()
)
logger.info("ADMIN_EMAILS loaded: %d entries", len(ADMIN_EMAILS))


def _as_bool_env(name: str) -> bool:
    """
    将环境变量的值解析为布尔值。

    支持的真值字符串（不区分大小写）: "1", "true", "yes", "on"。
    如果环境变量不存在或值不在上述列表中，则返回 False。

    参数:
        name (str): 环境变量名称。

    返回:
        bool: 环境变量对应的布尔值。
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _dev_auth_user():
    """
    构造一个用于开发环境的模拟用户对象。

    当开发模式认证绕过（NUTRIMASTER_DEV_AUTH_BYPASS）开启时，
    此函数会从环境变量中读取开发用户的邮箱、昵称和用户 ID，
    返回一个 SimpleNamespace 对象来模拟真实的 Supabase 用户。

    返回:
        SimpleNamespace: 包含 id、email、user_metadata 属性的模拟用户对象。
            - id: 用户 ID，默认为 "dev-user"
            - email: 用户邮箱，默认为 "dev@example.com"
            - user_metadata: 包含 nickname 和 avatar_url 的字典
    """
    email = os.getenv("NUTRIMASTER_DEV_AUTH_EMAIL", "dev@example.com")
    nickname = os.getenv("NUTRIMASTER_DEV_AUTH_NICKNAME") or email.split("@")[0] or "dev"
    return SimpleNamespace(
        id=os.getenv("NUTRIMASTER_DEV_AUTH_USER_ID", "dev-user"),
        email=email,
        user_metadata={"nickname": nickname, "avatar_url": None},
    )


async def get_current_user(request: Request):
    """
    FastAPI 依赖注入函数：从请求的 Authorization 头中提取并验证 Supabase 访问令牌。

    该函数作为 FastAPI 的 Depends 依赖使用，用于在需要认证的路由中获取当前登录用户。
    如果启用了开发模式绕过（NUTRIMASTER_DEV_AUTH_BYPASS），则返回模拟的开发用户。
    否则从请求头中提取 Bearer token，并通过 Supabase 后端验证其有效性。

    参数:
        request (Request): FastAPI 的请求对象，包含 HTTP 头信息。

    返回:
        用户对象: Supabase 返回的用户对象（包含 id、email、user_metadata 等属性），
                  或在开发模式下返回 SimpleNamespace 模拟用户对象。

    异常:
        HTTPException(401): 未提供 token 或 token 验证失败时抛出。
    """
    if _as_bool_env("NUTRIMASTER_DEV_AUTH_BYPASS"):
        logger.warning("NUTRIMASTER_DEV_AUTH_BYPASS is enabled; Supabase auth is bypassed")
        return _dev_auth_user()

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""

    if not token:
        raise HTTPException(status_code=401, detail="未登录，请先登录")

    try:
        user_response = await asyncio.to_thread(supabase.auth.get_user, token)
        return user_response.user
    except Exception:
        raise HTTPException(status_code=401, detail="认证失败，请重新登录")


async def user_profile_view(user):
    """
    获取并返回当前登录用户的个人资料信息。

    从用户对象中提取邮箱、昵称、头像 URL 等信息，并判断该用户是否为管理员。
    如果用户没有设置昵称，则使用邮箱 @ 前的部分作为默认昵称。

    参数:
        user: Supabase 用户对象或模拟用户对象，需包含 email 和 user_metadata 属性。

    返回:
        JSONResponse: 包含以下字段的 JSON 响应：
            - email (str): 用户邮箱
            - is_admin (bool): 是否为管理员
            - nickname (str): 用户昵称
            - avatar_url (str | None): 用户头像 URL
    """
    email = (user.email or "").lower()
    meta = getattr(user, "user_metadata", None) or {}

    nickname = meta.get("nickname") or email.split("@")[0] or "User"
    avatar_url = meta.get("avatar_url")
    is_admin = email in ADMIN_EMAILS

    return JSONResponse({
        "email": user.email,
        "is_admin": is_admin,
        "nickname": nickname,
        "avatar_url": avatar_url,
    })


async def update_profile_view(request: Request, user):
    """
    更新当前登录用户的个人资料（昵称和/或头像）。

    从请求体中获取要更新的字段（nickname、avatar_url），
    合并到用户现有的 user_metadata 中，然后通过 Supabase Admin API 保存更新。
    至少需要提供 nickname 或 avatar_url 中的一个字段。

    参数:
        request (Request): FastAPI 请求对象，请求体应为 JSON 格式，
                           可包含 "nickname" (str) 和/或 "avatar_url" (str) 字段。
        user: 当前登录的用户对象，需包含 id 和 user_metadata 属性。

    返回:
        JSONResponse: 包含更新结果的 JSON 响应：
            - status (str): 操作状态，成功为 "ok"
            - nickname (str): 更新后的昵称
            - avatar_url (str | None): 更新后的头像 URL

    异常:
        HTTPException(400): 未提供任何需要更新的字段时抛出。
        HTTPException(500): Supabase 更新操作失败时抛出。
    """
    data = await request.json()

    nickname = data.get("nickname")
    avatar_url = data.get("avatar_url")

    if nickname is None and avatar_url is None:
        raise HTTPException(status_code=400, detail="至少提供 nickname 或 avatar_url")

    meta = getattr(user, "user_metadata", None) or {}
    new_meta = dict(meta)
    if nickname is not None:
        new_meta["nickname"] = nickname
    if avatar_url is not None:
        new_meta["avatar_url"] = avatar_url

    try:
        await asyncio.to_thread(
            supabase.auth.admin.update_user_by_id,
            user.id,
            {"user_metadata": new_meta},
        )
    except Exception as e:
        logger.error("更新用户 profile 失败: %s", e)
        raise HTTPException(status_code=500, detail="更新失败")

    return JSONResponse({
        "status": "ok",
        "nickname": new_meta.get("nickname", ""),
        "avatar_url": new_meta.get("avatar_url"),
    })


async def delete_account_view(user):
    """
    删除当前登录用户的账号。

    通过 Supabase Admin API 永久删除指定用户。此操作不可逆。

    参数:
        user: 当前登录的用户对象，需包含 id 属性。

    返回:
        JSONResponse: 包含操作状态的 JSON 响应：
            - status (str): 操作状态，成功为 "ok"

    异常:
        HTTPException(500): 删除操作失败时抛出。
    """
    try:
        await asyncio.to_thread(supabase.auth.admin.delete_user, user.id)
    except Exception as e:
        logger.error("删除用户失败: %s", e)
        raise HTTPException(status_code=500, detail="删除失败")

    return JSONResponse({"status": "ok"})


def generate_verification_code() -> str:
    """
    生成一个随机的 6 位数字验证码。

    使用 random 模块生成 0 到 999999 之间的随机整数，
    并格式化为 6 位字符串（不足 6 位时前面补零）。

    返回:
        str: 6 位数字验证码字符串，例如 "042857"。
    """
    return f"{random.randint(0, 999999):06d}"


def _hash_verification_code(email: str, code: str) -> str:
    """
    对验证码进行带邮箱作用域的 HMAC-SHA256 哈希运算。

    将邮箱（转小写）和验证码拼接后，使用系统配置的盐值（AUTH_CODE_SALT
    或 Supabase service_role_key）进行 HMAC 哈希，避免验证码以明文形式
    存储在数据库中，同时防止跨邮箱的哈希碰撞。

    参数:
        email (str): 用户邮箱地址，会被转为小写后参与哈希计算。
        code (str): 6 位数字验证码字符串。

    返回:
        str: 十六进制格式的 HMAC-SHA256 哈希值。
    """
    payload = f"{email.lower()}:{code}".encode("utf-8")
    return hmac.new(_CODE_SALT.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _public_auth_message() -> str:
    """
    返回统一的认证提示信息。

    无论邮箱是否已注册、是否已验证，都返回相同的提示文案，
    以避免通过接口响应泄露用户账号的存在状态（防止用户枚举攻击）。

    返回:
        str: 统一的提示信息字符串。
    """
    return "如果邮箱可用，验证码已发送，请查收邮箱"


def _cleanup_verify_attempts(now: datetime) -> None:
    """
    清理已过期的验证码尝试记录。

    遍历全局的验证尝试字典 _VERIFY_ATTEMPTS，移除所有锁定时间已过期的记录，
    释放内存并允许被锁定的邮箱重新尝试验证。

    参数:
        now (datetime): 当前 UTC 时间，用于判断锁定是否已过期。
    """
    expired = [
        email for email, state in _VERIFY_ATTEMPTS.items()
        if state.get("lock_until") and state["lock_until"] <= now
    ]
    for email in expired:
        _VERIFY_ATTEMPTS.pop(email, None)


def _ensure_not_locked(email: str) -> None:
    """
    检查指定邮箱是否因验证码尝试次数过多而被锁定。

    在验证码校验之前调用此函数，若该邮箱当前处于锁定状态
    （连续失败次数达到上限后的冷却期内），则直接抛出 429 异常。
    同时会清理已过期的锁定记录。

    参数:
        email (str): 需要检查锁定状态的用户邮箱地址。

    异常:
        HTTPException(429): 邮箱处于锁定期时抛出，提示用户稍后再试。
    """
    now = datetime.utcnow()
    with _VERIFY_ATTEMPT_LOCK:
        _cleanup_verify_attempts(now)
        state = _VERIFY_ATTEMPTS.get(email)
        lock_until = state.get("lock_until") if state else None
        if lock_until and lock_until > now:
            raise HTTPException(status_code=429, detail="验证码尝试次数过多，请稍后再试")


def _record_verify_failure(email: str) -> None:
    """
    记录一次验证码校验失败，并在达到上限时锁定该邮箱。

    每次验证码校验失败时调用此函数，将对应邮箱的失败计数加 1。
    当失败次数达到 _MAX_VERIFY_ATTEMPTS（默认 5 次）时，
    设置锁定截止时间为当前时间加 _VERIFY_LOCK_MINUTES（默认 15 分钟）。

    参数:
        email (str): 验证失败的用户邮箱地址。
    """
    now = datetime.utcnow()
    with _VERIFY_ATTEMPT_LOCK:
        _cleanup_verify_attempts(now)
        state = _VERIFY_ATTEMPTS.setdefault(email, {"count": 0, "lock_until": None})
        state["count"] += 1
        if state["count"] >= _MAX_VERIFY_ATTEMPTS:
            state["lock_until"] = now + timedelta(minutes=_VERIFY_LOCK_MINUTES)


def _clear_verify_failures(email: str) -> None:
    """
    清除指定邮箱的所有验证码失败记录。

    在验证码校验成功后调用此函数，将该邮箱从失败记录字典中移除，
    重置其失败计数和锁定状态。

    参数:
        email (str): 需要清除失败记录的用户邮箱地址。
    """
    with _VERIFY_ATTEMPT_LOCK:
        _VERIFY_ATTEMPTS.pop(email, None)


async def signup_with_email(request: Request):
    """
    处理用户邮箱注册请求。

    完整的注册流程如下：
    1. 从请求体中解析邮箱、密码、昵称（可选）、头像 URL（可选）
    2. 校验邮箱和密码的有效性（非空、密码至少 6 位）
    3. 生成 6 位随机验证码，并对其进行 HMAC 哈希
    4. 查找是否已有同邮箱用户：
       - 若用户已存在且已验证邮箱：静默返回统一提示（防止用户枚举）
       - 若用户已存在但未验证：更新验证码信息，不覆盖原密码
       - 若为新用户：通过 Supabase Admin API 创建（未验证状态）
    5. 通过 163 SMTP 发送包含验证码的 HTML 邮件

    参数:
        request (Request): FastAPI 请求对象，请求体应为 JSON 格式，包含：
            - email (str): 用户邮箱地址（必填）
            - password (str): 用户密码，至少 6 位（必填）
            - nickname (str, 可选): 用户昵称
            - avatar_url (str, 可选): 用户头像 URL

    返回:
        JSONResponse: 包含注册结果的 JSON 响应：
            - status (str): 操作状态，成功为 "ok"
            - message (str): 统一的提示信息
            - password_updated (bool): 密码是否被设置/更新

    异常:
        HTTPException(400): 邮箱或密码为空，或密码长度不足时抛出。
        HTTPException(500): 用户创建失败或验证码邮件发送失败时抛出。
    """
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    nickname = (data.get("nickname") or "").strip()
    avatar_url = data.get("avatar_url")

    if not email or not password:
        raise HTTPException(status_code=400, detail="邮箱和密码不能为空")

    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    code = generate_verification_code()
    code_hash = _hash_verification_code(email, code)
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat() + "Z"

    # 构建 user_metadata
    user_metadata = {
        "verification_code_hash": code_hash,
        "code_expires_at": expires_at,
    }
    if nickname:
        user_metadata["nickname"] = nickname
    if avatar_url:
        user_metadata["avatar_url"] = avatar_url

    try:
        # 先尝试查找已有用户（可能之前注册过但未验证）
        users_resp = await asyncio.to_thread(
            supabase.auth.admin.list_users,
        )
        existing_user = None
        for u in users_resp:
            if hasattr(u, 'email') and u.email and u.email.lower() == email:
                existing_user = u
                break

        if existing_user:
            # 用户已存在
            if existing_user.email_confirmed_at:
                logger.info("Signup requested for existing confirmed email: %s", email)
            else:
                # 未验证 → 只更新验证码，不覆盖原密码，避免未验证账号被他人接管
                await asyncio.to_thread(
                    supabase.auth.admin.update_user_by_id,
                    existing_user.id,
                    {
                        "user_metadata": {
                            **(getattr(existing_user, 'user_metadata', None) or {}),
                            **user_metadata,
                        },
                    },
                )
                return JSONResponse({
                    "status": "ok",
                    "message": _public_auth_message(),
                    "password_updated": False,
                })
        if not existing_user:
            # 新用户 → 创建
            await asyncio.to_thread(
                supabase.auth.admin.create_user,
                {
                    "email": email,
                    "password": password,
                    "email_confirm": False,
                    "user_metadata": user_metadata,
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("创建用户失败: %s", e)
        raise HTTPException(status_code=500, detail="注册失败，请稍后再试")

    # 发送验证码邮件
    sent = await asyncio.to_thread(send_verification_code, email, code)
    if not sent:
        raise HTTPException(status_code=500, detail="验证码邮件发送失败，请稍后再试")

    return JSONResponse({
        "status": "ok",
        "message": _public_auth_message(),
        "password_updated": True,
    })


async def verify_email_code(request: Request):
    """
    处理邮箱验证码校验请求。

    完整的验证流程如下：
    1. 从请求体中解析邮箱和验证码
    2. 检查该邮箱是否因多次失败而被锁定
    3. 通过 Supabase Admin API 查找对应用户
    4. 从用户的 user_metadata 中取出哈希后的验证码和过期时间
    5. 使用 HMAC 安全比较验证码是否匹配
    6. 检查验证码是否已过期（有效期 10 分钟）
    7. 验证成功后：确认邮箱、清除验证码信息、重置失败计数
    8. 验证失败时：记录失败次数，达到上限后锁定该邮箱

    参数:
        request (Request): FastAPI 请求对象，请求体应为 JSON 格式，包含：
            - email (str): 用户邮箱地址（必填）
            - code (str): 6 位数字验证码（必填）

    返回:
        JSONResponse: 包含验证结果的 JSON 响应：
            - status (str): 操作状态，成功为 "ok"
            - message (str): 成功时为 "邮箱验证成功"

    异常:
        HTTPException(400): 参数缺失或验证码错误/已失效时抛出。
        HTTPException(429): 验证码尝试次数超限，邮箱被锁定时抛出。
        HTTPException(500): 服务端验证流程异常时抛出。
    """
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()

    if not email or not code:
        raise HTTPException(status_code=400, detail="邮箱和验证码不能为空")

    _ensure_not_locked(email)

    try:
        # 查找用户
        users_resp = await asyncio.to_thread(
            supabase.auth.admin.list_users,
        )
        target_user = None
        for u in users_resp:
            if hasattr(u, 'email') and u.email and u.email.lower() == email:
                target_user = u
                break

        if not target_user:
            _record_verify_failure(email)
            raise HTTPException(status_code=400, detail="验证码错误或已失效")

        meta = getattr(target_user, 'user_metadata', None) or {}
        saved_code_hash = meta.get("verification_code_hash")
        expires_at_str = meta.get("code_expires_at")

        if not saved_code_hash or not expires_at_str:
            _record_verify_failure(email)
            raise HTTPException(status_code=400, detail="验证码错误或已失效")

        # 校验验证码
        expected_hash = _hash_verification_code(email, code)
        if not hmac.compare_digest(expected_hash, saved_code_hash):
            _record_verify_failure(email)
            raise HTTPException(status_code=400, detail="验证码错误或已失效")

        # 校验过期时间
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        now = datetime.utcnow().replace(tzinfo=expires_at.tzinfo)
        if now > expires_at:
            _record_verify_failure(email)
            raise HTTPException(status_code=400, detail="验证码错误或已失效")

        # 验证成功：确认邮箱 + 清除验证码
        clean_meta = dict(meta)
        clean_meta.pop("verification_code_hash", None)
        clean_meta.pop("code_expires_at", None)

        await asyncio.to_thread(
            supabase.auth.admin.update_user_by_id,
            target_user.id,
            {
                "email_confirm": True,
                "user_metadata": clean_meta,
            },
        )
        _clear_verify_failures(email)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("验证邮箱失败: %s", e)
        raise HTTPException(status_code=500, detail="验证失败，请稍后再试")

    return JSONResponse({"status": "ok", "message": "邮箱验证成功"})


async def resend_verification_code(request: Request):
    """
    处理重新发送验证码的请求。

    完整流程如下：
    1. 从请求体中解析邮箱地址
    2. 通过 Supabase Admin API 查找对应用户
    3. 如果用户不存在或邮箱已验证，静默返回统一提示（防止用户枚举）
    4. 生成新的 6 位随机验证码，进行 HMAC 哈希
    5. 更新用户的 user_metadata 中的验证码哈希和过期时间
    6. 通过 163 SMTP 发送新的验证码邮件

    参数:
        request (Request): FastAPI 请求对象，请求体应为 JSON 格式，包含：
            - email (str): 用户邮箱地址（必填）

    返回:
        JSONResponse: 包含操作结果的 JSON 响应：
            - status (str): 操作状态，成功为 "ok"
            - message (str): 统一的提示信息

    异常:
        HTTPException(400): 邮箱为空时抛出。
        HTTPException(500): 更新验证码或发送邮件失败时抛出。
    """
    data = await request.json()
    email = (data.get("email") or "").strip().lower()

    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")

    try:
        users_resp = await asyncio.to_thread(
            supabase.auth.admin.list_users,
        )
        target_user = None
        for u in users_resp:
            if hasattr(u, 'email') and u.email and u.email.lower() == email:
                target_user = u
                break

        if not target_user:
            return JSONResponse({"status": "ok", "message": _public_auth_message()})

        if target_user.email_confirmed_at:
            return JSONResponse({"status": "ok", "message": _public_auth_message()})

        code = generate_verification_code()
        code_hash = _hash_verification_code(email, code)
        expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat() + "Z"

        meta = getattr(target_user, 'user_metadata', None) or {}
        new_meta = dict(meta)
        new_meta["verification_code_hash"] = code_hash
        new_meta["code_expires_at"] = expires_at

        await asyncio.to_thread(
            supabase.auth.admin.update_user_by_id,
            target_user.id,
            {"user_metadata": new_meta},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("重发验证码失败: %s", e)
        raise HTTPException(status_code=500, detail="发送失败，请稍后再试")

    sent = await asyncio.to_thread(send_verification_code, email, code)
    if not sent:
        raise HTTPException(status_code=500, detail="验证码邮件发送失败，请稍后再试")

    return JSONResponse({"status": "ok", "message": _public_auth_message()})
