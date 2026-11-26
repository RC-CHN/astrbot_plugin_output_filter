import re
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain

_HOOK_ATTR = "_output_filter_send_message_hook"
_PREV_FN_ATTR = "_output_filter_prev_send_message"


@register(
    "output_filter",
    "RC-CHN",
    "一个根据正则表达式过滤机器人输出内容的插件",
    "1.1.0",
)
class OutputFilter(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config or {}
        self.enable_output_filter = self.config.get("enable_output_filter", True)
        self.filter_pattern = self.config.get("filter_pattern", "")
        self._compiled_pattern: str | None = None
        self._compiled_regex: re.Pattern[str] | None = None
        self._wrapped_send_message: Any = None
        self._prev_instance_send_message: Any = None
        self._install_send_message_hook()

    def _get_regex(self) -> re.Pattern[str] | None:
        enabled = self.config.get("enable_output_filter", self.enable_output_filter)
        pattern = self.config.get("filter_pattern", self.filter_pattern)
        if not enabled or not pattern:
            return None

        if pattern != self._compiled_pattern:
            try:
                self._compiled_regex = re.compile(pattern)
                self._compiled_pattern = pattern
            except re.error as e:
                logger.error(f"OutputFilter: 正则表达式 '{pattern}' 无效: {e}")
                self._compiled_regex = None
                self._compiled_pattern = None
                return None

        return self._compiled_regex

    @staticmethod
    def _clean_text(original_text: str, regex: re.Pattern[str]) -> str:
        modified_text = regex.sub("", original_text)
        if modified_text != original_text:
            if not original_text.startswith(("\r", "\n")):
                modified_text = modified_text.lstrip("\r\n")
            if not original_text.endswith(("\r", "\n")):
                modified_text = modified_text.rstrip("\r\n")
        return modified_text

    def _filter_chain(self, chain: list[Any], *, source: str = "reply") -> bool:
        regex = self._get_regex()
        if regex is None or not chain:
            return False

        changed = False
        new_chain: list[Any] = []
        idx = 0
        total = len(chain)

        while idx < total:
            comp = chain[idx]
            if not isinstance(comp, Plain):
                new_chain.append(comp)
                idx += 1
                continue

            # 收集严格相邻的 Plain 组件，防止匹配块跨多个 Plain 片段导致漏滤
            end = idx + 1
            while end < total and isinstance(chain[end], Plain):
                end += 1

            plain_group = chain[idx:end]
            idx = end

            if len(plain_group) == 1:
                original_text = getattr(comp, "text", None)
                if not isinstance(original_text, str):
                    new_chain.append(comp)
                    continue
                modified_text = self._clean_text(original_text, regex)
                if original_text != modified_text:
                    changed = True
                    logger.info(f"OutputFilter[{source}]: 过滤前: {original_text}")
                    logger.info(f"OutputFilter[{source}]: 过滤后: {modified_text}")
                    if original_text.strip() and not modified_text.strip():
                        continue
                    comp.text = modified_text
                new_chain.append(comp)
                continue

            # 多个相邻 Plain：若拼接后能命中正则，则合并过滤；否则保持原样不动
            texts = [
                getattr(c, "text", "") if isinstance(getattr(c, "text", None), str) else ""
                for c in plain_group
            ]
            merged_text = "".join(texts)
            cleaned_merged = self._clean_text(merged_text, regex)
            if cleaned_merged != merged_text:
                changed = True
                logger.info(f"OutputFilter[{source}]: 过滤前: {merged_text}")
                logger.info(f"OutputFilter[{source}]: 过滤后: {cleaned_merged}")
                if not (merged_text.strip() and not cleaned_merged.strip()):
                    first_plain = plain_group[0]
                    first_plain.text = cleaned_merged
                    new_chain.append(first_plain)
            else:
                new_chain.extend(plain_group)

        if changed:
            chain[:] = new_chain
        return changed

    def _install_send_message_hook(self) -> None:
        """在 context 实例上拦截 send_message，覆盖 send_message_to_user / 后台任务 / 定时任务等绕过 on_decorating_result 的路径。

        仅在实例属性上包装并动态委托给类方法，避免与修改 Context.send_message 类方法的其他插件冲突。
        """
        ctx = self.context
        if ctx is None or not hasattr(ctx, "send_message"):
            return

        existing_instance_fn = ctx.__dict__.get("send_message")
        if existing_instance_fn is not None and getattr(
            existing_instance_fn, _HOOK_ATTR, False
        ):
            prev_instance_fn = getattr(existing_instance_fn, _PREV_FN_ATTR, None)
        else:
            prev_instance_fn = existing_instance_fn

        self._prev_instance_send_message = prev_instance_fn

        async def _wrapped_send_message(*args: Any, **kwargs: Any) -> Any:
            if self.config.get("filter_proactive_messages", True):
                message_chain = (
                    args[1] if len(args) >= 2 else kwargs.get("message_chain")
                )
                chain = getattr(message_chain, "chain", None)
                if isinstance(chain, list) and chain:
                    had_components = True
                    changed = self._filter_chain(chain, source="send_message")
                    if changed and had_components and not chain:
                        logger.info(
                            "OutputFilter[send_message]: 过滤后消息为空，已跳过发送。"
                        )
                        return True

            if prev_instance_fn is not None:
                return await prev_instance_fn(*args, **kwargs)
            return await type(ctx).send_message(ctx, *args, **kwargs)

        setattr(_wrapped_send_message, _HOOK_ATTR, True)
        setattr(_wrapped_send_message, _PREV_FN_ATTR, prev_instance_fn)
        self._wrapped_send_message = _wrapped_send_message
        ctx.send_message = _wrapped_send_message  # type: ignore[method-assign]

    def _uninstall_send_message_hook(self) -> None:
        ctx = self.context
        if ctx is None or self._wrapped_send_message is None:
            return
        if ctx.__dict__.get("send_message") is self._wrapped_send_message:
            if self._prev_instance_send_message is not None:
                ctx.send_message = self._prev_instance_send_message  # type: ignore[method-assign]
            else:
                ctx.__dict__.pop("send_message", None)
        self._wrapped_send_message = None
        self._prev_instance_send_message = None

    async def terminate(self) -> None:
        self._uninstall_send_message_hook()

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not isinstance(getattr(result, "chain", None), list):
            return
        self._filter_chain(result.chain, source="decorating_result")
