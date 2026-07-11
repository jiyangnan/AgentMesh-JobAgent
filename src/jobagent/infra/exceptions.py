"""Custom exceptions for Job Agent infrastructure."""


class LoginRequiredError(RuntimeError):
    """Raised when Boss login session is expired or missing,
    causing API responses to omit critical fields like salary.

    In normal CLI usage the system auto-guides login via Chrome.
    This error is a fallback for edge cases (timeout, non-CDP driver, etc.).
    """

    def __init__(self, message: str | None = None):
        default = (
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  ⚠️  Boss 登录态已失效                                       ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║  系统将自动弹出 Chrome 引导登录，若未弹出请检查：           ║\n"
            "║    1. Google Chrome 是否已安装                               ║\n"
            "║    2. 网络连接是否正常                                       ║\n"
            "║    3. 重新运行命令                                           ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n"
        )
        super().__init__(message or default)


class UserActionRequiredError(RuntimeError):
    """Raised when a platform needs a visible user action before continuing."""

    def __init__(self, code: str, message: str, user_prompt: str):
        self.code = code
        self.user_prompt = user_prompt
        super().__init__(message)
