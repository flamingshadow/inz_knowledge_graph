"""A scripted stand-in for the Anthropic client, so the assistant's guards can be tested."""

from types import SimpleNamespace as NS


def tool(name, **inp):
    return NS(type="tool_use", id=f"tu_{name}_{id(inp)}", name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


class FakeClient:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        content = self.turns.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(content=content, stop_reason=stop, model="fake",
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0))
