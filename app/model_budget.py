"""Safe budget errors and bounded text-request settings; no remote token counting."""
import json


class ModelBudgetError(RuntimeError):
    code = 'budget_unavailable'
    message = '模型费用预算暂不可用，请联系管理员'

    def __init__(self):
        super().__init__(self.code)


class ModelBudgetExceeded(ModelBudgetError):
    code = 'budget_exceeded'
    message = '模型费用预算或本次会话调用次数已达上限，请联系管理员'


def bounded_settings(request, output_limit):
    """Restrict the supported pricing surface, not a tokenizer approximation.

    Input reservation uses an explicitly approved supplier/model contract ceiling.
    That contract still needs independent supplier acceptance before production.
    """
    if request is None:
        raise ModelBudgetError()
    messages, settings, parameters = request[:3]
    if len(request)>3 and request[3].get('thinking_always_enabled',False):
        raise ModelBudgetError()
    settings = dict(settings or {})
    allowed = {'max_tokens', 'temperature', 'top_p', 'timeout', 'seed',
               'stop_sequences', 'parallel_tool_calls'}
    if set(settings) - allowed:
        raise ModelBudgetError()
    if (parameters.native_tools or parameters.allow_image_output or parameters.thinking
        or parameters.output_mode != 'text' or parameters.output_tools or parameters.output_object is not None):
        raise ModelBudgetError()
    for message in messages:
        for part in message.parts:
            kind = getattr(part, 'part_kind', '')
            if kind not in {'system-prompt','user-prompt','tool-return','retry-prompt','text','tool-call'}:
                raise ModelBudgetError()
            content = getattr(part, 'content', None)
            if kind in {'system-prompt','user-prompt','text'} and not isinstance(content,str):
                raise ModelBudgetError()
            if kind in {'tool-return','retry-prompt'}:
                try:
                    json.dumps(content, allow_nan=False)
                except (TypeError,ValueError):
                    raise ModelBudgetError() from None
    cap = settings.get('max_tokens',output_limit)
    if type(cap) is not int or cap <= 0:
        raise ModelBudgetError()
    settings['max_tokens'] = min(cap,output_limit)
    return settings
