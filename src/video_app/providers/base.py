"""Shared local-cancellation contract; never implies external cancellation."""


class TextTransport:
    def __init__(self):
        self.active_requests = {}

    async def generate(self, request, emit):
        self.active_requests[request.run_id] = request.cancel
        try:
            return await self._generate(request, emit)
        finally:
            self.active_requests.pop(request.run_id, None)

    async def cancel(self, run_id):
        event = self.active_requests.get(run_id)
        if event:
            event.set()
        return {"run_id": run_id, "local_cancellation_requested": event is not None,
                "external_cancel_confirmed": False}
