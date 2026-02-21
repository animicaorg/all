class {{CONTRACT_NAME}}:
    def __init__(self) -> None:
        self.total: int = 0
        self.enabled: bool = False
        self.payload: bytes = b""

    def create(self, amount: int, enabled: bool, payload: bytes) -> None:
        self.total = amount
        self.enabled = enabled
        self.payload = payload

    def read(self) -> tuple[int, bool, bytes]:
        return self.total, self.enabled, self.payload

    def update(self, amount: int | None = None, enabled: bool | None = None, payload: bytes | None = None) -> None:
        if amount is not None:
            self.total = amount
        if enabled is not None:
            self.enabled = enabled
        if payload is not None:
            self.payload = payload

    def delete(self) -> None:
        self.total = 0
        self.enabled = False
        self.payload = b""
