import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.components import Plain

@register("output_filter", "RC-CHN", "一个根据正则表达式过滤机器人输出内容的插件", "1.0.0")
class OutputFilter(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.enable_output_filter = self.config.get('enable_output_filter', True)
        self.filter_pattern = self.config.get('filter_pattern', '')

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not self.enable_output_filter or not self.filter_pattern:
            return

        # 编译正则表达式以提高效率
        try:
            regex = re.compile(self.filter_pattern)
        except re.error as e:
            logger.error(f"OutputFilter: 正则表达式 '{self.filter_pattern}' 无效: {e}")
            return

        # 遍历消息链中的每个组件
        for component in result.chain:
            if isinstance(component, Plain):
                original_text = component.text
                # 使用 re.sub 删除匹配的部分
                modified_text = regex.sub('', original_text)
                if original_text != modified_text:
                    logger.info(f"OutputFilter: 过滤前: {original_text}")
                    logger.info(f"OutputFilter: 过滤后: {modified_text}")
                    component.text = modified_text
