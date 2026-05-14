from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from nutrimaster.auth.service import (
    delete_account_view,
    get_current_user,
    resend_verification_code,
    signup_with_email,
    update_profile_view,
    user_profile_view,
    verify_email_code,
)

router = APIRouter()


@router.post("/api/auth/signup")
async def auth_signup(request: Request):
    """处理用户邮箱注册请求。

    解析请求体中的邮箱地址，创建新用户账号并发送邮箱验证码。
    委托给 auth.service.signup_with_email 执行具体逻辑。

    参数:
        request: FastAPI 请求对象，请求体应包含注册所需的邮箱等信息。

    返回:
        JSONResponse: 注册结果响应。
    """
    return await signup_with_email(request)


@router.post("/api/auth/verify")
async def auth_verify(request: Request):
    """验证邮箱验证码以完成用户注册流程。

    解析请求体中的验证码，校验后激活用户账号。
    委托给 auth.service.verify_email_code 执行具体逻辑。

    参数:
        request: FastAPI 请求对象，请求体应包含邮箱和验证码。

    返回:
        JSONResponse: 验证结果响应。
    """
    return await verify_email_code(request)


@router.post("/api/auth/resend")
async def auth_resend(request: Request):
    """重新发送邮箱验证码。

    当用户未收到验证码或验证码过期时，可调用此接口重新发送。
    委托给 auth.service.resend_verification_code 执行具体逻辑。

    参数:
        request: FastAPI 请求对象，请求体应包含目标邮箱地址。

    返回:
        JSONResponse: 重发结果响应。
    """
    return await resend_verification_code(request)


@router.get("/api/user/profile")
async def user_profile(user=Depends(get_current_user)):
    """获取当前登录用户的个人资料信息。

    需要用户登录认证。返回用户的基本信息和配置。

    参数:
        user: 当前登录用户对象（通过依赖注入获取）。

    返回:
        JSONResponse: 包含用户个人资料的 JSON 响应。
    """
    return await user_profile_view(user=user)


@router.put("/api/user/profile")
async def user_profile_update(request: Request, user=Depends(get_current_user)):
    """更新当前登录用户的个人资料信息。

    解析请求体中的更新字段并保存。需要用户登录认证。

    参数:
        request: FastAPI 请求对象，请求体应包含要更新的资料字段。
        user: 当前登录用户对象（通过依赖注入获取）。

    返回:
        JSONResponse: 更新结果响应。
    """
    return await update_profile_view(request=request, user=user)


@router.delete("/api/user/account")
async def user_account_delete(user=Depends(get_current_user)):
    """删除当前登录用户的账号及所有关联数据。

    此操作不可逆，将永久删除用户账号。需要用户登录认证。

    参数:
        user: 当前登录用户对象（通过依赖注入获取）。

    返回:
        JSONResponse: 删除结果响应。
    """
    return await delete_account_view(user=user)
