"""Error types shared by every interface (REST, MCP, WebSocket, CLI)."""


class BridgeError(Exception):
    code = "error"
    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message}


class ValidationFailed(BridgeError):
    code = "invalid_input"
    status = 422


class AuthFailed(BridgeError):
    code = "unauthorized"
    status = 401


class PolicyDenied(BridgeError):
    code = "policy_denied"
    status = 403


class NotFound(BridgeError):
    code = "not_found"
    status = 404


class Conflict(BridgeError):
    code = "conflict"
    status = 409


class RateLimited(BridgeError):
    code = "rate_limited"
    status = 429


class RemoteError(BridgeError):
    code = "remote_error"
    status = 502
