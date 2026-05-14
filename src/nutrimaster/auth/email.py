"""
邮件发送模块 - 使用 163 SMTP 发送验证码邮件
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

from nutrimaster.config.settings import Settings

logger = logging.getLogger(__name__)


def send_verification_code(to_email: str, code: str) -> bool:
    """
    通过 163 SMTP 服务器发送包含验证码的 HTML 格式邮件到指定邮箱。

    该函数会从环境配置中读取 SMTP 服务器信息（主机、端口、用户名、密码），
    构建一封包含品牌样式的 HTML 邮件，将 6 位数字验证码嵌入邮件正文，
    然后通过 SSL 加密连接发送到收件人邮箱。

    参数:
        to_email (str): 收件人邮箱地址，用于接收验证码。
        code (str): 6 位数字验证码字符串，将展示在邮件正文中。

    返回:
        bool: 邮件发送成功返回 True；发送过程中出现任何异常则返回 False，
              并在日志中记录错误详情。
    """
    try:
        settings = Settings.from_env()
        # 创建邮件对象
        msg = MIMEMultipart('alternative')
        msg['From'] = Header(f"{settings.smtp_from_name} <{settings.smtp_user}>")
        msg['To'] = to_email
        msg['Subject'] = Header("NutriMaster 邮箱验证码", 'utf-8')

        # HTML 邮件内容
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .container {{
            background: #ffffff;
            border-radius: 8px;
            padding: 40px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .logo {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .logo-text {{
            font-size: 28px;
            font-weight: 600;
        }}
        .logo-nutri {{
            color: #10b981;
        }}
        .logo-master {{
            color: #3b82f6;
        }}
        .code-box {{
            background: #f3f4f6;
            border: 2px dashed #d1d5db;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
            margin: 30px 0;
        }}
        .code {{
            font-size: 36px;
            font-weight: bold;
            letter-spacing: 8px;
            color: #1f2937;
            font-family: 'Courier New', monospace;
        }}
        .info {{
            color: #6b7280;
            font-size: 14px;
            text-align: center;
            margin-top: 20px;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e5e7eb;
            color: #9ca3af;
            font-size: 12px;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <div class="logo-text">
                <span class="logo-nutri">Nutri</span><span class="logo-master">Master</span>
            </div>
        </div>

        <p>您好，</p>
        <p>感谢您注册 NutriMaster 智能植物营养基因问答平台。请使用以下验证码完成邮箱验证：</p>

        <div class="code-box">
            <div class="code">{code}</div>
        </div>

        <div class="info">
            <p>验证码有效期为 <strong>10 分钟</strong></p>
            <p>如果这不是您本人的操作，请忽略此邮件。</p>
        </div>

        <div class="footer">
            <p>此邮件由系统自动发送，请勿直接回复。</p>
            <p>&copy; 2026 NutriMaster. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""

        # 添加 HTML 内容
        html_part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(html_part)

        # 连接 163 SMTP 服务器并发送
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, [to_email], msg.as_string())

        logger.info(f"验证码邮件发送成功: {to_email}")
        return True

    except Exception as e:
        logger.error(f"验证码邮件发送失败: {to_email}, 错误: {e}")
        return False
