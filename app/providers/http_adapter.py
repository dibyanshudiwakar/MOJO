"""OpenAI-compatible HTTP provider adapter.

Implements the ProviderExecutionAdapter seam so ReasoningGateway can reach any
endpoint that speaks the OpenAI chat-completions protocol: Ollama, vLLM, a
LiteLLM proxy, or OpenAI itself.  The adapter is instantiated with the three
values that vary per deployment (api_base, api_key, model) and is passed into
AgentRegistry.dispatch() as the provider= argument.  No credentials are stored
in agent configuration; the caller is responsible for sourcing them securely.
"""
import json
import urllib.request
import urllib.error
from app.agents.reasoning import ProviderExecutionAdapter


class HttpProviderAdapter(ProviderExecutionAdapter):
    """Call any OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(self, api_base: str, api_key: str, model: str, timeout: int = 60):
        self.url = api_base.rstrip('/') + '/chat/completions'
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def invoke(self, template: str, payload: dict) -> dict:
        """Send template + payload to the model and return a JSON-serialisable answer.

        template  -- the approved prompt text from prompt_registry
        payload   -- the agent's input evidence dict, already DLP-cleared

        Returns a dict.  If the model responds with valid JSON that is a dict,
        it is returned as-is (agents expect structured output).  A non-JSON or
        non-dict response is wrapped under 'answer' so callers always receive a
        dict and ReasoningGateway.complete_request() can hash it.
        """
        messages = [
            {'role': 'system', 'content': template},
            {'role': 'user',   'content': json.dumps(payload, sort_keys=True, default=str)},
        ]
        body = json.dumps({
            'model':       self.model,
            'messages':    messages,
            'temperature': 0,
        }).encode('utf-8')

        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                'Content-Type':  'application/json',
                'Authorization': 'Bearer ' + self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            raise RuntimeError('provider_http_error:%d' % exc.code) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError('provider_unreachable:' + str(exc.reason)) from exc

        content = data['choices'][0]['message']['content']
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {'answer': content}
