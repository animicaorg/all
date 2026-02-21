from dataclasses import dataclass


@dataclass
class {{EVENT_NAME}}:
    sender: str
    recipient: str
    amount: int


class Contract:
    def emit_event(self, sender: str, recipient: str, amount: int) -> dict:
        evt = {{EVENT_NAME}}(sender=sender, recipient=recipient, amount=amount)
        return {"event": "{{EVENT_NAME}}", "data": evt.__dict__}
